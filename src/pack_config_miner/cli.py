"""``pack-miner`` command-line entry point.

Wires the pipeline stages [0]→[7]. Subcommands are stubbed during Phase 0 / M0 and filled in
as each milestone lands (the ``run`` command completes at M5).
"""

from __future__ import annotations

import typer

from . import __version__

app = typer.Typer(
    name="pack-miner",
    help="Mine pack-opening footage into probabilistic per-set pack configurations.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def run(
    catalog: str = typer.Option(..., "--catalog", help="Path to the Card Catalog (cards.json)."),
    manifest: str = typer.Option(..., "--manifest", help="Path to the Footage Manifest."),
    template: str = typer.Option(..., "--template", help="Path to the per-set Pack Template."),
    out: str = typer.Option("out", "--out", help="Output directory for config + report."),
    tier: str = typer.Option(
        "cv",
        "--tier",
        help="Identifier: 'cv' (default), 'vision' (Tier 1), or 'hybrid' (cv+vision for foils).",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Vision model id for vision/hybrid tiers (e.g. claude-sonnet-4-6)."
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Vision reasoning effort: low|medium|high|xhigh|max."
    ),
    art_root: str = typer.Option(
        "data/art", "--art-root", help="Reference-art dir for Tier 2 (the default tier)."
    ),
    max_hits: int = typer.Option(
        1, "--max-hits", help="Table-bleed cap: max times a rare counts as a hit (e.g. 2 per box)."
    ),
) -> None:
    """Run the full pipeline for one set (catalog -> Pack Configuration + Confidence Report).

    Ingests + samples footage, identifies cards (default ``--tier cv`` = Tier 2 local art match,
    needs the cv extra + downloaded art; ``--tier vision`` = optional Tier 1 LLM, needs vision +
    ANTHROPIC_API_KEY), then resolves, groups, attributes, aggregates, and writes
    config.json / report.json / review.json into ``--out``.
    """
    from .pipeline.run import run_pipeline, write_outputs

    outputs = run_pipeline(
        catalog,
        manifest,
        template,
        tier=tier,
        model=model,
        effort=effort,
        art_root=art_root,
        max_hits_per_card=max_hits,
    )
    paths = write_outputs(outputs, out)
    n_packs = outputs.report.packsObserved
    rate = outputs.resolve_stats.unresolved_rate if outputs.resolve_stats else 0.0
    god = f", {outputs.god_pack_cards} god-pack card(s)" if outputs.god_pack_cards else ""
    typer.echo(
        f"set {outputs.report.setCode}: {n_packs} pack(s), "
        f"unresolved {rate:.0%}, {len(outputs.review)} review item(s){god}"
    )
    for name, path in paths.items():
        typer.echo(f"  {name}: {path}")


