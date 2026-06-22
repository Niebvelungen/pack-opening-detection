# Pack-Config Miner — Project Plan

A standalone pipeline that watches trading-card **pack-opening footage**, identifies every
card pulled, and **derives a probabilistic pack configuration** (the per-slot rarity/type
distribution) for each set — automatically replacing manual tallying.

The project is self-contained. It depends only on two inputs and produces one output, all
defined by the **data contracts** in §3:

- **In:** a *Card Catalog* (the `cards.json` schema) + *opening footage* (+ a small per-set *Pack Template*).
- **Out:** a *Pack Configuration* JSON per set, plus a confidence report.

There is **no coupling to any consumer application** — the miner only knows the contracts.

---

## 1. Why this is tractable

Generic card recognition is hard; this problem is the *easy* regime because:

1. **Closed-set identification.** Every card is in the catalog, so this is *retrieval against a
   known set*, not open-world recognition.
2. **Printed IDs.** Each card prints its collector ID (`SET-NUMBER`, e.g. `CMF-001`). When a
   frame is sharp enough to read it, OCR yields an **exact** match — no fuzzy vision needed.
3. **Free metadata.** Once the ID is known, rarity/type/race come straight from the catalog.

The decisive accuracy lever is **capture control**: cards filmed/scanned flat under steady
light identify at ~100%; uncontrolled third-party footage is messier (cuts, angles, glare,
occlusion) and mainly contributes aggregate frequencies.

---

## 2. Strategy

Two tiers; ship Tier 1, escalate only where it underperforms.

- **Tier 1 — Vision-LLM + OCR (primary).** Sample reveal frames, ask a vision model to read
  each card's printed ID (and flag foils); fall back to name matching. Fast to build,
  pay-per-call, good baseline recall.
- **Tier 2 — Local CV fallback (optional).** Card-rectangle detection + CNN embedding match
  against catalog art + OCR. No per-call cost, higher recall on messy footage. Only built for
  sets/sources where Tier 1 recall is insufficient.

**Per-slot conditional logic** (e.g. "the foil slot is 3% a Ruler") requires attributing each
identified card to a slot, which needs cards grouped *by pack*. Controlled captures give exact
per-pack grouping; uncontrolled footage is used to firm up the *marginal* ratios and is flagged
when grouping is uncertain.

---

## 3. Data contracts (authoritative)

All contracts are versioned and validated (e.g. `pydantic`) at every stage boundary.

### 3.1 INPUT — Card Catalog (`cards.json` schema)

The source of truth for what cards exist and their attributes. Nested
`root → clusters[] → sets[] → cards[]`.

```jsonc
{
  "fow": {
    "clusters": [
      {
        "name": "Grimm",
        "sets": [
          {
            "name": "Crimson Moon's Fairy Tale",
            "code": "CMF",
            "cards": [
              {
                "id": "CMF-001",              // collector ID, "SET-NUMBER"; primary key
                "name": "Aesop, the Prince's Tutor",
                "type": ["Resonator"],        // 0..n card-type strings
                "race": ["Human"],            // 0..n race strings
                "cost": "{W}{1}",             // mana-cost string (opaque)
                "colour": ["W"],              // 0..n colour codes (R/U/G/W/B/V)
                "ATK": 500,                    // integer OR "" when not applicable
                "DEF": 500,                    // integer OR "" when not applicable
                "abilities": ["..."],         // rules text lines
                "divinity": "",
                "flavour": "...",
                "artists": [],
                "rarity": "U"                  // rarity code: C/U/R/SR/MR/N/RR/JR/... 
              }
            ]
          }
        ]
      }
    ]
  }
}
```

**Field semantics**

