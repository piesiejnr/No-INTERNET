import hmac
import hashlib
from typing import Dict, Any


def sign_message(secret: str, message: Dict[str, Any]) -> str:
    payload = str(message).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