@app.command()
def ingest(
    manifest: str = typer.Option(..., "--manifest", help="Path to the Footage Manifest."),
    cache: str = typer.Option("data/media", "--cache", help="Directory to cache downloads in."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-download cached remote clips."),
) -> None:
    """Resolve a manifest's footage sources to local media files (stage [1])."""
    from .pipeline.ingest import ingest_manifest, load_manifest

    clips = ingest_manifest(load_manifest(manifest), cache, overwrite=overwrite)
    for clip in clips:
        typer.echo(f"{clip.source_id}\t{clip.set_code}\t{clip.capture}\t{clip.path}")
    typer.echo(f"resolved {len(clips)} source(s)")


@app.command(name="sample-frames")
def sample_frames_cmd(
    manifest: str = typer.Option(..., "--manifest", help="Path to the Footage Manifest."),
    cache: str = typer.Option("data/media", "--cache", help="Directory to cache downloads in."),
    out: str = typer.Option("data/frames", "--out", help="Directory to write keyframes into."),
    threshold: float = typer.Option(
        12.0, "--threshold", help="Min signature distance for a new keyframe (higher = fewer)."
    ),
    sample_every: int = typer.Option(
        1, "--sample-every", help="Decode every Nth frame before selecting (speed/coverage)."
    ),
) -> None:
    """Ingest a manifest and extract scene-change keyframes per source (stages [1]+[2])."""
    from .pipeline.frames import sample_keyframes
    from .pipeline.ingest import ingest_manifest, load_manifest

    clips = ingest_manifest(load_manifest(manifest), cache)
    total = 0
    for clip in clips:
        frames = sample_keyframes(clip, out, threshold=threshold, sample_every=sample_every)
        total += len(frames)
        typer.echo(f"{clip.source_id}: {len(frames)} keyframe(s)")
    typer.echo(f"extracted {total} keyframe(s) into {out}")


@app.command()
def identify(
    image: str = typer.Option(..., "--image", help="Path to a keyframe image to identify."),
    source: str = typer.Option("frame", "--source", help="Source id to stamp on detections."),
    model: str | None = typer.Option(None, "--model", help="Override the vision model id."),
    effort: str | None = typer.Option(
        None, "--effort", help="Reasoning effort: low|medium|high|xhigh|max."
    ),
) -> None:
    """Run the Tier 1 vision identifier on one keyframe (stage [3]). Needs ANTHROPIC_API_KEY."""
    from pathlib import Path

    from .pipeline.frames import CandidateFrame
    from .pipeline.identify.vision_llm import VisionConfig, VisionLLMIdentifier

    cfg = VisionConfig()
    if model is not None:
        cfg.model = model
    if effort is not None:
        cfg.effort = effort

    frame = CandidateFrame(
        source_id=source, ordinal=0, frame_index=0, timestamp=0.0, path=Path(image)
    )
    detections = VisionLLMIdentifier(cfg).identify(frame)
    for d in detections:
        ident = d.cardId or (d.name and f"name:{d.name}") or "?"
        foil = "" if d.isFoil is None else (" foil" if d.isFoil else " non-foil")
        typer.echo(f"{ident}\tconf={d.confidence:.2f}{foil}")
    typer.echo(f"{len(detections)} detection(s)")


@app.command(name="build-art-index")
def build_art_index_cmd(
    art_root: str = typer.Option("data/art", "--art-root", help="Reference-art dir (fetch-art)."),
    set_code: str | None = typer.Option(None, "--set", help="Only index ids '<SET>-...'."),
    out: str | None = typer.Option(
        None, "--out", help="Persist the index here (default: <art-root>/<set>.index)."
    ),
) -> None:
    """Build + persist the Tier 2 ORB/FAISS art index for a set (stage [3], M7)."""
    from .pipeline.identify.local_cv import build_art_index, save_art_index

    art_index = build_art_index(art_root, set_code)
    dest = out or f"{art_root}/{set_code or 'all'}.index"
    save_art_index(art_index, dest)
    typer.echo(
        f"indexed {len(art_index.card_ids)} card(s), {len(art_index.labels)} descriptors -> {dest}"
    )


@app.command(name="identify-cv")
def identify_cv_cmd(
    image: str = typer.Option(..., "--image", help="Path to a card image or frame to identify."),
    art_root: str = typer.Option("data/art", "--art-root", help="Reference-art dir (fetch-art)."),
    set_code: str | None = typer.Option(None, "--set", help="Restrict the index to a set."),
    source: str = typer.Option("frame", "--source", help="Source id to stamp on detections."),
    no_detect: bool = typer.Option(
        False, "--no-detect", help="Treat the whole image as one card (skip rectangle detection)."
    ),
) -> None:
    """Run the Tier 2 art-match identifier on a single image (stage [3], M7). Needs the cv extra."""
    from pathlib import Path

    from .pipeline.frames import CandidateFrame
    from .pipeline.identify.local_cv import LocalCVIdentifier, build_art_index

    identifier = LocalCVIdentifier(build_art_index(art_root, set_code), detect=not no_detect)
    frame = CandidateFrame(
        source_id=source, ordinal=0, frame_index=0, timestamp=0.0, path=Path(image)
    )
    detections = identifier.identify(frame)
    for d in detections:
        typer.echo(f"{d.cardId}\tconf={d.confidence:.2f}")
    typer.echo(f"{len(detections)} detection(s)")


@app.command(name="fetch-art")
def fetch_art_cmd(
    cache: str = typer.Option(
        ..., "--cache", help="Image cache JSON (local path or http(s) URL): {cardId: url}."
    ),
    out: str = typer.Option("data/art", "--out", help="Art root to download into."),
    set_code: str | None = typer.Option(
        None, "--set", help="Only fetch ids starting with '<SET>-'."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Cap the number of images fetched."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-download existing files."),
) -> None:
    """Download reference card images for Tier 2 / synthetic fixtures (plan.md section 8)."""
    from .assets import fetch_art, load_image_cache

    image_cache = load_image_cache(cache)
    manifest = fetch_art(image_cache, out, set_code=set_code, limit=limit, overwrite=overwrite)
    typer.echo(f"art manifest now has {len(manifest)} entries at {out}")


if __name__ == "__main__":
    app()
