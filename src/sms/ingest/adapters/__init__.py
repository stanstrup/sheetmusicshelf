"""Collection adapters.

Importing this package registers every adapter.  Order matters only in that the
generic fallback must be importable last; selection is by ``detect()`` score.
"""

from .base import Adapter, CollectionContext, all_adapters, choose_adapter, get_adapter, register  # noqa: F401
from . import cdsheetmusic  # noqa: F401,E402
from . import popcollection  # noqa: F401,E402
from . import sheetmusicarchive  # noqa: F401,E402
from . import generic  # noqa: F401,E402

__all__ = ["Adapter", "CollectionContext", "all_adapters", "choose_adapter", "get_adapter", "register"]
