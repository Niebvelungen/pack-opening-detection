"""Stage [3] -- Identification seam (plan.md section 4).

The provider-agnostic interface every identifier implements: ``identify(frame) -> list[Detection]``.
Tier 1 (vision-LLM, :mod:`vision_llm`) and a future Tier 2 (local CV, M7) are swappable behind
this one protocol -- no downstream stage knows which concrete identifier produced a detection.

A :class:`Detection` is the *raw* read off a card, before resolution (stage [4]) maps it to a
catalog ``cardId``: it carries the printed collector id **if legible** (``cardId``) else the
visible card ``name``, a foil flag, an optional bounding box, and the identifier's self-reported
``confidence``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ...contracts.observation import IdMethod

if TYPE_CHECKING:
    from ..frames import CandidateFrame


@dataclass(frozen=True)
class BBox:
    """Axis-aligned bounding box in normalised image coordinates (0..1)."""

    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Detection:
    """One card read off a frame, prior to catalog resolution.

    Exactly one of ``cardId`` (printed ``SET-NUMBER`` id, OCR'd when legible) or ``name`` (the
    visible card name, the fuzzy-match fallback) is normally populated; both may be ``None`` for a
    card the identifier saw but could not read. ``isFoil`` is ``None`` when foil status is
    indeterminate. ``confidence`` is the identifier's own 0..1 estimate.

    ``idMethod`` lets an identifier declare *how* it produced ``cardId`` -- Tier 2 (local CV) sets
    ``"embedding"`` since it matched art, not text; Tier 1 leaves it ``None`` and lets resolve
    classify the id hit as ``"ocr"`` or the name fallback as ``"name"``.

    ``godPack`` flags a card from a **god pack** -- a rare all-MR/Ruler variant whose cards are
    monochrome and have no reference art, so Tier 2 cannot art-match them. Such a card is detected
    visually (monochrome) but left unidentified (``cardId=None``) and counted separately rather than
    mistaken for an ordinary unresolved detection.
    """

    sourceId: str
    frameOrdinal: int
    cardId: str | None = None
    name: str | None = None
    isFoil: bool | None = None
    confidence: float = 0.0
    bbox: BBox | None = None
    idMethod: IdMethod | None = None
    godPack: bool = False


@runtime_checkable
class Identifier(Protocol):
    """A frame -> detections identifier. The Tier 1 <-> Tier 2 swap point."""

    def identify(self, frame: CandidateFrame) -> list[Detection]: ...
