"""Adapter protocol.

An adapter encodes what is true about *one collection* -- how its files are
named, what its embedded metadata means, which paths are not music at all.
Keeping that knowledge per-collection instead of in one global heuristic is the
central structural decision of the ingester: the CD Sheet Music discs and a
flat folder of pop lead sheets share almost no assumptions.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from ...pdfsignals import FileSignals
from ..model import FileProposal


@dataclass
class CollectionContext:
    """Whole-collection state an adapter may build once, before any file is read.

    The CD Sheet Music adapter uses this to parse the disc's ``toc.pdf`` a
    single time and then cross-check every one of its 94 files against it.
    """

    root: Path
    name: str
    adapter: str = ""
    defaults: dict[str, object] = field(default_factory=dict)
    data: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Adapter:
    """Base class.  Subclasses override :meth:`detect`, :meth:`prepare`, :meth:`propose`."""

    name: str = "base"
    #: Weight given to values that come from this collection's declared defaults.
    default_weight: float = 0.60
    #: Paths never catalogued.  Matched against the POSIX-style relative path.
    ignore_globs: tuple[str, ...] = (
        "*/readme/*", "readme/*",
        "*/DATA/*",
        "*.exe", "*.iso", "*.dll", "*.com", "*.chm", "*.inf", "*.hqx", "*.gz",
        "*/Icon_*", "*Icon\r*",
        "*/! *",              # the discs' bundled utilities: "! Photoscore prof. 3.1"
    )

    def detect(self, root: Path) -> float:
        """Confidence in the range 0..1 that this adapter owns ``root``."""
        return 0.0

    def prepare(self, context: CollectionContext) -> None:
        """Build whole-collection state.  Default: nothing to do."""

    def should_ignore(self, rel_path: str) -> bool:
        posix = rel_path.replace("\\", "/")
        return any(fnmatch.fnmatch(posix, pattern) for pattern in self.ignore_globs)

    def propose(self, signals: FileSignals, context: CollectionContext) -> FileProposal:
        raise NotImplementedError


_REGISTRY: list[Adapter] = []


def register(adapter: type[Adapter] | Adapter) -> type[Adapter] | Adapter:
    """Register an adapter.  Usable as a class decorator or on an instance."""
    _REGISTRY.append(adapter() if isinstance(adapter, type) else adapter)
    return adapter


def all_adapters() -> list[Adapter]:
    return list(_REGISTRY)


def get_adapter(name: str) -> Adapter:
    for adapter in _REGISTRY:
        if adapter.name == name:
            return adapter
    raise KeyError(f"no adapter named {name!r} (have: {[a.name for a in _REGISTRY]})")


def choose_adapter(root: Path) -> Adapter:
    """Pick the best-matching adapter for a collection root.

    Ties go to whichever registered first; the generic adapter always scores
    above zero so there is never no answer.
    """
    ranked = sorted(_REGISTRY, key=lambda a: a.detect(root), reverse=True)
    return ranked[0]
