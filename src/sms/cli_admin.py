"""Administrative CLI: database, collections, ingest, tokens, worker.

Kept separate from the report tooling in :mod:`sms.cli` because everything
here needs a live database, and ``sms report`` deliberately does not.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from .config import get_settings
from .db import session_scope
from .ingest.adapters.base import choose_adapter, get_adapter
from .ingest.persist import commit_proposal, upsert_collection
from .ingest.scanner import ScanStats, build_context, iter_proposals
from .models import Collection, Piece, SourceFile

console = Console()

db_app = typer.Typer(help="Database schema management.")
collection_app = typer.Typer(help="Register and scan collections.")
token_app = typer.Typer(help="API tokens for agents and devices.")


# --- database -------------------------------------------------------------

@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head")) -> None:
    """Apply migrations."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, revision)
    console.print(f"[green]database at {revision}[/green]")


@db_app.command("revision")
def db_revision(message: str = typer.Option(..., "-m", "--message")) -> None:
    """Autogenerate a migration from the models."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.revision(config, message=message, autogenerate=True)


@db_app.command("url")
def db_url() -> None:
    """Show which database is configured (password masked)."""
    import re

    url = get_settings().database_url
    console.print(re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", url))


# --- collections ----------------------------------------------------------

@collection_app.command("add")
def collection_add(
    path: Path = typer.Argument(..., help="Collection root."),
    name: str = typer.Option(None, "--name"),
    adapter: str = typer.Option(None, "--adapter", "-a"),
    auto_accept: float = typer.Option(None, "--auto-accept"),
    review_floor: float = typer.Option(None, "--review-floor"),
) -> None:
    """Register a collection.  Nothing is read until you run `sms collection scan`."""
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise typer.BadParameter(f"not a directory: {root}")
    chosen = get_adapter(adapter) if adapter else choose_adapter(root)

    with session_scope() as session:
        collection = upsert_collection(session, root, chosen.name, name)
        if auto_accept is not None:
            collection.auto_accept = auto_accept
        if review_floor is not None:
            collection.review_floor = review_floor
        console.print(
            f"[green]collection {collection.id}[/green] {collection.name} "
            f"[dim]adapter={chosen.name} accept>={collection.auto_accept} review>={collection.review_floor}[/dim]"
        )


@collection_app.command("list")
def collection_list() -> None:
    with session_scope() as session:
        table = Table(header_style="dim")
        for column in ("id", "name", "adapter", "files", "pieces", "accept", "review", "hold"):
            table.add_column(column, justify="right" if column not in ("name", "adapter") else "left")

        for collection in session.scalars(select(Collection).order_by(Collection.name)):
            files = session.scalar(
                select(func.count()).select_from(SourceFile)
                .where(SourceFile.collection_id == collection.id)
            ) or 0
            counts = dict(
                session.execute(
                    select(Piece.route, func.count())
                    .join(SourceFile, Piece.source_file_id == SourceFile.id)
                    .where(SourceFile.collection_id == collection.id)
                    .group_by(Piece.route)
                ).all()
            )
            table.add_row(
                str(collection.id), collection.name, collection.adapter, str(files),
                str(sum(counts.values())),
                str(counts.get("accept", 0)), str(counts.get("review", 0)), str(counts.get("hold", 0)),
            )
        console.print(table)


@collection_app.command("scan")
def collection_scan(
    collection_id: int = typer.Argument(..., help="Collection id from `sms collection list`."),
    limit: int = typer.Option(None, "--limit", "-n"),
    no_hash: bool = typer.Option(False, "--no-hash", help="Skip SHA-256 (faster over SMB)."),
) -> None:
    """Scan a collection into the catalogue, in the foreground.

    The same work the worker does, run synchronously so a first ingest can be
    watched.  Use the API's `/scan` endpoint for the background version.
    """
    with session_scope() as session:
        collection = session.get(Collection, collection_id)
        if collection is None:
            raise typer.BadParameter(f"no collection {collection_id}")

        root = Path(collection.source_path)
        adapter, context = build_context(root, get_adapter(collection.adapter))
        stats = ScanStats()

        with console.status(f"scanning {collection.name}...") as status:
            for signals, proposal in iter_proposals(
                root, adapter, context, with_hash=not no_hash, limit=limit
            ):
                stats.record(
                    proposal,
                    auto_accept=collection.auto_accept,
                    review_floor=collection.review_floor,
                )
                if signals is not None:
                    commit_proposal(session, collection, signals, proposal)
                if stats.files_seen % 25 == 0:
                    session.commit()
                    status.update(f"scanning {collection.name}: {stats.files_seen} files, {stats.pieces} pieces")

        from datetime import datetime, timezone

        collection.last_scanned_at = datetime.now(timezone.utc)

    console.print(
        f"[green]{stats.files_seen} files[/green] ({stats.skipped} skipped) -> {stats.pieces} pieces: "
        f"[green]{stats.by_route['accept']} accept[/green] / "
        f"[yellow]{stats.by_route['review']} review[/yellow] / "
        f"[red]{stats.by_route['hold']} hold[/red]"
    )


@collection_app.command("materialise")
def collection_materialise(
    collection_id: int = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply", help="Actually copy. Without this it only shows the plan."),
    include_unreviewed: bool = typer.Option(
        False, "--include-unreviewed", help="Also file pieces that have not been accepted."
    ),
) -> None:
    """Copy a collection into the managed tree.

    Dry run by default: the first thing worth seeing is the tree it *would*
    build.  Originals are never moved or deleted.
    """
    from .library import materialise

    with session_scope() as session:
        collection = session.get(Collection, collection_id)
        if collection is None:
            raise typer.BadParameter(f"no collection {collection_id}")

        result = materialise(
            session, collection,
            dry_run=not apply,
            only_accepted=not include_unreviewed,
        )

    if result.sample:
        table = Table(title="planned layout", header_style="dim", title_style="dim")
        table.add_column("source", max_width=42, overflow="ellipsis", style="dim")
        table.add_column("managed tree", overflow="fold")
        for source, target in result.sample:
            table.add_row(source, target)
        console.print(table)
        if result.planned > len(result.sample):
            console.print(f"[dim]... and {result.planned - len(result.sample)} more[/dim]")

    verb = "copied" if apply else "would copy"
    console.print(
        f"\n[green]{result.planned} files[/green] {verb}"
        + (f", {result.copied} done ({result.bytes_copied / 1e6:.1f} MB)" if apply else "")
        + (f", {result.skipped_unchanged} already present" if result.skipped_unchanged else "")
        + (f", [yellow]{result.skipped_unreviewed} not yet accepted[/yellow]" if result.skipped_unreviewed else "")
    )
    for error in result.errors[:10]:
        console.print(f"  [red]{error}[/red]")
    if not apply:
        console.print("[dim]nothing was written; re-run with --apply to copy[/dim]")


# --- tokens ---------------------------------------------------------------

@token_app.command("create")
def token_create(
    name: str = typer.Argument(..., help="What this token is for, e.g. 'claude-curation'."),
    scopes: list[str] = typer.Option(
        ["curation:read", "curation:write", "catalog:read"], "--scope", "-s",
    ),
) -> None:
    """Mint a bearer token for an external tool.  The secret is shown once."""
    from .auth import mint_token

    with session_scope() as session:
        secret, row = mint_token(session, name, list(scopes))
        console.print(f"[green]token {row.id}[/green] {row.name}  [dim]{', '.join(row.scopes)}[/dim]")
        console.print("\n[bold]Save this now -- it is not recoverable:[/bold]")
        console.print(f"  {secret}\n")
        console.print("[dim]Use it as:  Authorization: Bearer <token>[/dim]")


@token_app.command("list")
def token_list() -> None:
    from .models import ApiToken

    with session_scope() as session:
        table = Table(header_style="dim")
        for column in ("id", "name", "scopes", "last used", "revoked"):
            table.add_column(column)
        for row in session.scalars(select(ApiToken).order_by(ApiToken.created_at)):
            table.add_row(
                str(row.id), row.name, ", ".join(row.scopes or []),
                row.last_used_at.isoformat(timespec="minutes") if row.last_used_at else "-",
                "yes" if row.revoked_at else "no",
            )
        console.print(table)


@token_app.command("revoke")
def token_revoke(token_id: int) -> None:
    from datetime import datetime, timezone

    from .models import ApiToken

    with session_scope() as session:
        row = session.get(ApiToken, token_id)
        if row is None:
            raise typer.BadParameter(f"no token {token_id}")
        row.revoked_at = datetime.now(timezone.utc)
        console.print(f"[yellow]revoked[/yellow] {row.name}")


# --- worker ---------------------------------------------------------------

def worker() -> None:
    """Run the background job worker until interrupted."""
    from .jobs import worker_loop

    worker_loop()
