"""Pack Configuration + Confidence Report contracts (plan.md section 3.5).

This is the deliverable. Its on-disk shape is awkward for a static model because **each lottery
slot is its own top-level key** (e.g. ``"BS"``, ``"R-SR-MR"``, ``"FOIL"``) mapping to a weighted
outcome list. We model the fixed parts as fields and hold the lottery slots in :attr:`lottery`,
then flatten/inflate to the on-disk shape via :meth:`to_config_dict` / :meth:`from_config_dict`.

Faithfully reproduced quirks (do NOT "fix" these; the consuming simulator depends on them):

* ``excludes`` / ``set_override`` entries are keyed by ``"rarity": <slotName>`` -- the key is
  literally ``rarity`` but its value is the **slot name**.
* ``chance`` values are integers and each lottery slot's outcomes sum to exactly 100.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Top-level keys that are NOT lottery slots (everything else at top level is a lottery slot).
_RESERVED_KEYS = frozenset({"packImage", "slots", "excludes", "set_override"})


class Condition(BaseModel):
    """An outcome condition: ``equals:true`` => card must match; ``equals:false`` => must not."""

    model_config = ConfigDict(extra="allow")

    equals: bool
    type: str | None = None
    races: list[str] | None = None
    cardIdPrefix: str | None = None
    setOverrides: list[str] | None = None


class Outcome(BaseModel):
    """One weighted outcome of a lottery slot. Pick with probability proportional to ``chance``,
    then pull a uniform card matching ``rarity`` (if present) and all ``conditions``."""

    chance: int
    rarity: str | None = None
    conditions: list[Condition] | None = None


class ExcludeEntry(BaseModel):
    """Pool exclusion. NOTE: ``rarity`` holds the **slot name**, not a rarity code (quirk)."""

    rarity: str  # slot name
    type: list[str] = Field(default_factory=list)


class SetOverrideEntry(BaseModel):
    """Draw a slot from other sets. NOTE: ``rarity`` holds the **slot name** (quirk)."""

    rarity: str  # slot name
    setCodes: list[str] = Field(default_factory=list)


class PackConfig(BaseModel):
    """A probabilistic description of one pack.

    Use :meth:`to_config_dict` to emit the on-disk JSON shape (lottery slots as top-level keys)
    and :meth:`from_config_dict` to parse it back.
    """

    packImage: str = ""
    slots: list[str] = Field(default_factory=list)
    excludes: list[ExcludeEntry] = Field(default_factory=list)
    set_override: list[SetOverrideEntry] = Field(default_factory=list)
    # slot name -> weighted outcomes. Flattened to top-level keys on serialization.
    lottery: dict[str, list[Outcome]] = Field(default_factory=dict)

    def to_config_dict(self) -> dict[str, Any]:
        """Serialize to the on-disk shape: fixed fields, then one key per lottery slot."""
        out: dict[str, Any] = {"packImage": self.packImage, "slots": list(self.slots)}
        if self.excludes:
            out["excludes"] = [e.model_dump() for e in self.excludes]
        if self.set_override:
            out["set_override"] = [s.model_dump() for s in self.set_override]
        for slot, outcomes in self.lottery.items():
            out[slot] = [o.model_dump(exclude_none=True) for o in outcomes]
        return out

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> PackConfig:
        """Parse the on-disk shape: split reserved keys from lottery-slot keys."""
        lottery = {
            key: [Outcome.model_validate(o) for o in value]
            for key, value in data.items()
            if key not in _RESERVED_KEYS
        }
        return cls(
            packImage=data.get("packImage", ""),
            slots=list(data.get("slots", [])),
            excludes=[ExcludeEntry.model_validate(e) for e in data.get("excludes", [])],
            set_override=[SetOverrideEntry.model_validate(s) for s in data.get("set_override", [])],
            lottery=lottery,
        )


# ---------------------------------------------------------------------------
# Confidence Report sidecar (plan.md section 3.5)
# ---------------------------------------------------------------------------

SlotStatus = Literal["ok", "needs_more_samples", "review"]


class OutcomeConfidence(BaseModel):
    label: str
    chance: int
    ci95: float  # half-width, in percentage points
    samples: int


class SlotConfidence(BaseModel):
    samples: int
    outcomes: list[OutcomeConfidence] = Field(default_factory=list)
    status: SlotStatus = "ok"


class ConfidenceReport(BaseModel):
    setCode: str
    packsObserved: int
    slots: dict[str, SlotConfidence] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
