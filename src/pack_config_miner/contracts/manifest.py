"""Footage Manifest contract (plan.md section 3.2).

Declares the footage to mine and which set each clip belongs to.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Capture = Literal["controlled", "uncontrolled"]


class FootageSource(BaseModel):
    """One clip to mine.

    ``capture`` drives pack grouping (stage [5]): ``controlled`` footage is one-pack-per-shot
    (grouping confidence 1.0); ``uncontrolled`` footage is grouped heuristically and flagged.
    """

    id: str
    setCode: str
    capture: Capture
    uri: str  # remote URL or local path
    packsExpected: int | None = None  # optional, aids grouping/QA
    notes: str = ""


class Manifest(BaseModel):
    sources: list[FootageSource] = Field(default_factory=list)
