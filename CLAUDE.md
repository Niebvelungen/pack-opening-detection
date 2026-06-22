# CLAUDE.md

Guidance for working in this repository.

## What this is

**Pack-Config Miner** — a pipeline that mines trading-card pack-opening footage and derives a
probabilistic **Pack Configuration** (per-slot rarity/type distribution) per set. See
[plan.md](plan.md) for the authoritative design and [implementation-plan.md](implementation-plan.md)
for the build sequence and current status.

## Core principle: contracts are the API

The §3 data contracts in [plan.md](plan.md) are authoritative. Every stage boundary is a
versioned, **`pydantic`-validated** contract. The pipeline stages know *only* the contracts —
there is **no coupling to any consumer application**.

The contracts (in dependency order):
- **Card Catalog** (`cards.json`) → flattened to a **CatalogIndex** on load (§3.1)
- **Footage Manifest** (§3.2) — which clips, which set, controlled vs uncontrolled
- **Pack Template** (§3.3) — per-set slot skeleton + attribution rules
- **Pack Observation** (§3.4) — one record per grouped pack (the audit trail)
- **Pack Configuration** + **Confidence Report** (§3.5) — the deliverable

When changing a contract, update the `pydantic` model, the §3 doc, and any golden fixtures together.

## Output-format quirks (reproduce faithfully — do not "fix")

The Pack Configuration is consumed by an external simulator with specific rules (§3.5):

- `slots` is an ordered token list. A token **not** a top-level key = a *bare rarity code*
  (uniform-random card of that rarity). A token that **is** a top-level key = a *lottery slot*.
- A lottery slot's value is a weighted list of *outcomes*; pick one with probability ∝ `chance`
  (integer), then pull a uniform card matching its `rarity` and **all** `conditions`.
- A `condition` is `{ equals, type? | races? | cardIdPrefix? | setOverrides? }`;
  `equals:true` = must match, `equals:false` = must not.
- ⚠️ `set_override` / `excludes` entries are keyed by `"rarity": <slotName>` — the key is
  literally `rarity` but its value is the **slot name**. **Reproduce this quirk faithfully.**
- `chance` values are integers; each lottery slot's outcomes sum to **exactly 100**
  (largest-remainder rounding).

## Card-art assets

Reference card images (Force of Will) are a flat `{cardId: url}` map keyed by collector id:
`https://raw.githubusercontent.com/Niebvelungen/TCG-Arena-FoW/refs/heads/main/image_cache.json`.
Download via `pack-miner fetch-art --cache <path|url> [--set CMF] [--limit N]` (module:
`assets.py`). Art + `image_cache.json` live under gitignored `data/`. Used for the Tier 2
embedding index (M7) and for compositing synthetic test frames for the vertical slice (M1–M5).
The printed `SET-NUMBER` id is legible on the scans (Tier 1 OCR premise holds).

## Catalog parsing gotchas

- `id` format is `SET-NUMBER` (e.g. `CMF-001`); suffixes possible (`J`, `^`, `*`).
- ⚠️ `ATK`/`DEF` are `int` **or** the empty string `""`. Parsers must accept both.
- `type`, `race`, `colour` are `0..n`-length string arrays.

## Tech stack & conventions

- **Python 3.12+**, `pydantic` v2, `typer` CLI, `pytest` + recorded fixtures.
- Identification sits behind a provider-agnostic `identify(frame) -> Detection[]` interface
  (`pipeline/identify/base.py`). **Tier 2 (local CV art match) is now the primary/default**
  (`--tier cv`); Tier 1 (vision-LLM) is optional (`--tier vision`); `hybrid` runs Tier 2 then
  escalates unmatched frames (foils) to Tier 1. Measured: Tier 1 recall on uncontrolled footage
  was insufficient (and Sonnet hallucinated ids), so M7 Tier 2 was built and promoted. No stage
  knows the concrete model.
- **LLM provider:** default to the latest Claude models (Opus 4.8 / Sonnet 4.6) via the
  Anthropic API. Before writing or changing any vision-LLM / Anthropic client code, consult the
  `claude-api` skill for current model IDs, pricing, and the vision/tool-use API — do not rely
  on memory.
- Statistics: largest-remainder for integer `chance`; Wilson interval for 95% CIs (§6).

## Project layout

`contracts/` (pydantic models) · `pipeline/` (stages [0]–[7]) · `templates/` (per-set) ·
`data/` (catalog, manifests, media — **gitignored**) · `out/` (derived configs — gitignored) ·
`tests/`.

## Working norms

- **Vertical slice first** (history): M0→M5 was proven on a synthetic golden set, then breadth
  followed. Tier 2 was built once Tier 1 recall on real uncontrolled footage was measured as
  insufficient — that gate is now passed; Tier 2 is the default identifier.
- **Flag, don't drop.** Packs failing template validation, low-confidence IDs, and
  under-sampled slots go to a review queue — never silently discarded.
- Run tests with `pytest`. Validate contracts at every stage boundary.
- This is a Windows dev box; the primary shell is PowerShell. A Bash tool is also available.
