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


if __name__ == "__main__":
    app()
