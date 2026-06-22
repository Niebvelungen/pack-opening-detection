"""Stage [3] -- Hybrid identifier: Tier 2 primary, Tier 1 fallback.

Tier 2 (local art match) is free, fast, and can't hallucinate, so it leads. But it can't read two
things: **foils** (different reveal framing / no foil art) and **god-pack** cards (monochrome, no
art at all). This combines the two seams behind one :class:`~.base.Identifier`:

* If Tier 2 art-matches a card, trust it (free).
* If Tier 2 only flagged a monochrome **god pack**, skip the frame (god packs are excluded from the
  config -- see plan.md §12).
* Otherwise Tier 2 saw nothing it could place (a foil, an odd angle): fall back to Tier 1 vision,
  which reads the printed id / name **and the foil flag**.

Escalation is per *frame* and only when Tier 2 comes up empty, so the paid Tier 1 calls are limited
to the cards Tier 2 genuinely can't handle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Detection, Identifier

if TYPE_CHECKING:
    from ..frames import CandidateFrame


class HybridIdentifier:
    """Tier 2 ``primary``; escalate empty/foil frames to Tier 1 ``fallback``; skip god packs."""

    def __init__(
        self, primary: Identifier, fallback: Identifier, *, skip_god_packs: bool = True
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.skip_god_packs = skip_god_packs

    def identify(self, frame: CandidateFrame) -> list[Detection]:
        primary = self.primary.identify(frame)
        matched = [d for d in primary if d.cardId is not None]
        if matched:
            return matched  # Tier 2 art-matched -> trust it, no paid call
        if self.skip_god_packs and any(d.godPack for d in primary):
            return []  # monochrome god pack -> skip (excluded from the config)
        return self.fallback.identify(frame)  # foil / odd framing -> Tier 1 reads id + foil
