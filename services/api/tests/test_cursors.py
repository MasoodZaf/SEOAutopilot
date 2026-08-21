from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.cursors import CursorError, PageCursor, decode_page_cursor, encode_page_cursor

KEY = "test-cursor-signing-key-with-32-characters"
SITE_ID = UUID("019d0000-0000-7000-8000-000000000001")


def test_cursor_round_trip_is_scoped_to_site() -> None:
    cursor = PageCursor(
        site_id=SITE_ID,
        last_seen_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        page_id=UUID("019d0000-0000-7000-8000-000000000002"),
    )
    assert decode_page_cursor(encode_page_cursor(cursor, KEY), SITE_ID, KEY) == cursor


def test_cursor_rejects_tampering() -> None:
    cursor = PageCursor(
        site_id=SITE_ID,
        last_seen_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        page_id=UUID("019d0000-0000-7000-8000-000000000002"),
    )
    token = encode_page_cursor(cursor, KEY)
    with pytest.raises(CursorError, match="cursor_signature_invalid"):
        decode_page_cursor(token[:-1] + ("a" if token[-1] != "a" else "b"), SITE_ID, KEY)


def test_cursor_rejects_cross_site_reuse() -> None:
    cursor = PageCursor(
        site_id=SITE_ID,
        last_seen_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        page_id=UUID("019d0000-0000-7000-8000-000000000002"),
    )
    other_site = UUID("019d0000-0000-7000-8000-000000000003")
    with pytest.raises(CursorError, match="cursor_scope_invalid"):
        decode_page_cursor(encode_page_cursor(cursor, KEY), other_site, KEY)
