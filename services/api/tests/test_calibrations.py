from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CalibrationReviewCreate
from app.core.context import Role, TenantContext
from app.db.models import CalibrationReview
from app.services.calibrations import (
    CalibrationService,
    calibration_summary,
    review_request_hash,
)


def command(notes: str = "Checked rendered page") -> CalibrationReviewCreate:
    return CalibrationReviewCreate(
        accuracy_label="true_positive",
        actionability="edit",
        severity_fit="appropriate",
        notes=notes,
    )


def review(accuracy: str, actionability: str) -> CalibrationReview:
    return CalibrationReview(
        id=uuid4(),
        tenant_id=uuid4(),
        calibration_item_id=uuid4(),
        reviewer_id=uuid4(),
        accuracy_label=accuracy,
        actionability=actionability,
        severity_fit="appropriate",
        notes="",
        request_hash="r" * 64,
        idempotency_key="review-key",
        created_at=datetime.now(UTC),
    )


def test_review_hash_is_stable_and_bound_to_item_and_normalized_notes() -> None:
    item_id = UUID("019d0000-0000-7000-8000-000000000111")
    assert review_request_hash(item_id, command("  Checked rendered page  ")) == review_request_hash(
        item_id, command()
    )
    assert review_request_hash(item_id, command()) != review_request_hash(uuid4(), command())


def test_calibration_summary_excludes_uncertain_from_precision() -> None:
    summary = calibration_summary(
        target_size=20,
        reviews=[
            review("true_positive", "accept"),
            review("false_positive", "dismiss"),
            review("uncertain", "defer"),
        ],
    )
    assert summary == {
        "target_size": 20,
        "reviewed": 3,
        "true_positive": 1,
        "false_positive": 1,
        "uncertain": 1,
        "precision": 0.5,
        "actionable": 1,
    }


@pytest.mark.asyncio
async def test_viewer_cannot_create_or_submit_calibration() -> None:
    session = cast(AsyncSession, MagicMock(spec=AsyncSession))
    context = TenantContext(uuid4(), uuid4(), Role.VIEWER, "trace")
    service = CalibrationService(session, context)

    with pytest.raises(HTTPException) as create_error:
        await service.create_run(uuid4(), target_size=20, idempotency_key="create-review-set")
    assert create_error.value.status_code == 403

    with pytest.raises(HTTPException) as review_error:
        await service.review_item(uuid4(), command(), "submit-review")
    assert review_error.value.status_code == 403
