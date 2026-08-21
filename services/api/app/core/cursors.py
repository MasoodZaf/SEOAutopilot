import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class CursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PageCursor:
    site_id: UUID
    last_seen_at: datetime
    page_id: UUID


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_page_cursor(cursor: PageCursor, key: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "site_id": str(cursor.site_id),
            "last_seen_at": cursor.last_seen_at.isoformat(),
            "page_id": str(cursor.page_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(key.encode(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def decode_page_cursor(token: str, expected_site_id: UUID, key: str) -> PageCursor:
    try:
        payload_token, signature_token = token.split(".", 1)
        payload = _decode(payload_token)
        signature = _decode(signature_token)
        expected = hmac.new(key.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CursorError("cursor_signature_invalid")
        data = json.loads(payload)
        if data.get("v") != 1:
            raise CursorError("cursor_version_invalid")
        cursor = PageCursor(
            site_id=UUID(data["site_id"]),
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]),
            page_id=UUID(data["page_id"]),
        )
        if cursor.site_id != expected_site_id:
            raise CursorError("cursor_scope_invalid")
        if cursor.last_seen_at.tzinfo is None:
            raise CursorError("cursor_timestamp_invalid")
        return cursor
    except CursorError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CursorError("cursor_invalid") from error
