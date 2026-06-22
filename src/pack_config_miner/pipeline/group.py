"""Stage [5] -- Pack grouping (plan.md section 4).

Turn a flat stream of resolved detections into packs. ``controlled`` one-pack-per-shot footage
groups by keyframe (one reveal frame == one pack, ``groupingConfidence`` 1.0). ``uncontrolled``
footage is grouped heuristically into consecutive runs of ``packSize`` and flagged with a
sub-1.0 confidence -- never silently dropped.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from ..contracts.manifest import Capture
from .resolve import ResolvedDetection

# Heuristic confidence for an uncontrolled run that lands exactly packSize cards.
UNCONTROLLED_CONFIDENCE = 0.6


@dataclass(frozen=True)
class PackGroup:
    """A candidate pack: the resolved detections believed to belong to one opened pack."""

    sourceId: str
    setCode: str
    packIndex: int
    groupingConfidence: float
    resolved: list[ResolvedDetection]
    flags: list[str] = field(default_factory=list)


def _group_controlled(resolved: list[ResolvedDetection], set_code: str) -> list[PackGroup]:
    """One keyframe == one pack (confidence 1.0)."""
    by_frame: OrderedDict[int, list[ResolvedDetection]] = OrderedDict()
    for r in resolved:
        by_frame.setdefault(r.detection.frameOrdinal, []).append(r)
    packs: list[PackGroup] = []
    for index, (_ordinal, members) in enumerate(sorted(by_frame.items())):
        packs.append(
            PackGroup(
                sourceId=members[0].detection.sourceId,
                setCode=set_code,
                packIndex=index,
                groupingConfidence=1.0,
                resolved=members,
            )
        )
    return packs


def _pack(
    members: list[ResolvedDetection], set_code: str, index: int, flags: list[str]
) -> PackGroup:
    return PackGroup(
        sourceId=members[0].detection.sourceId,
        setCode=set_code,
        packIndex=index,
        groupingConfidence=UNCONTROLLED_CONFIDENCE,
        resolved=members,
        flags=flags,
    )


def _group_by_boundary(resolved: list[ResolvedDetection], set_code: str) -> list[PackGroup]:
    """Split on the reveal pattern: a pack ends at its foil, the next starts at the next N card.

    Cards between a foil and the next N (back-of-card flips, table chatter) are dropped as
    transitions. Used for ``uncontrolled`` footage once foils are being detected (the hybrid tier).
    """
    packs: list[PackGroup] = []
    current: list[ResolvedDetection] = []
    waiting = False  # past a foil, waiting for the next N to open a new pack
    for r in resolved:
        if waiting:
            if r.rarity == "N":
                current = [r]
                waiting = False
            continue
        current.append(r)
        if bool(r.detection.isFoil):
            packs.append(_pack(current, set_code, len(packs), ["uncontrolled: foil/N boundary"]))
            current = []
            waiting = True
    if current:
        packs.append(_pack(current, set_code, len(packs), ["uncontrolled: trailing partial pack"]))
    return packs


def _group_by_chunks(
    resolved: list[ResolvedDetection], set_code: str, pack_size: int
) -> list[PackGroup]:
    """Fallback when no foils are detected: chunk the stream into runs of ``pack_size``."""
    if pack_size < 1:
        raise ValueError("pack_size must be >= 1")
    packs: list[PackGroup] = []
    for index, start in enumerate(range(0, len(resolved), pack_size)):
        chunk = resolved[start : start + pack_size]
        flags = ["uncontrolled grouping: heuristic run of packSize"]
        if len(chunk) != pack_size:
            flags.append(f"partial pack: {len(chunk)}/{pack_size} cards")
        packs.append(_pack(chunk, set_code, index, flags))
    return packs


def group_packs(
    resolved: list[ResolvedDetection],
    *,
    set_code: str,
    capture: Capture,
    pack_size: int,
) -> list[PackGroup]:
    """Group one source's resolved detections into packs by its ``capture`` mode.

    ``uncontrolled`` footage uses the foil/N reveal boundary when any foil is present, else falls
    back to fixed ``pack_size`` chunks.
    """
    if capture == "controlled":
        return _group_controlled(resolved, set_code)
    if any(bool(r.detection.isFoil) for r in resolved):
        return _group_by_boundary(resolved, set_code)
    return _group_by_chunks(resolved, set_code, pack_size)
