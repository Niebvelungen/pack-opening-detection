"""Card Catalog contract (plan.md section 3.1) and the derived CatalogIndex.

The catalog is the source of truth for what cards exist. Its on-disk shape is nested
``root -> clusters[] -> sets[] -> cards[]`` keyed by an opaque game code (e.g. ``"fow"``).

On load the rest of the pipeline uses the flattened :class:`CatalogIndex`, built by
``pipeline.index`` (stage [0]).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ATK / DEF are an integer OR the empty string "" when not applicable (plan.md section 3.1).
# We normalise "" to None on load so downstream code sees a clean ``int | None``.
IntOrBlank = int | None


class CatalogCard(BaseModel):
    """One card entry. ``id`` is the primary key, format ``SET-NUMBER`` (e.g. ``CMF-001``)."""

    model_config = ConfigDict(extra="allow")  # carry passthrough fields we do not model

    id: str
    name: str
    type: list[str] = Field(default_factory=list)
    race: list[str] = Field(default_factory=list)
    colour: list[str] = Field(default_factory=list)
    rarity: str
    atk: IntOrBlank = Field(default=None, alias="ATK")
    def_: IntOrBlank = Field(default=None, alias="DEF")

    @field_validator("atk", "def_", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        """Accept ``int`` or the empty string ``""``; map ``""`` (or blank) to ``None``."""
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return None
            return int(v)
        return v

    @field_validator("type", "race", "colour", mode="before")
    @classmethod
    def _none_to_empty_list(cls, v: object) -> object:
        return [] if v is None else v


class CatalogSet(BaseModel):
    """A set: a ``code`` (e.g. ``CMF``) and its cards."""

    name: str = ""
    code: str
    cards: list[CatalogCard] = Field(default_factory=list)


class CatalogCluster(BaseModel):
    name: str = ""
    sets: list[CatalogSet] = Field(default_factory=list)


class CatalogGame(BaseModel):
    """Top-level value under each game key (e.g. ``"fow"``)."""

    clusters: list[CatalogCluster] = Field(default_factory=list)


# The catalog root is a mapping of game-code -> CatalogGame.
Catalog = dict[str, CatalogGame]


# ---------------------------------------------------------------------------
# Derived index (plan.md section 3.1)
# ---------------------------------------------------------------------------


class IndexedCard(BaseModel):
    """A flattened catalog entry, as stored in ``CatalogIndex.byId``."""

    setCode: str
    name: str
    rarity: str
    types: list[str] = Field(default_factory=list)
    races: list[str] = Field(default_factory=list)
    colours: list[str] = Field(default_factory=list)


class CatalogIndex(BaseModel):
    """Flattened lookup the pipeline uses after stage [0].

    ``byId`` maps collector id -> :class:`IndexedCard`. ``bySet`` lists ids per set code.
    ``raritiesBySet`` and ``typesBySet`` are per-set tallies/inventories used downstream.
    """

    byId: dict[str, IndexedCard] = Field(default_factory=dict)
    bySet: dict[str, list[str]] = Field(default_factory=dict)
    raritiesBySet: dict[str, dict[str, int]] = Field(default_factory=dict)
    typesBySet: dict[str, list[str]] = Field(default_factory=dict)
