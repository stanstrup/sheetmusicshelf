"""Command line entry point.

``sms report`` runs the whole ingest pipeline without a database, so a
collection's guesses can be judged before a single row is committed.  That is
the intended first step for every new collection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import cli_admin
from .ingest import adapters as _adapters  # noqa: F401  (registers adapters)
from .ingest.adapters.base import all_adapters, choose_adapter, get_adapter
from .ingest.model import FileProposal
from .ingest.scanner import scan
from .ingest.scoring import AUTO_ACCEPT, REVIEW_FLOOR, route

app = typer.Typer(add_completion=False, help="Sheet Music Shelf ingest and cataloguing tools.")
console = Console()

# Commands that need a live database live in cli_admin.
app.add_typer(cli_admin.db_app, name="db")
app.add_typer(cli_admin.collection_app, name="collection")
app.add_typer(cli_admin.composer_app, name="composer")
app.add_typer(cli_admin.work_app, name="work")
app.add_typer(cli_admin.token_app, name="token")
app.command("worker")(cli_admin.worker)

ROUTE_STYLE = {"accept": "green", "review": "yellow", "hold": "red"}
#: Notes that describe normal structure rather than something to look at.
BENIGN_NOTES = {"whole-file"}


@app.command("adapters")
def list_adapters() -> None:
    """Show the registered collection adapters."""
    table = Table(title="Registered adapters", header_style="dim")
    table.add_column("name")
    table.add_column("ignore globs", overflow="fold")
    for adapter in all_adapters():
        table.add_row(adapter.name, ", ".join(adapter.ignore_globs))
    console.print(table)


@app.command("report")
def report(
    path: Path = typer.Argument(..., help="Collection root to scan."),
    adapter_name: Optional[str] = typer.Option(None, "--adapter", "-a", help="Force a specific adapter."),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Stop after N files."),
    show: str = typer.Option("all", "--show", help="all | accept | review | hold | problems"),
    rows: int = typer.Option(40, "--rows", help="Maximum rows to print; 0 for all."),
    auto_accept: float = typer.Option(AUTO_ACCEPT, "--auto-accept", help="Auto-accept threshold."),
    review_floor: float = typer.Option(REVIEW_FLOOR, "--review-floor", help="Review threshold."),
    no_hash: bool = typer.Option(False, "--no-hash", help="Skip SHA-256 (faster over SMB)."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write the full proposal set as JSON."),
) -> None:
    """Scan a collection and report what the ingester would catalogue, and how sure it is."""
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise typer.BadParameter(f"not a directory: {root}")

    adapter = get_adapter(adapter_name) if adapter_name else choose_adapter(root)
    with console.status(f"scanning {root.name} with the {adapter.name} adapter..."):
        context, proposals, stats = scan(
            root,
            adapter=adapter,
            with_hash=not no_hash,
            limit=limit,
            auto_accept=auto_accept,
            review_floor=review_floor,
        )

    header = [f"[bold]{root}[/bold]", f"adapter: [cyan]{adapter.name}[/cyan]"]
    header += [f"[dim]{note}[/dim]" for note in context.notes]
    console.print(Panel("\n".join(header), title="collection", border_style="dim"))

    _print_summary(stats)
    _print_rows(proposals, show=show, rows=rows, auto_accept=auto_accept, review_floor=review_floor)
    _print_problems(proposals)

    if json_out is not None:
        json_out.write_text(json.dumps(_serialise(root, adapter.name, proposals), indent=2), encoding="utf-8")
        console.print(f"\n[dim]wrote[/dim] {json_out}")


def _print_summary(stats) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", justify="right")
    table.add_column()
    table.add_row("files seen", str(stats.files_seen))
    table.add_row("catalogued", str(stats.catalogued))
    table.add_row("skipped", str(stats.skipped))
    if stats.unreadable:
        table.add_row("unreadable", f"[red]{stats.unreadable}[/red]")
    table.add_row("pieces", str(stats.pieces))
    total = max(stats.pieces, 1)
    for name in ("accept", "review", "hold"):
        count = stats.by_route[name]
        table.add_row(name, f"[{ROUTE_STYLE[name]}]{count}[/] [dim]({count * 100 // total}%)[/dim]")
    console.print(table)
    console.print()


def _print_rows(
    proposals: list[FileProposal],
    *,
    show: str,
    rows: int,
    auto_accept: float,
    review_floor: float,
) -> None:
    table = Table(header_style="dim", row_styles=["", "on grey11"])
    table.add_column("conf", justify="right", width=5)
    table.add_column("route", width=6)
    table.add_column("composer", max_width=22, overflow="ellipsis")
    table.add_column("title", max_width=40, overflow="ellipsis")
    table.add_column("catalog", width=12)
    table.add_column("key", width=14)
    table.add_column("pp", justify="right", width=7)
    table.add_column("file", max_width=28, overflow="ellipsis", style="dim")

    limit = rows if rows > 0 else None
    printed = 0
    for proposal in proposals:
        if proposal.skipped:
            continue
        for piece in proposal.pieces:
            bucket = route(piece.confidence, auto_accept=auto_accept, review_floor=review_floor)
            if show == "problems":
                interesting = [n for n in piece.all_notes if n not in BENIGN_NOTES]
                if bucket == "accept" and not proposal.warnings and not interesting:
                    continue
            elif show != "all" and bucket != show:
                continue
            if limit is not None and printed >= limit:
                break
            marker = "!" if any(f.conflict for f in piece.fields.values()) else ""
            pages = f"{piece.page_start}-{piece.page_end}" if piece.page_count > 1 else str(piece.page_start)
            table.add_row(
                f"{piece.confidence:.2f}",
                f"[{ROUTE_STYLE[bucket]}]{bucket}{marker}[/]",
                str(piece.get("composer", "") or ""),
                str(piece.get("title", "") or ""),
                str(piece.get("catalog", "") or ""),
                str(piece.get("key", "") or ""),
                pages,
                proposal.rel_path,
            )
            printed += 1
        if limit is not None and printed >= limit:
            break

    if printed:
        console.print(table)
        console.print("[dim]! = signals disagree on at least one field[/dim]")
    else:
        console.print(f"[dim]no pieces matched --show {show}[/dim]")


def _print_problems(proposals: list[FileProposal]) -> None:
    warned = [(p.rel_path, w) for p in proposals for w in p.warnings]
    noted = [(p.rel_path, n) for p in proposals for pc in p.pieces for n in pc.all_notes
             if n not in BENIGN_NOTES]
    if not warned and not noted:
        return
    console.print()
    table = Table(title="warnings", header_style="dim", title_style="dim")
    table.add_column("file", max_width=40, overflow="ellipsis", style="dim")
    table.add_column("note")
    for rel, message in (warned + noted)[:25]:
        table.add_row(rel, message)
    console.print(table)
    if len(warned) + len(noted) > 25:
        console.print(f"[dim]... and {len(warned) + len(noted) - 25} more[/dim]")


def _serialise(root: Path, adapter: str, proposals: list[FileProposal]) -> dict:
    return {
        "collection": str(root),
        "adapter": adapter,
        "files": [
            {
                "path": p.rel_path,
                "skipped": p.skipped,
                "warnings": p.warnings,
                "pieces": [
                    {
                        "page_start": pc.page_start,
                        "page_end": pc.page_end,
                        "printed_first_page": pc.printed_first_page,
                        "confidence": pc.confidence,
                        "notes": pc.all_notes,
                        "fields": {
                            name: {
                                "value": f.value,
                                "confidence": f.confidence,
                                "sources": f.sources,
                                "conflict": f.conflict,
                                "alternatives": [[a, c, s] for a, c, s in f.alternatives],
                            }
                            for name, f in pc.fields.items()
                        },
                    }
                    for pc in p.pieces
                ],
            }
            for p in proposals
        ],
    }


if __name__ == "__main__":  # pragma: no cover
    app()