| Field      | Type                 | Used by miner | Notes |
|------------|----------------------|:-------------:|-------|
| `id`       | string               | ✅ key        | Format `SET-NUMBER`; suffixes possible (`J`, `^`, `*`). |
| `rarity`   | string code          | ✅            | Outcome dimension. |
| `type`     | string[]             | ✅            | Condition dimension (e.g. Magic Stone, Ruler). |
| `race`     | string[]             | ✅            | Condition dimension. |
| `name`     | string               | ✅ fallback   | Fuzzy match when ID illegible. |
| `colour`   | string[]             | ⬜ passthrough | |
| `cost`,`ATK`,`DEF`,`abilities`,`divinity`,`flavour`,`artists` | mixed | ⬜ ignored | Carried only if a passthrough is needed. |

> ⚠️ `ATK`/`DEF` are `int` **or** the empty string `""`. Parsers must accept both.

**Derived index.** On load the miner flattens the catalog into a lookup the rest of the
pipeline uses:

```jsonc
// CatalogIndex
{
  "byId": {
    "CMF-001": { "setCode": "CMF", "name": "...", "rarity": "U",
                 "types": ["Resonator"], "races": ["Human"], "colours": ["W"] }
  },
  "bySet": { "CMF": ["CMF-001", "CMF-002", ...] },
  "raritiesBySet": { "CMF": { "C": 60, "U": 30, "R": 20, "SR": 10, "MR": 3 } },
  "typesBySet": { "CMF": ["Resonator", "Magic Stone", "Ruler", ...] }
}
```

### 3.2 INPUT — Footage Manifest

Declares the footage to mine and the set each clip belongs to.

```jsonc
{
  "sources": [
    {
      "id": "ttt-box-a",
      "setCode": "TTT",
      "capture": "controlled",          // "controlled" | "uncontrolled"
      "uri": "https://youtu.be/...",     // remote URL or local path
      "packsExpected": 36,               // optional, aids grouping/QA
      "notes": "flat layout, 1 pack per shot"
    }
  ]
}
```

### 3.3 INPUT — Pack Template (per set)

The **deterministic skeleton** of a pack: its ordered slots, which slots are fixed
(single-rarity, no sampling) vs lottery (need a derived distribution), and the predicates that
recognise which physical card fills each lottery slot.

```jsonc
{
  "setCode": "TTT",
  "packSize": 9,
  "layout": [
    { "slot": "C",        "kind": "fixed",   "count": 6 },   // emitted as bare-rarity slots
    { "slot": "BS",       "kind": "lottery", "count": 1 },
    { "slot": "R-SR-MR",  "kind": "lottery", "count": 1 },
    { "slot": "FOIL",     "kind": "lottery", "count": 1 }
  ],
  "attribution": {
    // How to assign an identified card in a pack to a lottery slot.
    // Evaluated top-to-bottom; first match wins. Remaining cards fall to fixed slots.
    "rules": [
      { "slot": "FOIL",      "match": { "isFoil": true } },
      { "slot": "BS",        "match": { "anyType": ["Magic Stone"] } },
      { "slot": "R-SR-MR",   "match": { "rarityIn": ["R", "SR", "MR"] } }
    ]
  }
}
```

> Fixed slots need **no** observation. The miner only derives distributions for lottery slots,
> which is the whole of the manual pain today.

### 3.4 INTERMEDIATE — Pack Observation

One record per grouped pack after identification + attribution. The audit trail behind every
derived number.

```jsonc
{
  "sourceId": "ttt-box-a",
  "setCode": "TTT",
  "packIndex": 12,
  "groupingConfidence": 0.97,          // 1.0 for controlled 1-pack-per-shot
  "cards": [
    { "cardId": "TTT-118", "rarity": "SR", "types": ["Resonator"], "races": ["Beast"],
      "isFoil": false, "assignedSlot": "R-SR-MR", "idMethod": "ocr", "confidence": 0.99 },
    { "cardId": "TTT-153", "rarity": "R", "types": ["Ruler"],
      "isFoil": true,  "assignedSlot": "FOIL",    "idMethod": "embedding", "confidence": 0.86 }
    // ...
  ],
  "unresolved": 0                      // detections that couldn't be identified
}
```

