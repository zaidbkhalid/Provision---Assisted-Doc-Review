"""Obligation extraction — placeholder.

DELIBERATELY EMPTY. Phase 1 covers ingestion and grounded Q&A only. This module
is the seam where later phases attach:

  * extracting discount tiers, penalties/compensation, delivery commitments and
    SLAs from stored pages into a structured, citable schema;
  * delivery tracking and lateness prediction on top of those records;
  * loophole detection over asymmetric terms;
  * order-quantity and order-date optimisation;
  * drafting the claim notice or supplier email an obligation calls for.

Nothing here is implemented or wired into the API. It exists so those features
land in a module of their own rather than inside qa.py.
"""

from __future__ import annotations

__all__: list[str] = []
