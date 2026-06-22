"""Review queue (plan.md section 7) -- the human-in-the-loop artifact.

Collects everything the pipeline flagged rather than dropped into one reviewable list: unresolved
detections, low-confidence ids, packs that failed template validation, and slots still below the
sample threshold. Serialised to JSON alongside the config + confidence report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..contracts.observation import PackObservation
from ..contracts.pack_config import ConfidenceReport

ReviewKind = str  # "unresolved" | "low_confidence" | "template_mismatch" | "under_sampled"

DEFAULT_LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class ReviewItem:
    """One flagged item awaiting a maintainer's confirmation or patch."""

    kind: ReviewKind
    detail: str
    sourceId: str | None = None
    packIndex: int | None = None


def build_review_queue(
    observations: list[PackObservation],
    validation_flags: list[str],
    report: ConfidenceReport,
    *,
    low_confidence: float = DEFAULT_LOW_CONFIDENCE,
) -> list[ReviewItem]:
    """Assemble the review queue from the pipeline's flagged outputs."""
    items: list[ReviewItem] = []

    for obs in observations:
        if obs.unresolved:
            items.append(
                ReviewItem(
                    kind="unresolved",
                    detail=f"{obs.unresolved} detection(s) could not be identified",
                    sourceId=obs.sourceId,
                    packIndex=obs.packIndex,
                )
            )
        for card in obs.cards:
            if card.confidence < low_confidence:
                items.append(
                    ReviewItem(
                        kind="low_confidence",
                        detail=f"{card.cardId} ({card.idMethod}) confidence {card.confidence:.2f}",
                        sourceId=obs.sourceId,
                        packIndex=obs.packIndex,
                    )
                )

    for flag in validation_flags:
        items.append(ReviewItem(kind="template_mismatch", detail=flag))

    for slot_name, slot in report.slots.items():
        if slot.status != "ok":
            items.append(
                ReviewItem(
                    kind="under_sampled",
                    detail=f"slot {slot_name}: status={slot.status}, samples={slot.samples}",
                )
            )

    return items


def review_queue_to_json(items: list[ReviewItem]) -> str:
    """Serialise the review queue to pretty JSON."""
    return json.dumps([asdict(i) for i in items], indent=2)


def write_review_queue(items: list[ReviewItem], path: str | Path) -> None:
    Path(path).write_text(review_queue_to_json(items), encoding="utf-8")
