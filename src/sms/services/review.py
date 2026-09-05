"""Deciding what a piece is: the transitions, in one place.

Every one of these does the same four things, and doing three of them is a bug
that does not announce itself:

* record the state a person put the piece in,
* record who and when,
* **recompute**, because the route is derived and a stale route is what the
  rest of the system acts on,
* and queue the filing pass, because a library folder is named from metadata
  that may just have changed.

Rejecting used to do the first two only.  A piece that had auto-accepted kept
``route = "accept"``, and :func:`sms.library.materialise` files anything whose
route says accept -- so marking a scan "not music" left its file sitting in
the library under the title nobody believed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..ingest.persist import accept_value, recompute
from ..models import Piece


@dataclass(frozen=True)
class Reviewer:
    """Who is deciding, in the terms both surfaces can supply."""

    user_id: int | None = None
    name: str = "human"

    @property
    def source(self) -> str:
        return f"human:{self.name}"


def _settle(session: Session, piece: Piece, state: str, reviewer: Reviewer) -> Piece:
    """Record the decision, re-derive what follows from it, and file."""
    piece.review_state = state
    piece.reviewed_by = reviewer.user_id
    piece.reviewed_at = datetime.now(timezone.utc)

    collection = piece.source_file.collection
    recompute(
        session, piece,
        auto_accept=collection.auto_accept,
        review_floor=collection.review_floor,
    )
    _queue_filing(session, collection.id)
    session.flush()
    return piece


def _queue_filing(session: Session, collection_id: int) -> None:
    """Ask for a filing pass, at most one waiting at a time.

    Queued rather than done inline: the move is filesystem work over SMB and
    the reviewer is waiting for the next piece, not for a copy.
    """
    from ..jobs import enqueue_once

    enqueue_once(session, "materialise", {"collection_id": collection_id})


def approve(session: Session, piece: Piece, reviewer: Reviewer) -> Piece:
    """Accept the piece as catalogued and take it out of the queue.

    Reviewing is a fact of its own: the piece is accepted because a person
    looked at it, whatever the signals add up to.  Deciding the *value* of a
    field is a separate act -- see :func:`decide` -- so that confirming a
    piece does not silently turn every pre-filled guess into a permanent human
    judgement.
    """
    return _settle(session, piece, "accepted", reviewer)


def reject(session: Session, piece: Piece, reviewer: Reviewer) -> Piece:
    """Set the piece aside: a cover page, a licence, something not music.

    The entry is excluded from the catalogue and kept, so it can be found
    again.  The file on disk is untouched.
    """
    return _settle(session, piece, "rejected", reviewer)


def skip(session: Session, piece: Piece) -> Piece:
    """Leave it pending, but push it behind the rest for now.

    Deliberately not a decision, and deliberately not recorded as one: the
    reviewer is saying "not this one, not now", which is information about
    them rather than about the piece.
    """
    piece.confidence = min(piece.confidence + 0.0001, 0.9999)
    session.flush()
    return piece


def siblings(session: Session, piece: Piece, scope: str = "folder") -> list[Piece]:
    """The pending pieces this one sits with, including itself.

    ``folder`` is the directory the file is in, which in every collection here
    is the unit that shares a composer.  ``file`` is the pieces of one volume,
    for an anthology split into many.
    """
    from sqlalchemy import select

    from ..models import SourceFile

    query = (
        select(Piece)
        .join(SourceFile)
        .where(
            SourceFile.collection_id == piece.source_file.collection_id,
            Piece.review_state == "pending",
        )
    )
    if scope == "file":
        query = query.where(Piece.source_file_id == piece.source_file_id)
    else:
        folder = folder_of(piece.source_file.rel_path)
        # Same directory, not the whole subtree: "bach" is a composer, but
        # "bach/wtc" is a set and "bach/invents" is a different one.
        query = query.where(SourceFile.rel_path.like(f"{folder}%")).where(
            ~SourceFile.rel_path.like(f"{folder}%/%")
        )
    return list(session.scalars(query.order_by(Piece.id)))


def folder_of(rel_path: str) -> str:
    normalised = rel_path.replace("\\", "/")
    cut = normalised.rfind("/")
    return "" if cut < 0 else normalised[: cut + 1]


def decide_many(
    session: Session,
    pieces: list[Piece],
    values: dict[str, str],
    reviewer: Reviewer,
) -> int:
    """Apply the same values to several pieces.  Returns how many changed.

    Deliberately *not* an approval: it records what these pieces have in
    common -- the composer, usually -- and leaves each one in the queue to be
    confirmed on its own. Filling in a shared field is a different act from
    saying a piece is right.
    """
    touched = 0
    for piece in pieces:
        # Not changed_only. Ticking the box *is* the act: the folder almost
        # always already carries the right composer as a guess, and the point
        # is to turn that guess into a decision across the whole set. That is
        # the opposite of the single-piece form, which arrives pre-filled and
        # must not record values nobody looked at.
        if decide(session, piece, values, reviewer, changed_only=False):
            collection = piece.source_file.collection
            recompute(
                session, piece,
                auto_accept=collection.auto_accept,
                review_floor=collection.review_floor,
            )
            touched += 1
    if touched:
        _queue_filing(session, pieces[0].source_file.collection_id)
    session.flush()
    return touched


def decide(
    session: Session,
    piece: Piece,
    values: dict[str, str],
    reviewer: Reviewer,
    *,
    changed_only: bool = True,
) -> list[str]:
    """Record chosen values as decisions.  Returns the fields that changed.

    ``changed_only`` compares against what the piece already shows, so a form
    that arrives pre-filled does not record values nobody examined.  A decision
    cannot be argued down by a later, better adapter, and one pass through the
    queue should not spend that on six fields the reviewer never read.
    """
    from ..ingest.persist import DENORMALISED

    current = {
        field: str(getattr(piece, column))
        for field, column in DENORMALISED.items()
        if getattr(piece, column, None) is not None
    }
    decided: list[str] = []
    for field, value in values.items():
        value = (value or "").strip()
        if not value:
            continue
        if changed_only and value == current.get(field, ""):
            continue
        accept_value(session, piece, field, value,
                     user_id=reviewer.user_id, source=reviewer.source)
        decided.append(field)
    return decided
