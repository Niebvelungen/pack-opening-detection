"""Stage [4] -- Resolve (plan.md section 4).

Map each raw :class:`~.identify.base.Detection` to a catalog ``cardId`` and attach its
rarity/types/races from the :class:`~..contracts.catalog.CatalogIndex`:

1. **Exact id hit** -- the printed ``SET-NUMBER`` the identifier OCR'd is a key in ``index.byId``
   (``idMethod="ocr"``).
2. **Fuzzy name match** -- otherwise the visible name is matched with ``rapidfuzz``, **constrained
   to the source's set** (only names whose id is in ``index.bySet[set_code]``), accepting the best
   hit at or above ``score_cutoff`` (``idMethod="name"``).
3. **Unresolved** -- neither path lands; ``cardId`` stays ``None`` and the record is flagged for
   review (plan.md "flag, don't drop"). The unresolved *rate* is tracked, not the cards dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from ..contracts.catalog import CatalogIndex
from ..contracts.observation import IdMethod
from .identify.base import Detection

DEFAULT_SCORE_CUTOFF = 85.0

# Trailing id suffixes that mark the *same* physical card: ``J`` = the J-Ruler back of a Ruler,
# ``^``/``*`` = alternate-art printings (plan.md "Catalog parsing gotchas").
_SUFFIX = re.compile(r"[J^*]+$")


def base_card_id(card_id: str) -> str:
    """Strip ``J``/``^``/``*`` suffixes so a Ruler and its J-Ruler back collapse to one card."""
    return _SUFFIX.sub("", card_id)


@dataclass(frozen=True)
class ResolvedDetection:
    """A :class:`Detection` mapped to the catalog (or flagged unresolved).

    ``cardId`` is ``None`` when unresolved; ``idMethod`` records how it resolved (``"ocr"`` for an
    exact id hit, ``"name"`` for a fuzzy match) and ``matchScore`` is the 0..100 fuzzy score for
    name matches (``None`` for exact hits and unresolved). The original detection is kept so later
    stages can read foil/confidence/bbox and the source/frame it came from.
    """

    detection: Detection
    cardId: str | None = None
    rarity: str | None = None
    types: list[str] = field(default_factory=list)
    races: list[str] = field(default_factory=list)
    idMethod: IdMethod | None = None
    matchScore: float | None = None

    @property
    def resolved(self) -> bool:
        return self.cardId is not None


@dataclass(frozen=True)
class ResolveStats:
    """Tally of a resolve pass; surfaces the unresolved rate for QA gating."""

    total: int
    resolved: int

    @property
    def unresolved(self) -> int:
        return self.total - self.resolved

    @property
    def unresolved_rate(self) -> float:
        return self.unresolved / self.total if self.total else 0.0


def _attach(
    detection: Detection, card_id: str, index: CatalogIndex, method: IdMethod, score: float | None
) -> ResolvedDetection:
    card = index.byId[card_id]
    return ResolvedDetection(
        detection=detection,
        cardId=card_id,
        rarity=card.rarity,
        types=list(card.types),
        races=list(card.races),
        idMethod=method,
        matchScore=score,
    )


def resolve_detection(
    detection: Detection,
    index: CatalogIndex,
    set_code: str,
    *,
    score_cutoff: float = DEFAULT_SCORE_CUTOFF,
) -> ResolvedDetection:
    """Resolve one detection to a ``cardId`` (exact id -> fuzzy name -> unresolved)."""
    # 1. Exact id hit. Honour the identifier's own method (Tier 2 -> "embedding"); else it's OCR.
    card_id = detection.cardId
    if card_id is not None and card_id in index.byId:
        return _attach(detection, card_id, index, detection.idMethod or "ocr", None)

    # 2. Fuzzy name match, constrained to the source's set.
    if detection.name:
        choices = {
            cid: index.byId[cid].name for cid in index.bySet.get(set_code, []) if cid in index.byId
        }
        if choices:
            match = process.extractOne(
                detection.name, choices, scorer=fuzz.WRatio, score_cutoff=score_cutoff
            )
            if match is not None:
                _matched_name, score, matched_id = match
                return _attach(detection, matched_id, index, "name", float(score))

    # 3. Unresolved -- flagged, not dropped.
    return ResolvedDetection(detection=detection)


def resolve_detections(
    detections: list[Detection],
    index: CatalogIndex,
    set_code: str,
    *,
    score_cutoff: float = DEFAULT_SCORE_CUTOFF,
) -> tuple[list[ResolvedDetection], ResolveStats]:
    """Resolve a batch of detections; return the results and a :class:`ResolveStats`."""
    results = [resolve_detection(d, index, set_code, score_cutoff=score_cutoff) for d in detections]
    resolved = sum(1 for r in results if r.resolved)
    return results, ResolveStats(total=len(results), resolved=resolved)


def dedupe_consecutive(resolved: list[ResolvedDetection]) -> list[ResolvedDetection]:
    """Collapse maximal runs of the *same physical card* into one detection.

    A card lingers across many keyframes, and a double-faced Ruler shows both faces when flipped
    (front ``JRV-062`` and back ``JRV-062J`` share a :func:`base_card_id`). Consecutive resolved
    detections with the same ``(base id, foil)`` key fold to the single highest-confidence
    representative -- foil is part of the key so a card that is legitimately both the rare *and* the
    separate foil in one pack is not merged. Unresolved detections break runs and pass through.
    """
    out: list[ResolvedDetection] = []
    prev_key: tuple[str, bool] | None = None
    for r in resolved:
        if r.cardId is None:
            out.append(r)
            prev_key = None
            continue
        key = (base_card_id(r.cardId), bool(r.detection.isFoil))
        if key == prev_key and out:
            if r.detection.confidence > out[-1].detection.confidence:
                out[-1] = r
            continue
        out.append(r)
        prev_key = key
    return out
