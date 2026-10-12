#!/usr/bin/env python3
import fcntl
import hashlib
import hmac
import os
import select
import socket
import struct
import subprocess
import time

TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
MAX_PACKET = 65535
FRAME_TAG_LEN = 16
FRAME_HDR_LEN = 6


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()


def tune_socket(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in (('TCP_KEEPIDLE', 15), ('TCP_KEEPINTVL', 5), ('TCP_KEEPCNT', 3)):
        opt = getattr(socket, name, None)
        if opt is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
            except OSError:
                pass


def create_tun(iface):
    fd = os.open('/dev/net/tun', os.O_RDWR | os.O_NONBLOCK)
    ifr = struct.pack('16sH', iface.encode()[:15], IFF_TUN | IFF_NO_PI)
    result = fcntl.ioctl(fd, TUNSETIFF, ifr)
    actual = result[:16].split(b'\x00', 1)[0].decode()
    if actual != iface:
        os.close(fd)
        raise RuntimeError(f'TUN name mismatch: expected {iface}, got {actual}')
    return fd


def configure_tun(iface, tunnel_ip, mtu):
    run(['ip', 'link', 'set', 'dev', iface, 'mtu', str(int(mtu)), 'txqueuelen', '1000'])
    run(['ip', 'addr', 'flush', 'dev', iface, 'scope', 'global'], check=False)
    run(['ip', 'addr', 'add', tunnel_ip, 'dev', iface])
    run(['ip', 'link', 'set', 'dev', iface, 'up'])
    run(['sysctl', '-q', '-w', f'net.ipv4.conf.{iface}.rp_filter=0'], check=False)


def _iptables():
    rc, out, _ = run(['sh', '-c', 'command -v iptables'], check=False)
    return out.strip() if rc == 0 else ''


def _ensure_chain(ipt, table, parent, chain):
    run([ipt, '-w', '5', '-t', table, '-N', chain], check=False)
    rc, _, _ = run([ipt, '-w', '5', '-t', table, '-C', parent, '-j', chain], check=False)
    if rc != 0:
        run([ipt, '-w', '5', '-t', table, '-I', parent, '1', '-j', chain])


def _ensure_rule(ipt, table, chain, args):
    rc, _, _ = run([ipt, '-w', '5', '-t', table, '-C', chain, *args], check=False)
    if rc != 0:
        run([ipt, '-w', '5', '-t', table, '-A', chain, *args])


def setup_tun_firewall(iface):
    ipt = _iptables()
    if not ipt:
        return
    for parent, chain in (('INPUT', 'TP27_TUN_IN'), ('OUTPUT', 'TP27_TUN_OUT'), ('FORWARD', 'TP27_TUN_FWD')):
        _ensure_chain(ipt, 'filter', parent, chain)
    _ensure_rule(ipt, 'filter', 'TP27_TUN_IN', ['-i', iface, '-j', 'ACCEPT'])
    _ensure_rule(ipt, 'filter', 'TP27_TUN_OUT', ['-o', iface, '-j', 'ACCEPT'])
    _ensure_rule(ipt, 'filter', 'TP27_TUN_FWD', ['-i', iface, '-j', 'ACCEPT'])
    _ensure_rule(ipt, 'filter', 'TP27_TUN_FWD', ['-o', iface, '-j', 'ACCEPT'])


def cleanup_tun_firewall(iface):
    ipt = _iptables()
    if not ipt:
        return
    rules = [
        ('TP27_TUN_IN', ['-i', iface, '-j', 'ACCEPT']),
        ('TP27_TUN_OUT', ['-o', iface, '-j', 'ACCEPT']),
        ('TP27_TUN_FWD', ['-i', iface, '-j', 'ACCEPT']),
        ('TP27_TUN_FWD', ['-o', iface, '-j', 'ACCEPT']),
    ]
    for chain, args in rules:
        while True:
            rc, _, _ = run([ipt, '-w', '5', '-t', 'filter', '-C', chain, *args], check=False)
            if rc != 0:
                break
            run([ipt, '-w', '5', '-t', 'filter', '-D', chain, *args], check=False)


def cleanup_tun(iface):
    cleanup_tun_firewall(iface)
    run(['ip', 'link', 'del', iface], check=False)


def stream_open_signature(key, path, ts, nonce):
    msg = f'TP27-STREAM-OPEN-V1\nGET\n{path}\n{int(ts)}\n{nonce}'.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def stream_accept_signature(key, eid, nonce):
    msg = f'TP27-STREAM-ACCEPT-V1\n{eid}\n{nonce}'.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _frame_tag(key, direction, seq, payload):
    msg = b'TP27-STREAM-FRAME-V1\n' + direction + b'\n' + struct.pack('!I', seq) + payload
    return hmac.new(key, msg, hashlib.sha256).digest()[:FRAME_TAG_LEN]


def encode_frame(key, direction, seq, payload):
    if len(payload) > MAX_PACKET:
        raise RuntimeError('packet too large')
    header = struct.pack('!HI', len(payload), seq)
    return header + payload + _frame_tag(key, direction, seq, payload)


def pump(tun_fd, sock, key, send_direction, recv_direction, initial=b''):
    tune_socket(sock)
    send_seq = 0
    recv_seq = 0
    buffer = bytearray(initial)
    last_tx = time.monotonic()

    def consume():
        nonlocal recv_seq
        while len(buffer) >= FRAME_HDR_LEN:
            length, seq = struct.unpack('!HI', buffer[:FRAME_HDR_LEN])
            if length > MAX_PACKET:
                raise RuntimeError('invalid frame length')
            total = FRAME_HDR_LEN + length + FRAME_TAG_LEN
            if len(buffer) < total:
                return
            payload = bytes(buffer[FRAME_HDR_LEN:FRAME_HDR_LEN + length])
            tag = bytes(buffer[FRAME_HDR_LEN + length:total])
            del buffer[:total]
            if seq != recv_seq:
                raise RuntimeError(f'frame sequence mismatch expected={recv_seq} got={seq}')
            expected = _frame_tag(key, recv_direction, seq, payload)
            if not hmac.compare_digest(tag, expected):
                raise RuntimeError('frame authentication failed')
            recv_seq = (recv_seq + 1) & 0xffffffff
            if payload:
                os.write(tun_fd, payload)

    if buffer:
        consume()

    while True:
        ready, _, _ = select.select([tun_fd, sock], [], [], 15)
        if not ready:
            frame = encode_frame(key, send_direction, send_seq, b'')
            sock.sendall(frame)
            send_seq = (send_seq + 1) & 0xffffffff
            last_tx = time.monotonic()
            continue

        if tun_fd in ready:
            try:
                payload = os.read(tun_fd, MAX_PACKET)
            except BlockingIOError:
                payload = b''
            if payload:
                sock.sendall(encode_frame(key, send_direction, send_seq, payload))
                send_seq = (send_seq + 1) & 0xffffffff
                last_tx = time.monotonic()

        if sock in ready:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError('stream closed')
            buffer.extend(chunk)
            consume()
