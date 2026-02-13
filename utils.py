import os
import socket
import uuid
import time

DEVICE_ID_FILE = "device_id.txt"


def get_device_id() -> str:
    if os.path.exists(DEVICE_ID_FILE):
        with open(DEVICE_ID_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    value = str(uuid.uuid4())
    with open(DEVICE_ID_FILE, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def get_device_name() -> str:
    return socket.gethostname()


def get_platform() -> str:
    return "pc"


def get_timestamp() -> int:
    return int(time.time())


def get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()
