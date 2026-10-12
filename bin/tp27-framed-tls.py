#!/usr/bin/env python3
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
from pathlib import Path

TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
MAX_PACKET = 65535


def run_quiet(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def load_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    role = str(data.get('role', '')).strip()
    if role not in {'framed-server', 'framed-client'}:
        raise RuntimeError('invalid framed TLS role')
    for key in ('interface', 'tunnel_ip', 'peer_ip'):
        if not data.get(key):
            raise RuntimeError(f'missing {key}')
    port = int(data.get('listen_port', 0))
    if not 1 <= port <= 65535:
        raise RuntimeError('invalid listen_port')
    mtu = int(data.get('mtu', 1300))
    if not 1000 <= mtu <= 1400:
        raise RuntimeError('invalid mtu')
    data['listen_port'] = port
    data['mtu'] = mtu
    return data


def make_tun(cfg):
    run_quiet(['modprobe', 'tun'])
    fd = os.open('/dev/net/tun', os.O_RDWR | os.O_NONBLOCK)
    ifr = struct.pack('16sH', cfg['interface'].encode(), IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(fd, TUNSETIFF, ifr)
    run_quiet(['ip', 'addr', 'flush', 'dev', cfg['interface']])
    p = subprocess.run(
        ['ip', 'addr', 'add', cfg['tunnel_ip'], 'dev', cfg['interface']],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'ip addr add failed').strip())
    p = subprocess.run(
        ['ip', 'link', 'set', 'dev', cfg['interface'], 'mtu', str(cfg['mtu']),
         'txqueuelen', '1000', 'up'],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'ip link up failed').strip())
    run_quiet(['sysctl', '-q', '-w', f"net.ipv4.conf.{cfg['interface']}.rp_filter=0"])
    return fd


def tune_socket(writer):
    sock = writer.get_extra_info('socket')
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, 'TCP_KEEPIDLE'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        if hasattr(socket, 'TCP_KEEPINTVL'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
        if hasattr(socket, 'TCP_KEEPCNT'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except Exception:
        pass


async def pump(reader, writer, tun_fd):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=8192)

    def tun_ready():
        try:
            packet = os.read(tun_fd, MAX_PACKET)
        except BlockingIOError:
            return
        if not packet:
            return
        try:
            queue.put_nowait(packet)
        except asyncio.QueueFull:
            pass

    loop.add_reader(tun_fd, tun_ready)

    async def sender():
        while True:
            try:
                packet = await asyncio.wait_for(queue.get(), timeout=10)
                writer.write(struct.pack('!I', len(packet)) + packet)
            except asyncio.TimeoutError:
                writer.write(b'\x00\x00\x00\x00')
            await writer.drain()

    async def receiver():
        while True:
            header = await reader.readexactly(4)
            length = struct.unpack('!I', header)[0]
            if length == 0:
                continue
            if length > MAX_PACKET:
                raise RuntimeError(f'invalid frame size {length}')
            packet = await reader.readexactly(length)
            while True:
                try:
                    os.write(tun_fd, packet)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.001)

    tx = asyncio.create_task(sender())
    rx = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait([tx, rx], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    finally:
        loop.remove_reader(tun_fd)
        for task in (tx, rx):
            if not task.done():
                task.cancel()


async def server_main(cfg, tun_fd):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cfg['cert'], cfg['key'])
    bind_ip = str(cfg.get('local_public') or '0.0.0.0')
    allowed = str(cfg.get('allowed_peer') or '').strip()
    lock = asyncio.Lock()

    async def handle(reader, writer):
        peer = writer.get_extra_info('peername')
        if not peer or (allowed and peer[0] != allowed):
            print(f'TP27_FRAMED_REJECT peer={peer}', flush=True)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        tune_socket(writer)
        async with lock:
            print(f'TP27_FRAMED_CONNECTED peer={peer[0]}:{peer[1]}', flush=True)
            try:
                await pump(reader, writer, tun_fd)
            except Exception as exc:
                print(f'TP27_FRAMED_DISCONNECTED {type(exc).__name__}: {exc}', flush=True)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    server = await asyncio.start_server(
        handle, bind_ip, cfg['listen_port'], ssl=context, backlog=128,
    )
    print(
        f"TP27_FRAMED_READY role=server iface={cfg['interface']} "
        f"addr={cfg['tunnel_ip']} listen={bind_ip}:{cfg['listen_port']}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


async def client_main(cfg, tun_fd):
    expected = str(cfg.get('cert_pin') or '').replace(':', '').strip().lower()
    if len(expected) != 64:
        raise RuntimeError('invalid certificate pin')
    remote = str(cfg.get('remote_public') or '').strip()
    if not remote:
        raise RuntimeError('missing remote_public')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    print(
        f"TP27_FRAMED_READY role=client iface={cfg['interface']} addr={cfg['tunnel_ip']} "
        f"remote={remote}:{cfg['listen_port']}",
        flush=True,
    )
    failures = 0
    while True:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(remote, cfg['listen_port'], ssl=context, server_hostname=None),
                timeout=12,
            )
            ssl_obj = writer.get_extra_info('ssl_object')
            actual = hashlib.sha256(ssl_obj.getpeercert(binary_form=True)).hexdigest()
            if actual != expected:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                raise RuntimeError(f'certificate pin mismatch actual={actual}')
            tune_socket(writer)
            failures = 0
            print(f'TP27_FRAMED_CONNECTED remote={remote}:{cfg["listen_port"]} pin={actual}', flush=True)
            await pump(reader, writer, tun_fd)
        except Exception as exc:
            failures += 1
            delay = min(15, max(2, failures * 2))
            print(f'TP27_FRAMED_RECONNECT {type(exc).__name__}: {exc} retry={delay}s', flush=True)
            await asyncio.sleep(delay)


async def async_main(config_path):
    cfg = load_config(config_path)
    tun_fd = make_tun(cfg)
    if cfg['role'] == 'framed-server':
        await server_main(cfg, tun_fd)
    else:
        await client_main(cfg, tun_fd)


def main():
    p = argparse.ArgumentParser(prog='tp27-framed-tls')
    p.add_argument('--config', required=True)
    args = p.parse_args()
    try:
        asyncio.run(async_main(args.config))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print('TP27_FRAMED_FATAL ' + str(exc), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