`idMethod ∈ {ocr, name, embedding}`. Low-confidence or unresolved items are flagged for review.

### 3.5 OUTPUT — Pack Configuration

The deliverable. A probabilistic description of one pack.

```jsonc
{
  "packImage": "",                     // optional opaque display ref; passthrough, not derived
  "slots": [                           // ordered; reveal order
    "C", "C", "C", "C", "C", "C",      // bare rarity token → uniform random card of that rarity
    "BS", "R-SR-MR", "FOIL"            // named token → a "lottery slot" defined below
  ],
  "excludes": [                        // optional pool exclusions, keyed by SLOT NAME
    { "rarity": "R-SR-MR", "type": ["Token"] }
  ],
  "set_override": [                    // optional: draw a slot from other sets, keyed by SLOT NAME
    { "rarity": "BS", "setCodes": ["CMF"] }
  ],

  // One key per lottery slot → weighted list of outcomes.
  "BS": [
    { "chance": 100, "rarity": "C", "conditions": [ { "equals": true, "type": "Magic Stone" } ] }
  ],
  "R-SR-MR": [
    { "chance": 5,  "rarity": "MR" },
    { "chance": 28, "rarity": "SR" },
    { "chance": 67, "rarity": "R",
      "conditions": [ { "equals": false, "type": "Ruler" },
                      { "equals": false, "type": "J-Ruler" } ] }
  ],
  "FOIL": [
    { "chance": 38, "rarity": "N" },
    { "chance": 39, "rarity": "R",
      "conditions": [ { "equals": false, "type": "Ruler" }, { "equals": false, "type": "J-Ruler" } ] },
    { "chance": 17, "rarity": "SR" },
    { "chance": 3,  "rarity": "MR" },
    { "chance": 3,  "rarity": "R", "conditions": [ { "equals": true, "type": "Ruler" } ] }
  ]
}
```

