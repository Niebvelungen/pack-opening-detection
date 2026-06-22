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
) -> None:
    """Run the full pipeline for one set (catalog -> Pack Configuration + Confidence Report).

    Not yet implemented; completed at milestone M5. See implementation-plan.md.
    """
    typer.echo(
        "pack-miner run is not implemented yet (lands at M5). "
        f"Inputs received: catalog={catalog}, manifest={manifest}, "
        f"template={template}, out={out}"
    )
    raise typer.Exit(code=1)


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
