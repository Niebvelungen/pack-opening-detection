"""Confidence Report (plan.md sections 3.5, 6) -- the sidecar emitted with every config.

Per outcome, a 95% **Wilson** interval on the observed proportion gives a ``ci95`` half-width in
percentage points. Per slot, a ``status`` (``ok`` / ``needs_more_samples`` / ``review``) plus a
"sample N more packs" hint flags slots too thin to trust yet.
"""

from __future__ import annotations

import math

from ..contracts.pack_config import (
    ConfidenceReport,
    OutcomeConfidence,
    SlotConfidence,
    SlotStatus,
)
from .aggregate import SlotTally

Z95 = 1.959963984540054  # 97.5th percentile of the standard normal
DEFAULT_CI_THRESHOLD = 5.0  # percentage points; above this a slot needs more samples
DEFAULT_TARGET_MARGIN = 5.0  # the margin the "sample N more" hint aims for


def wilson_halfwidth(k: int, n: int, z: float = Z95) -> float:
    """Half-width of the Wilson 95% interval for ``k`` successes in ``n`` trials (proportion)."""
    if n == 0:
        return 0.0
    phat = k / n
    denom = 1.0 + z * z / n
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n)) / denom
    return margin


def samples_for_margin(p: float, half_width: float, z: float = Z95) -> int:
    """Approximate trials needed so a proportion ``p`` reaches the target ``half_width`` (0..1)."""
    if half_width <= 0:
        return 0
    return math.ceil(z * z * p * (1.0 - p) / (half_width * half_width))


def _slot_confidence(
    tally: SlotTally, ci_threshold: float, target_margin: float
) -> tuple[SlotConfidence, str | None]:
    n = tally.samples
    outcomes = [
        OutcomeConfidence(
            label=o.label,
            chance=o.chance,
            ci95=round(wilson_halfwidth(o.count, n) * 100.0, 1),
            samples=o.count,
        )
        for o in tally.outcomes
    ]

    max_ci = max((o.ci95 for o in outcomes), default=0.0)
    has_singleton = any(o.samples == 1 for o in outcomes)
    status: SlotStatus
    if has_singleton:
        status = "review"
    elif max_ci > ci_threshold:
        status = "needs_more_samples"
    else:
        status = "ok"

    flag: str | None = None
    if status != "ok" and outcomes:
        worst = max(outcomes, key=lambda o: o.ci95)
        p = worst.samples / n if n else 0.0
        needed = samples_for_margin(p, target_margin / 100.0)
        extra = max(0, needed - n)
        flag = (
            f"{tally.slot} slot: {n} packs only -- {worst.label} estimate +/-{worst.ci95}%, "
            f"sample ~{extra} more"
        )

    return SlotConfidence(samples=n, outcomes=outcomes, status=status), flag


def build_confidence_report(
    set_code: str,
    packs_observed: int,
    tallies: list[SlotTally],
    *,
    ci_threshold: float = DEFAULT_CI_THRESHOLD,
    target_margin: float = DEFAULT_TARGET_MARGIN,
) -> ConfidenceReport:
    """Build the Confidence Report from per-slot tallies (M5 output)."""
    slots: dict[str, SlotConfidence] = {}
    flags: list[str] = []
    for tally in tallies:
        confidence, flag = _slot_confidence(tally, ci_threshold, target_margin)
        slots[tally.slot] = confidence
        if flag is not None:
            flags.append(flag)
    return ConfidenceReport(
        setCode=set_code, packsObserved=packs_observed, slots=slots, flags=flags
    )
