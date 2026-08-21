from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any


class RedactAccessQueryFilter(logging.Filter):
    """Remove query values from Uvicorn access-log request targets."""

    marker = "seo_autopilot_redact_access_query"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, Sequence) or isinstance(args, (str, bytes)) or len(args) < 3:
            return True

        request_target = args[2]
        if not isinstance(request_target, str) or "?" not in request_target:
            return True

        safe_target = f"{request_target.partition('?')[0]}?[REDACTED]"
        safe_args: list[Any] = list(args)
        safe_args[2] = safe_target
        record.args = tuple(safe_args)
        return True


def configure_safe_access_logging() -> None:
    """Install the access-log redactor once for this API process."""

    logger = logging.getLogger("uvicorn.access")
    if any(getattr(existing, "marker", None) == RedactAccessQueryFilter.marker for existing in logger.filters):
        return
    logger.addFilter(RedactAccessQueryFilter())
