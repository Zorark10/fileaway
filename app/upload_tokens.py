import base64
import binascii
import hashlib
import hmac
import json
import time


class InvalidUploadToken(Exception):
    pass


class ExpiredUploadToken(InvalidUploadToken):
    pass


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_upload_token(
    payload: dict,
    secret: str,
    ttl_seconds: int,
) -> str:
    body = {
        **payload,
        "v": 1,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(
        body,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"),
        raw,
        hashlib.sha256,
    ).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}"


def verify_upload_token(token: str, secret: str) -> dict:
    try:
        encoded_body, encoded_signature = token.split(".", 1)
        raw = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise InvalidUploadToken("Malformed upload token") from exc

    expected = hmac.new(
        secret.encode("utf-8"),
        raw,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidUploadToken("Invalid upload token signature")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidUploadToken("Invalid upload token payload") from exc

    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise InvalidUploadToken("Unsupported upload token")

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int):
        raise InvalidUploadToken("Upload token has no expiry")
    if expires_at <= int(time.time()):
        raise ExpiredUploadToken("Upload authorization has expired")

    return payload
