"""Stage [0] -- Load & index (plan.md section 4).

Parse a Card Catalog into the flattened :class:`CatalogIndex` the rest of the pipeline uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from ..contracts.catalog import (
    Catalog,
    CatalogGame,
    CatalogIndex,
    IndexedCard,
)

_CATALOG_ADAPTER: TypeAdapter[dict[str, CatalogGame]] = TypeAdapter(Catalog)


def load_catalog(path: str | Path) -> Catalog:
    """Read and validate a ``cards.json`` file into the nested catalog model."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _CATALOG_ADAPTER.validate_python(raw)


def build_index(catalog: Catalog) -> CatalogIndex:
    """Flatten a validated catalog into a :class:`CatalogIndex`.

    Builds ``byId`` (collector id -> card metadata), ``bySet`` (ordered ids per set),
    ``raritiesBySet`` (per-set rarity counts), and ``typesBySet`` (per-set distinct types).
    """
    index = CatalogIndex()
    for game in catalog.values():
        for cluster in game.clusters:
            for cset in cluster.sets:
                code = cset.code
                ids = index.bySet.setdefault(code, [])
                rarities = index.raritiesBySet.setdefault(code, {})
                # Preserve first-seen order of types while de-duplicating.
                types_seen = index.typesBySet.setdefault(code, [])
                for card in cset.cards:
                    index.byId[card.id] = IndexedCard(
                        setCode=code,
                        name=card.name,
                        rarity=card.rarity,
                        types=card.type,
                        races=card.race,
                        colours=card.colour,
                    )
                    ids.append(card.id)
                    rarities[card.rarity] = rarities.get(card.rarity, 0) + 1
                    for t in card.type:
                        if t not in types_seen:
                            types_seen.append(t)
    return index


def load_index(path: str | Path) -> CatalogIndex:
    """Convenience: load a catalog file and return its :class:`CatalogIndex`."""
    return build_index(load_catalog(path))
