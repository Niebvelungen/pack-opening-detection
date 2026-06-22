"""Tests for the Confidence Report (M6): Wilson CIs, slot status transitions, sample hints."""

from __future__ import annotations

from pack_config_miner.pipeline.aggregate import OutcomeTally, SlotTally
from pack_config_miner.pipeline.confidence import (
    build_confidence_report,
    samples_for_margin,
    wilson_halfwidth,
)


def _outcome(label: str, count: int, chance: int) -> OutcomeTally:
    return OutcomeTally(label=label, rarity=label, conditions=(), count=count, chance=chance)


def test_wilson_halfwidth_zero_and_monotonic() -> None:
    assert wilson_halfwidth(0, 0) == 0.0
    # More samples at the same proportion -> a tighter interval.
    assert wilson_halfwidth(50, 100) > wilson_halfwidth(500, 1000) > 0.0


def test_samples_for_margin_positive() -> None:
    assert samples_for_margin(0.5, 0.05) > samples_for_margin(0.5, 0.10)
    assert samples_for_margin(0.0, 0.05) == 0


def test_status_ok_when_well_sampled() -> None:
    tally = SlotTally(
        "R-SR-MR", samples=400, outcomes=[_outcome("R", 200, 50), _outcome("SR", 200, 50)]
    )
    report = build_confidence_report("GLD", 400, [tally])
    assert report.slots["R-SR-MR"].status == "ok"
    assert report.flags == []


def test_status_needs_more_samples_when_ci_wide() -> None:
    # n=20, no singletons, but the proportions carry a wide CI (> 5%).
    tally = SlotTally("FOIL", samples=20, outcomes=[_outcome("R", 12, 60), _outcome("SR", 8, 40)])
    report = build_confidence_report("GLD", 20, [tally])
    assert report.slots["FOIL"].status == "needs_more_samples"
    assert any("sample" in f for f in report.flags)


def test_status_review_on_singleton() -> None:
    tally = SlotTally("FOIL", samples=50, outcomes=[_outcome("R", 49, 98), _outcome("MR", 1, 2)])
    report = build_confidence_report("GLD", 50, [tally])
    assert report.slots["FOIL"].status == "review"


def test_outcome_ci_is_reported_in_percentage_points() -> None:
    tally = SlotTally("R-SR-MR", samples=214, outcomes=[_outcome("MR", 11, 5)])
    report = build_confidence_report("GLD", 214, [tally])
    ci = report.slots["R-SR-MR"].outcomes[0].ci95
    # 11/214 with a Wilson interval -> roughly +/-3 percentage points.
    assert 2.5 < ci < 3.5
