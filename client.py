from connection_manager import ConnectionManager


def connect(manager: ConnectionManager, ip: str, port: int) -> None:
    manager.connect_to(ip, port)
