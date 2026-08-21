import logging

from app.core.logging import RedactAccessQueryFilter, configure_safe_access_logging


def access_record(request_target: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:12345",
            "GET",
            request_target,
            "1.1",
            303,
        ),
        None,
    )


def test_access_filter_removes_oauth_code_and_state_values() -> None:
    record = access_record(
        "/v1/connectors/oauth/callback?state=state-secret&code=authorization-secret&scope=readonly"
    )

    assert RedactAccessQueryFilter().filter(record)

    rendered = record.getMessage()
    assert "/v1/connectors/oauth/callback?[REDACTED]" in rendered
    assert "state-secret" not in rendered
    assert "authorization-secret" not in rendered
    assert "scope=readonly" not in rendered
    assert rendered.endswith('HTTP/1.1" 303')


def test_access_filter_leaves_queryless_request_unchanged() -> None:
    record = access_record("/v1/system/readiness")

    assert RedactAccessQueryFilter().filter(record)

    assert "/v1/system/readiness" in record.getMessage()
    assert "[REDACTED]" not in record.getMessage()


def test_access_filter_configuration_is_idempotent() -> None:
    logger = logging.getLogger("uvicorn.access")
    original_filters = list(logger.filters)
    try:
        logger.filters = []
        configure_safe_access_logging()
        configure_safe_access_logging()
        installed = [
            item
            for item in logger.filters
            if getattr(item, "marker", None) == RedactAccessQueryFilter.marker
        ]
        assert len(installed) == 1
    finally:
        logger.filters = original_filters
