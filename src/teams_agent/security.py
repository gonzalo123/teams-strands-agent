import base64
import hashlib
import hmac


def signature(body: bytes, key: bytes) -> str:
    digest = hmac.new(key, body, hashlib.sha256).digest()
    return "HMAC " + base64.b64encode(digest).decode("ascii")


def authenticated(body: bytes, authorization: str, key: bytes) -> bool:
    return hmac.compare_digest(signature(body, key).encode(), authorization.encode())
