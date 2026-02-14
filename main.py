"""CLI entry point for the LynkLAN PC client.

This module provides an interactive command-line interface for the peer-to-peer
LAN messaging system. It orchestrates discovery, connection management, direct
messaging, group chat, and file transfer without requiring internet or servers.

Rationale:
- CLI chosen for PC to enable rapid testing and debugging before GUI.
- All state kept in callbacks/storage to simplify porting to mobile.
"""

import threading

from discovery import DiscoveryService
from connection_manager import ConnectionManager
from utils import get_device_id, get_device_name
from storage import ChatStore

# Default TCP port for peer connections. High numbered to avoid conflicts.
TCP_PORT = 60000


def main() -> None:
    # In-memory caches for CLI display and invite flow.
    # Reason: Avoid polling storage on every command; only persist critical state.
    discovered = {}
    lock = threading.Lock()  # Protects discovered dict from race conditions.

    def on_device(info):
        device_id = info.get("device_id")
        if not device_id:
            return
        with lock:
            is_new = device_id not in discovered
            discovered[device_id] = info
        if is_new:
            print(
                f"\ndiscovered: {device_id} {info.get('device_name')} "
                f"{info.get('ip')}:{info.get('tcp_port')}"
            )

    # Callbacks invoked by the connection manager.
    # Reason: Event-driven architecture decouples networking from UI updates.
    def on_text(peer_id: str, text: str) -> None:
        """Handle incoming direct messages. Prints to console for visibility."""
        print(f"\n[{peer_id}] {text}")

    def on_file(peer_id: str, path: str) -> None:
        """Notify user when file transfer completes. Path shows where it's saved."""
        print(f"\n[{peer_id}] file received: {path}")

    # Track pending group invites locally so user can accept/reject by group_id.
    # Reason: Invites are transient and don't need persistent storage.
    pending_invites = {}

    def on_group(peer_id: str, group_id: str, text: str) -> None:
        """Display group messages with clear group context for multi-group scenarios."""
        print(f"\n[group {group_id}] {peer_id}: {text}")

    def on_group_invite(group_id: str, name: str, master_id: str, inviter_id: str) -> None:
        """Cache invite and prompt user to accept/reject.
        
        Reason: Prevents auto-joining; user controls group membership explicitly.
        """
        pending_invites[group_id] = {
            "name": name,
            "master_id": master_id,
            "inviter_id": inviter_id,
        }
        print(
            f"\ninvite: group={group_id} name={name} "
            f"master={master_id} from={inviter_id}"
        )
        print("use: group_accept <group_id> or group_reject <group_id>")

    def on_group_notice(text: str) -> None:
        """Generic group operation feedback (invites sent, joins, errors)."""
        print(f"\n[group] {text}")

    def on_peer_connected(peer_id: str, name: str) -> None:
        """Notify on new TCP peer connection for awareness."""
        print(f"\nconnected: {peer_id} ({name})")

    def on_peer_disconnected(peer_id: str) -> None:
        """Notify on peer disconnect; helps diagnose network issues."""
        print(f"\ndisconnected: {peer_id}")

    # Core services: storage, connections, discovery.
    # Reason: Separate concerns; storage is independent of network layer.
    store = ChatStore()
    manager = ConnectionManager(
        TCP_PORT,
        on_text,
        on_file,
        on_group,
        on_group_invite,
        on_group_notice,
        on_peer_connected,
        on_peer_disconnected,
        store,
    )
    # Start TCP server immediately so we can accept inbound connections.
    manager.start_server()

    # Start UDP broadcast discovery in parallel.
    # Reason: Passive discovery allows peers to find each other without manual IP entry.
    discovery = DiscoveryService(TCP_PORT, on_device)
    discovery.start()

    print("LynkLAN PC client")
    print(f"Device: {get_device_name()} ({get_device_id()})")
    print("Type 'help' for commands.")

    # Command loop for local interaction.
    # Reason: Blocking input is fine for CLI; async not needed here.
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit on Ctrl+C or Ctrl+D.
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
            print("  history <peer_id>")
            print("  groups")
            print("  group_create <name>")
            print("  group_invite <group_id> <peer_id,peer_id,...>")
            print("  group_accept <group_id>")
            print("  group_reject <group_id>")
            print("  group_send <group_id> <text>")
            print("  group_history <group_id>")
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

        if line == "groups":
            groups = store.get_groups()
            if not groups:
                print("no groups")
                continue
            for group_id, info in groups.items():
                name = info.get("name", "group")
                master = info.get("master_id", "unknown")
                members = ",".join(info.get("members", []))
                print(f"{group_id} {name} master={master} members={members}")
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

        if line.startswith("history "):
            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("usage: history <peer_id>")
                continue
            peer_id = parts[1]
            entries = store.read_direct(peer_id)
            if not entries:
                print("no history")
                continue
            for entry in entries:
                text = entry.get("payload", {}).get("text", "")
                ts = entry.get("timestamp", "")
                print(f"{ts} {peer_id}: {text}")
            continue

        if line.startswith("group_create "):
            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("usage: group_create <name>")
                continue
            name = parts[1]
            group_id = manager.create_group(name)
            print(f"group created: {group_id}")
            continue

        if line.startswith("group_invite "):
            parts = line.split(" ", 2)
            if len(parts) != 3:
                print("usage: group_invite <group_id> <peer_id,peer_id,...>")
                continue
            group_id = parts[1]
            raw_members = [p for p in parts[2].split(",") if p]
            members = set(raw_members)
            manager.invite_to_group(group_id, members)
            continue

        if line.startswith("group_accept "):
            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("usage: group_accept <group_id>")
                continue
            group_id = parts[1]
            invite = pending_invites.get(group_id)
            if not invite:
                print("no pending invite")
                continue
            manager.accept_group_invite(group_id, invite["master_id"], invite["name"])
            pending_invites.pop(group_id, None)
            continue

        if line.startswith("group_reject "):
            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("usage: group_reject <group_id>")
                continue
            group_id = parts[1]
            invite = pending_invites.get(group_id)
            if not invite:
                print("no pending invite")
                continue
            manager.reject_group_invite(group_id, invite["master_id"])
            pending_invites.pop(group_id, None)
            continue

        if line.startswith("group_send "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("usage: group_send <group_id> <text>")
                continue
            group_id = parts[1]
            text = parts[2]
            manager.send_group_message(group_id, text)
            continue

        if line.startswith("group_history "):
            parts = line.split(" ", 1)
            if len(parts) != 2:
                print("usage: group_history <group_id>")
                continue
            group_id = parts[1]
            entries = store.read_group(group_id)
            if not entries:
                print("no group history")
                continue
            for entry in entries:
                text = entry.get("payload", {}).get("text", "")
                sender = entry.get("device_id", "unknown")
                ts = entry.get("timestamp", "")
                print(f"{ts} {sender}: {text}")
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
