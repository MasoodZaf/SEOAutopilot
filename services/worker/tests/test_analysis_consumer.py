from app.analysis_consumer import new_messages, reclaimed_messages


def test_parses_reclaimed_stream_messages() -> None:
    reply = ["0-0", [["1-0", {"type": "crawl.completed"}]], []]
    assert reclaimed_messages(reply) == [("1-0", {"type": "crawl.completed"})]


def test_parses_new_stream_messages() -> None:
    reply = [["seo-autopilot:events", [["2-0", {"tenant_id": "tenant"}]]]]
    assert new_messages(reply) == [("2-0", {"tenant_id": "tenant"})]


def test_malformed_stream_replies_fail_closed() -> None:
    assert reclaimed_messages(None) == []
    assert new_messages({"unexpected": True}) == []
