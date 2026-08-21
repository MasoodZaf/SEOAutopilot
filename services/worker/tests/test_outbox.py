import json
from uuid import UUID

from app.outbox import encode_event


def test_encode_event_is_deterministic_and_carries_dedupe_id() -> None:
    row = {
        "id": UUID("019d0000-0000-7000-8000-000000000001"),
        "tenant_id": UUID("019d0000-0000-7000-8000-000000000002"),
        "event_type": "crawl.requested",
        "event_version": 1,
        "aggregate_type": "crawl_job",
        "aggregate_id": UUID("019d0000-0000-7000-8000-000000000003"),
        "payload": {"site_id": "site", "crawl_id": "crawl"},
    }
    encoded = encode_event(row)
    assert encoded["event_id"] == str(row["id"])
    assert json.loads(encoded["payload"]) == row["payload"]
