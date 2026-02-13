import json
import socket
import threading
import time
from typing import Callable, Dict, Optional

from utils import get_device_id, get_device_name, get_platform, get_timestamp, get_local_ip

DISCOVERY_PORT = 50000
BROADCAST_ADDR = "255.255.255.255"


class DiscoveryService:
    def __init__(self, tcp_port: int, on_device: Callable[[Dict[str, str]], None]) -> None:
        self.tcp_port = tcp_port
        self.on_device = on_device
        self.running = False
        self.listener_thread: Optional[threading.Thread] = None
        self.broadcast_thread: Optional[threading.Thread] = None
        self.sock: Optional[socket.socket] = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", DISCOVERY_PORT))
        self.listener_thread = threading.Thread(target=self._listen, daemon=True)
        self.listener_thread.start()
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.sock:
            self.sock.close()

    def _broadcast_loop(self) -> None:
        while self.running and self.sock:
            message = {
                "type": "discovery_request",
                "device_id": get_device_id(),
                "device_name": get_device_name(),
                "platform": get_platform(),
                "ip": get_local_ip(),
                "tcp_port": self.tcp_port,
                "timestamp": get_timestamp(),
            }
            data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            try:
                self.sock.sendto(data, (BROADCAST_ADDR, DISCOVERY_PORT))
            except OSError:
                pass
            time.sleep(3)

    def _listen(self) -> None:
        while self.running and self.sock:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                break
            try:
                message = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue

            msg_type = message.get("type")
            if msg_type == "discovery_request":
                self._send_response(addr[0])
            elif msg_type == "discovery_response":
                if message.get("device_id") == get_device_id():
                    continue
                self.on_device(message)

    def _send_response(self, ip: str) -> None:
        if not self.sock:
            return
        message = {
            "type": "discovery_response",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "ip": get_local_ip(),
            "tcp_port": self.tcp_port,
            "timestamp": get_timestamp(),
        }
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        try:
            self.sock.sendto(data, (ip, DISCOVERY_PORT))
        except OSError:
            pass
