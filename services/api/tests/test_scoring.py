import pytest

from app.domain.scoring import ScoreFactors, opportunity_score


def test_risk_adjustment():
    assert opportunity_score(ScoreFactors(0.9, 0.8, 0.7, 0.2, "low")) == 100
    assert opportunity_score(ScoreFactors(0.9, 0.8, 0.7, 0.2, "high")) == 44.1


def test_prohibited_is_zero():
    assert opportunity_score(ScoreFactors(1, 1, 1, 0, "prohibited")) == 0


def test_invalid_fails_closed():
    with pytest.raises(ValueError):
        opportunity_score(ScoreFactors(2, 1, 1, 0, "low"))
