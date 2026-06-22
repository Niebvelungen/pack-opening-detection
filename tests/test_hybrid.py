"""Tests for the hybrid identifier: Tier 2 primary, Tier 1 fallback, god packs skipped.

Uses tiny stub identifiers (no cv/vision extras needed) to exercise the escalation logic.
"""

from __future__ import annotations

from pack_config_miner.pipeline.frames import CandidateFrame
from pack_config_miner.pipeline.identify.base import Detection, Identifier
from pack_config_miner.pipeline.identify.hybrid import HybridIdentifier


class _Stub:
    """A stub Identifier that returns a preset list and records whether it was called."""

    def __init__(self, dets: list[Detection]) -> None:
        self.dets = dets
        self.called = False

    def identify(self, frame: CandidateFrame) -> list[Detection]:
        self.called = True
        return self.dets


_FRAME = CandidateFrame(source_id="s1", ordinal=0, frame_index=0, timestamp=0.0, path=None)


def test_hybrid_is_an_identifier() -> None:
    h = HybridIdentifier(_Stub([]), _Stub([]))
    assert isinstance(h, Identifier)


def test_primary_match_is_trusted_without_fallback() -> None:
    primary = _Stub(
        [Detection(sourceId="s1", frameOrdinal=0, cardId="JRV-001", idMethod="embedding")]
    )
    fallback = _Stub([Detection(sourceId="s1", frameOrdinal=0, cardId="WRONG")])
    out = HybridIdentifier(primary, fallback).identify(_FRAME)
    assert [d.cardId for d in out] == ["JRV-001"]
    assert fallback.called is False  # no paid call when Tier 2 already matched


def test_empty_primary_escalates_to_fallback_for_foils() -> None:
    primary = _Stub([])  # Tier 2 saw nothing (a foil with different framing)
    fallback = _Stub(
        [Detection(sourceId="s1", frameOrdinal=0, cardId="JRV-070", isFoil=True, idMethod="name")]
    )
    out = HybridIdentifier(primary, fallback).identify(_FRAME)
    assert fallback.called is True
    assert out[0].cardId == "JRV-070" and out[0].isFoil is True


def test_god_pack_is_skipped_not_escalated() -> None:
    primary = _Stub([Detection(sourceId="s1", frameOrdinal=0, godPack=True)])
    fallback = _Stub([Detection(sourceId="s1", frameOrdinal=0, cardId="X")])
    out = HybridIdentifier(primary, fallback).identify(_FRAME)
    assert out == []  # god pack dropped
    assert fallback.called is False  # and not sent to the paid Tier 1


def test_god_pack_escalates_when_skip_disabled() -> None:
    primary = _Stub([Detection(sourceId="s1", frameOrdinal=0, godPack=True)])
    fallback = _Stub([Detection(sourceId="s1", frameOrdinal=0, cardId="JRV-005")])
    out = HybridIdentifier(primary, fallback, skip_god_packs=False).identify(_FRAME)
    assert fallback.called is True
    assert out[0].cardId == "JRV-005"
