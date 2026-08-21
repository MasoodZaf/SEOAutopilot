from app.domain.synthetic_control import calculate_synthetic_control_diff_in_diff


def test_synthetic_control_with_clear_treatment_effect() -> None:
    # Target URL: clicks went from 100 to 180 (delta +80)
    target_b = 100.0
    target_f = 180.0

    # Control group URLs: average baseline was 100, followup was 110 (site-wide trend +10)
    ctrl_b = [90.0, 100.0, 110.0]
    ctrl_f = [100.0, 110.0, 120.0]

    res = calculate_synthetic_control_diff_in_diff(target_b, target_f, ctrl_b, ctrl_f)

    assert res.target_delta == 80.0
    assert res.control_delta_avg == 10.0
    assert res.net_treatment_effect == 70.0  # +80 raw - 10 background trend = +70 true effect
    assert res.is_statistically_significant
    assert "outperformed control group" in res.explanation


def test_synthetic_control_when_control_group_is_empty() -> None:
    res = calculate_synthetic_control_diff_in_diff(50.0, 80.0, [], [])
    assert res.target_delta == 30.0
    assert res.net_treatment_effect == 30.0
    assert not res.is_statistically_significant
