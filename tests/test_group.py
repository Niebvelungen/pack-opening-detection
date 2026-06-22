"""Tests for stage [5] grouping: controlled (one frame = one pack) vs uncontrolled (runs)."""

from __future__ import annotations

from pack_config_miner.pipeline.group import UNCONTROLLED_CONFIDENCE, group_packs
from pack_config_miner.pipeline.identify.base import Detection
from pack_config_miner.pipeline.resolve import ResolvedDetection


def _rd(frame: int, source: str = "s1") -> ResolvedDetection:
    det = Detection(sourceId=source, frameOrdinal=frame, cardId="X-1")
    return ResolvedDetection(detection=det, cardId="X-1", rarity="C", idMethod="ocr")


def _card(rarity: str, *, foil: bool = False, source: str = "s1") -> ResolvedDetection:
    det = Detection(sourceId=source, frameOrdinal=0, cardId="X", isFoil=foil)
    return ResolvedDetection(detection=det, cardId="X", rarity=rarity, idMethod="embedding")


def test_controlled_groups_one_pack_per_frame() -> None:
    resolved = [_rd(0), _rd(0), _rd(1), _rd(2), _rd(2), _rd(2)]
    packs = group_packs(resolved, set_code="GLD", capture="controlled", pack_size=9)
    assert [p.packIndex for p in packs] == [0, 1, 2]
    assert [len(p.resolved) for p in packs] == [2, 1, 3]
    assert all(p.groupingConfidence == 1.0 for p in packs)
    assert all(not p.flags for p in packs)
    assert all(p.setCode == "GLD" for p in packs)


def test_uncontrolled_chunks_by_pack_size_and_flags() -> None:
    resolved = [_rd(i) for i in range(7)]
    packs = group_packs(resolved, set_code="GLD", capture="uncontrolled", pack_size=3)
    assert [len(p.resolved) for p in packs] == [3, 3, 1]
    assert all(p.groupingConfidence == UNCONTROLLED_CONFIDENCE for p in packs)
    # Every uncontrolled pack is flagged; the short final run is flagged again as partial.
    assert all(p.flags for p in packs)
    assert any("partial pack: 1/3" in f for f in packs[-1].flags)


def test_controlled_empty_input() -> None:
    assert group_packs([], set_code="GLD", capture="controlled", pack_size=9) == []


def test_uncontrolled_uses_foil_boundary_when_foils_present() -> None:
    # Two packs: N... ending in a foil, then N... ending in a foil.
    resolved = [
        _card("N"),
        _card("N"),
        _card("R", foil=True),  # foil ends pack 0
        _card("N"),  # next N opens pack 1
        _card("N"),
        _card("SR", foil=True),  # foil ends pack 1
    ]
    packs = group_packs(resolved, set_code="JRV", capture="uncontrolled", pack_size=10)
    assert [len(p.resolved) for p in packs] == [3, 3]
    assert all(p.groupingConfidence < 1.0 for p in packs)
    assert all(any("boundary" in f for f in p.flags) for p in packs)


def test_uncontrolled_drops_transitions_between_foil_and_next_n() -> None:
    # A back-of-card / chatter detection between the foil and the next pack's first N is dropped.
    resolved = [
        _card("N"),
        _card("R", foil=True),  # foil ends pack 0
        _card("SR"),  # transition (not an N) -> dropped
        _card("N"),  # pack 1 opens
        _card("R", foil=True),
    ]
    packs = group_packs(resolved, set_code="JRV", capture="uncontrolled", pack_size=10)
    assert [len(p.resolved) for p in packs] == [2, 2]


def test_uncontrolled_falls_back_to_chunks_without_foils() -> None:
    resolved = [_rd(i) for i in range(7)]  # all commons, no foils
    packs = group_packs(resolved, set_code="GLD", capture="uncontrolled", pack_size=3)
    assert [len(p.resolved) for p in packs] == [3, 3, 1]