**Semantics (the consuming simulator's rules — documented so output is correct):**

- **`slots`** is an ordered list of tokens. A token **not** present as a top-level key is a
  *bare rarity code*: pull a uniform-random card of that rarity from the set. A token that
  **is** a top-level key is a *lottery slot*.
- A **lottery slot**'s value is a list of *outcomes*. Pick one outcome with probability
  proportional to its `chance` (integer weight), then pull a uniform-random card from the set
  matching the outcome's `rarity` (if present) **and all** its `conditions`.
- An outcome **`condition`** is `{ equals, type? | races? | cardIdPrefix? | setOverrides? }`.
  `equals:true` ⇒ the card *must* match; `equals:false` ⇒ must *not* match.
- **`set_override`** / **`excludes`** entries are keyed by `"rarity": <slotName>` — note the key
  is literally `rarity` but its value is the **slot name**. Faithfully reproduce this quirk.
- `chance` values are integers; by convention each lottery slot's outcomes sum to 100.

**Sidecar — Confidence Report** (emitted alongside each config):

```jsonc
{
  "setCode": "TTT",
  "packsObserved": 214,
  "slots": {
    "R-SR-MR": {
      "samples": 214,
      "outcomes": [
        { "label": "MR",            "chance": 5,  "ci95": 2.9, "samples": 11 },
        { "label": "SR",            "chance": 28, "ci95": 6.0, "samples": 60 },
        { "label": "R (non-Ruler)", "chance": 67, "ci95": 6.3, "samples": 143 }
      ],
      "status": "ok"                 // "ok" | "needs_more_samples" | "review"
    }
  },
  "flags": [ "FOIL slot: 41 packs only — MR estimate ±5.3%, sample more" ]
}
```

---

## 4. Pipeline architecture

```
Catalog ─┐
         ▼
[0] Load & index ──► CatalogIndex
                          │
Manifest ─► [1] Ingest ─► media files
                          ▼
              [2] Frame sampling ─► candidate frames
                          ▼
              [3] Vision-LLM / OCR ─► raw detections (id|name, foil?, bbox, conf)
                          ▼
              [4] Resolve ─(CatalogIndex)─► identified cards (cardId + metadata)
                          ▼
              [5] Pack grouping ─► packs
                          ▼
              [6] Slot attribution ─(Pack Template)─► Pack Observations
                          ▼
              [7] Aggregate & emit ─► Pack Configuration + Confidence Report
```

**[0] Load & index.** Parse catalog → `CatalogIndex` (§3.1). Validate.

**[1] Ingest.** Resolve each manifest source: download remote video (`yt-dlp`) or read local
file; normalise container/fps.

**[2] Frame sampling.** Scene-change detection (`ffmpeg`/PyAV) to extract "reveal" frames where
cards are laid out, deduplicating near-identical frames. Controlled 1-pack-per-shot footage
yields ~one keyframe per pack.

**[3] Identification (Tier 1).** For each frame, call the vision model behind a
**provider-agnostic interface** (`identify(frame) -> Detection[]`). Prompt: enumerate every
card; return printed `SET-NUMBER` ID if legible, else the visible name; flag holo/foil; give a
bbox + self-reported confidence. (Tier 2 swaps this stage for the local CV detector+embedder.)

**[4] Resolve.** Map each detection to a `cardId`:
1. exact ID hit in `CatalogIndex.byId`;
2. else fuzzy name match (`rapidfuzz`) **constrained to the source's `setCode`**;
3. else mark unresolved.
Attach rarity/types/races from the index.

**[5] Pack grouping.** `controlled` → one shot = one pack (confidence 1.0). `uncontrolled` →
group consecutive detections into runs of `packSize`, or split on scene boundaries; emit
`groupingConfidence < 1` and flag.

**[6] Slot attribution.** For each pack, run the Pack Template's `attribution.rules`
(first-match-wins) to tag each card with its `assignedSlot`; leftovers fill fixed slots.
Validate the pack against the template (right counts per slot) — mismatches are flagged, not
silently dropped.

**[7] Aggregate & emit.** Across all observations, tally per-slot outcomes, fold identical
`(rarity, conditions)` signatures together, normalise to integer chances, compute CIs, and
write the Pack Configuration + Confidence Report.

---

## 5. Slot-attribution & outcome-signature logic

The bridge from "cards we saw" to "per-slot distribution":

1. **Assign** each card to a slot via template rules (§3.3).
2. **Signature** each lottery-slot card by the *minimal* outcome descriptor: its `rarity` plus
   any conditions implied by its type/race that the template marks as distinguishing
   (e.g. Ruler vs non-Ruler in a foil slot). Two cards with the same signature are the same
   outcome.
3. **Tally** signatures per slot across all packs.
4. **Emit** one outcome object per distinct signature with the normalised `chance`.

Conditional splits (the hard requirement) emerge naturally: if 3% of foil-slot cards are
Rulers, that surfaces as a distinct `{rarity:R, conditions:[{equals:true,type:Ruler}]}` outcome.

---

## 6. Statistics

- **Chance normalisation.** Counts → integer percentages via **largest-remainder** so each
  slot sums to exactly 100.
- **Confidence interval.** Per outcome, 95% CI on the proportion (Wilson interval; falls back
  to normal approx for large n). Reported as `± half-width %`.
- **Minimum-sample guidance.** Flag any slot whose rarest non-zero outcome has CI half-width
  above a threshold (e.g. > 5%), with a "sample N more packs" hint derived from the target
  margin.
- **Zero handling.** Outcomes never observed are omitted; outcomes observed once are kept but
  flagged as low-confidence (could be noise or a genuine rare hit).

---

## 7. Quality / human-in-the-loop

- Every derived number is traceable to its Pack Observations (§3.4).
- A **review queue** collects: unresolved detections, low-confidence IDs, packs failing
  template validation, and slots below the sample threshold.
- Output is a **draft** config + report; a thin review UI (or a CSV/JSON the maintainer edits)
  confirms or patches flagged slots before the config is considered final.

---

## 8. Tech stack

| Concern            | Choice |
|--------------------|--------|
| Language           | Python 3.12+ |
| Contracts/validation | `pydantic` v2 |
| Video download     | `yt-dlp` |
| Frame extraction   | `ffmpeg` (CLI) / `PyAV` |
| Vision (Tier 1)    | Provider-agnostic vision-LLM client (vision + OCR) behind one interface |
| Fuzzy matching     | `rapidfuzz` |
| CV fallback (Tier 2) | `opencv-python`, an embedding model (CLIP/DINOv2), a vector index (`faiss`) |
| CLI / orchestration | `typer` |
| Tests              | `pytest` + recorded fixtures (sample frames, golden configs) |

The vision provider sits behind `interfaces/Identifier` so Tier 1 ↔ Tier 2 is a swap, and no
stage knows the concrete model.

---

## 9. Suggested project structure

```
pack-config-miner/
├── plan.md
├── pyproject.toml
├── contracts/                 # pydantic models — the §3 schemas, the only "API"
│   ├── catalog.py
│   ├── manifest.py
│   ├── template.py
│   ├── observation.py
│   └── pack_config.py
├── pipeline/
│   ├── index.py               # [0]
│   ├── ingest.py              # [1]
│   ├── frames.py              # [2]
│   ├── identify/              # [3]
│   │   ├── base.py            #   Identifier interface
│   │   ├── vision_llm.py      #   Tier 1
│   │   └── local_cv.py        #   Tier 2 (later)
│   ├── resolve.py             # [4]
│   ├── group.py               # [5]
│   ├── attribute.py           # [6]
│   └── aggregate.py           # [7]
├── templates/                 # one Pack Template per set (§3.3)
├── data/                      # catalog.json, manifests, downloaded media (gitignored)
├── out/                       # derived configs + confidence reports
└── tests/
```

---

## 10. Milestones

| # | Deliverable | Exit criteria |
|---|-------------|---------------|
| M0 | Contracts + catalog index | All §3 schemas as validated models; catalog loads to `CatalogIndex`. |
| M1 | Ingest + frame sampling | Manifest → keyframes for one controlled clip. |
| M2 | Vision identify (Tier 1) | Frames → detections with IDs/foil flags on a sample clip. |
| M3 | Resolve | Detections → `cardId` + metadata; unresolved rate measured. |
| M4 | Group + attribute | Controlled clip → Pack Observations passing template validation. |
| M5 | Aggregate + emit | End-to-end: one set → Pack Configuration + Confidence Report. |
| M6 | Confidence + review | Flags, sample-size guidance, review queue. |
| M7 | Tier-2 CV fallback | Local detector+embedder for sources where Tier 1 recall is low. |

A vertical slice (M0→M5) on **one controlled set** proves the whole contract before scaling.

---

## 11. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Uncontrolled footage: low recall / bad pack grouping | Lean on controlled captures for conditional splits; use uncontrolled only for marginal ratios; flag low grouping confidence. |
| Printed IDs unreadable (compression/blur) | Name-match fallback, then embedding match (Tier 2); aggregate across many frames. |
| Foil vs non-foil ambiguity | Ask vision model explicitly; for controlled capture adopt a reveal convention; let template attribution encode it. |
| Misidentification poisons ratios | Confidence thresholds; template count-validation per pack; human review of flags. |
| Small sample sizes for rare slots | CI reporting + explicit "sample more" gating before a config is final. |
| Vision provider/model drift | Provider-agnostic `Identifier` interface; golden-fixture regression tests. |

---

## 12. Open questions

1. Foil detectability in target footage — does the vision model reliably flag holo, or is a
   capture convention required?
2. Are there sets whose pack structure isn't a simple fixed skeleton (variable size, guaranteed
   box-level pulls) that the Pack Template must express?
3. Acceptable per-call vision cost vs. frame-sampling density (accuracy/cost trade-off).
4. Catalog refresh cadence — how new-set IDs enter the catalog the miner reads.
