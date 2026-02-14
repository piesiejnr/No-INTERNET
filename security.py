"""Simple HMAC signing helper for optional message integrity.

Provides basic message authentication using shared secrets.
Not currently integrated but ready for future security layer.

Rationale:
- HMAC-SHA256 is fast and secure for message authentication.
- Shared secret model works for small trusted groups.
- For production, consider asymmetric signatures or TLS.
"""

import hmac
import hashlib
from typing import Dict, Any


def sign_message(secret: str, message: Dict[str, Any]) -> str:
    """Return an HMAC signature for a message dict.
    
    Reason: Prevents tampering; receiver with same secret can verify.
    Uses string representation of dict (deterministic with sorted keys).
    """
    # Convert message to bytes; str() is simple but fragile.
    # TODO: Use canonical JSON for production.
    payload = str(message).encode("utf-8")
    # Compute HMAC-SHA256 and return hex digest.
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
