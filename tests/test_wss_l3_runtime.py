import importlib.util
import socket
import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "tp27-wss-l3.py"
SPEC = importlib.util.spec_from_file_location("tp27_wss_l3", MODULE_PATH)
assert SPEC and SPEC.loader
wss = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wss
SPEC.loader.exec_module(wss)


class WSSL3RuntimeTests(unittest.TestCase):
    def server_cfg(self):
        return wss.Config.from_dict(
            {
                "id": "lab1",
                "role": "server",
                "interface": "t27wss1",
                "tunnel_ip": "10.251.10.1/30",
                "peer_ip": "10.251.10.2",
                "mtu": 1280,
                "ws_path": "/tp27/lab1",
                "auth_token": "0123456789abcdef0123456789abcdef",
                "listen_port": 8443,
                "tls_cert": "/tmp/cert.pem",
                "tls_key": "/tmp/key.pem",
            }
        )

    def client_cfg(self):
        return wss.Config.from_dict(
            {
                "id": "lab1",
                "role": "client",
                "interface": "t27wss2",
                "tunnel_ip": "10.251.10.2/30",
                "peer_ip": "10.251.10.1",
                "mtu": 1280,
                "ws_path": "/tp27/lab1",
                "auth_token": "0123456789abcdef0123456789abcdef",
                "remote_addr": "127.0.0.1:8443",
                "server_name": "lab.example",
            }
        )

    def test_config_validates_point_to_point_subnet(self):
        self.assertEqual(self.server_cfg().peer_ip, "10.251.10.2")
        with self.assertRaises(ValueError):
            wss.Config.from_dict(
                {
                    "id": "bad",
                    "role": "client",
                    "interface": "t27bad",
                    "tunnel_ip": "10.251.10.2/30",
                    "peer_ip": "10.252.10.1",
                    "mtu": 1280,
                    "ws_path": "/tp27/bad1",
                    "auth_token": "0123456789abcdef",
                    "remote_addr": "127.0.0.1:8443",
                    "server_name": "lab.example",
                }
            )

    def test_websocket_accept_matches_rfc_example(self):
        self.assertEqual(
            wss.websocket_accept_value("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_masked_client_frame_round_trip(self):
        left, right = socket.socketpair()
        try:
            payload = b"\x45" + b"A" * 1279
            left.sendall(wss.encode_frame(payload, masked=True))
            opcode, decoded = wss.recv_frame(right, require_masked=True)
            self.assertEqual(opcode, 0x2)
            self.assertEqual(decoded, payload)
        finally:
            left.close()
            right.close()

    def test_unmasked_server_frame_round_trip(self):
        left, right = socket.socketpair()
        try:
            payload = b"hello-tp27"
            left.sendall(wss.encode_frame(payload, masked=False))
            opcode, decoded = wss.recv_frame(right, require_masked=False)
            self.assertEqual(opcode, 0x2)
            self.assertEqual(decoded, payload)
        finally:
            left.close()
            right.close()

    def test_mask_direction_is_enforced(self):
        left, right = socket.socketpair()
        try:
            left.sendall(wss.encode_frame(b"bad-direction", masked=False))
            with self.assertRaises(ValueError):
                wss.recv_frame(right, require_masked=True)
        finally:
            left.close()
            right.close()

    def test_plain_socket_websocket_handshake_helpers(self):
        server_sock, client_sock = socket.socketpair()
        server_cfg = self.server_cfg()
        client_cfg = self.client_cfg()
        error = []

        def server():
            try:
                wss.server_handshake(server_sock, server_cfg)
            except Exception as exc:
                error.append(exc)

        thread = threading.Thread(target=server)
        thread.start()
        try:
            wss.client_handshake(client_sock, client_cfg)
        finally:
            thread.join(timeout=2)
            server_sock.close()
            client_sock.close()
        self.assertFalse(error)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
