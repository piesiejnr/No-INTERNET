import threading

from discovery import DiscoveryService
from connection_manager import ConnectionManager
from utils import get_device_id, get_device_name

TCP_PORT = 60000


def main() -> None:
    discovered = {}
    lock = threading.Lock()

    def on_device(info):
        device_id = info.get("device_id")
        if not device_id:
            return
        with lock:
            discovered[device_id] = info

    def on_text(peer_id: str, text: str) -> None:
        print(f"\n[{peer_id}] {text}")

    def on_file(peer_id: str, path: str) -> None:
        print(f"\n[{peer_id}] file received: {path}")

    def on_peer_connected(peer_id: str, name: str) -> None:
        print(f"\nconnected: {peer_id} ({name})")

    def on_peer_disconnected(peer_id: str) -> None:
        print(f"\ndisconnected: {peer_id}")

    manager = ConnectionManager(
        TCP_PORT,
        on_text,
        on_file,
        on_peer_connected,
        on_peer_disconnected,
    )
    manager.start_server()

    discovery = DiscoveryService(TCP_PORT, on_device)
    discovery.start()

    print("LynkLAN PC client")
    print(f"Device: {get_device_name()} ({get_device_id()})")
    print("Type 'help' for commands.")

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue

        if line == "help":
            print("commands:")
            print("  peers")
            print("  discoveries")
            print("  connect <ip> <port>")
            print("  connect_discovered <device_id>")
            print("  msg <peer_id> <text>")
            print("  sendfile <peer_id> <path>")
            print("  quit")
            continue

        if line == "peers":
            peers = manager.get_peers()
            if not peers:
                print("no peers")
                continue
            for peer_id, peer in peers.items():
                name = peer.device_name or "unknown"
                print(f"{peer_id} {name}")
            continue

        if line == "discoveries":
            with lock:
                if not discovered:
                    print("no discoveries")
                    continue
                for device_id, info in discovered.items():
                    print(
                        f"{device_id} {info.get('device_name')} "
                        f"{info.get('ip')}:{info.get('tcp_port')}"
                    )
            continue

        if line.startswith("connect_discovered "):
            _, device_id = line.split(" ", 1)
            with lock:
                info = discovered.get(device_id)
            if not info:
                print("device not found")
                continue
            if not manager.connect_to(info.get("ip"), int(info.get("tcp_port"))):
                print("connect failed")
            continue

        if line.startswith("connect "):
            parts = line.split(" ")
            if len(parts) != 3:
                print("usage: connect <ip> <port>")
                continue
            ip = parts[1]
            port = int(parts[2])
            if not manager.connect_to(ip, port):
                print("connect failed")
            continue

        if line.startswith("msg "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("usage: msg <peer_id> <text>")
                continue
            peer_id = parts[1]
            text = parts[2]
            manager.send_text(peer_id, text)
            continue

        if line.startswith("sendfile "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("usage: sendfile <peer_id> <path>")
                continue
            peer_id = parts[1]
            path = parts[2]
            manager.send_file(peer_id, path)
            continue

        if line == "quit":
            break

        print("unknown command")

    discovery.stop()
    manager.stop()


if __name__ == "__main__":
    main()
