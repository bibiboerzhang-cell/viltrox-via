"""How a final_v1 score is recognised and read — one seam, two callers.

The quality gate decides whether a score was *returned*; the result projection
decides what that score *is*. Those two answers must never disagree, because a
gate that accepts a shape the projection silently drops hides a paid analysis
from the user, and a gate stricter than the projection rejects one outright.
Both defects shipped at once, so the two rules live here side by side.

Alignment with ``app.domains.kol.final_v1_extract._score_from_value`` — the
third consumer of the same value — is exact for every **finite** value: any
number that projection can read, this gate accepts, in the same shapes.

Two deliberate, documented divergences (neither is a quality standard):

* An explicit ``None`` (or ``{"score": None}``) is *returned* but not readable.
  The prompt contract lets the model answer an honest "no number", so the gate
  accepts it while the projection reports no score. This asymmetry is the point
  of the gate: it checks that the field came back, not that a number was
  invented.
* ``NaN``/``±Inf`` (which ``json.loads`` produces from bare ``NaN``/``Infinity``
  tokens) are treated here as "no number came back". ``_score_from_value``
  drops ``NaN`` too, but clamps ``±Inf`` into 0..100 — so for ``±Inf`` this
  module is the stricter of the two on purpose. Clamping a non-number to a
  score, and to a *perfect* score at that, would manufacture a verdict out of
  provider noise.

Pure functions: no I/O, no service imports.
"""
from __future__ import annotations

import math
from typing import Any


def _score_value(entry: Any) -> float | int | None:
    """Read a score out of either contract shape: ``{"score": N}`` or a bare N.

    Returns ``None`` when there is no readable number, so callers can tell
    "unknown" from a real zero.  Finite out-of-range values clamp to 0..100
    rather than being discarded; ``NaN``/``±Inf`` are not numbers and clamp to
    nothing.  ``bool`` is not a score.
    """

    if isinstance(entry, bool):
        return None
    if isinstance(entry, dict):
        value = entry.get("score")
    else:
        value = entry
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        # NaN never compares true, so ``min``/``max`` would silently hand it
        # through as the clamp bound -- 100, a perfect score, from a value the
        # model never gave. Infinity has no honest place on a 0..100 scale
        # either. Both mean the same thing here: no number came back.
        return None
    parsed = max(0.0, min(100.0, parsed))
    return int(parsed) if parsed.is_integer() else round(parsed, 2)


def _final_v1_score_returned(scores: dict[str, Any], key: str) -> bool:
    """Report whether ``key`` was explicitly returned in a shape we can read.

    The contract carries a score in three legal shapes:

    * a mapping carrying ``score`` (what gemini-2.5-flash emitted),
    * a scalar the projection can read as a number -- a bare int/float, or the
      numeric string some providers emit for the same field,
    * an explicit ``None`` (the prompt allows an honest "no number").

    A key the provider never returned stays a real gap, and so does a value no
    consumer can read as a number: ``bool`` (``True`` is an ``int`` in Python,
    and scoring a video ``1`` because the model answered "yes" would invent a
    number rather than read one), free text, and ``NaN``/``±Inf``.

    The two shapes are judged by **one** rule, not two.  A mapping is unwrapped
    to the value under ``score`` and then decided exactly as a bare value would
    be, so ``{"score": NaN}`` -- the shape gemini-2.5-flash actually emits --
    cannot be called "returned" while a bare ``NaN`` is called a gap.  A second,
    shape-only rule for the mapping is what let a non-number through before.
    """

    if key not in scores:
        return False
    value = scores[key]
    if isinstance(value, dict):
        # A mapping without ``score`` never carried one.  With it, the verdict
        # is the verdict of the value inside -- same rules, one seam.
        if "score" not in value:
            return False
        value = value["score"]
    if isinstance(value, bool):
        return False
    if value is None:
        # An honest "no number" from the model, in either shape.
        return True
    return _score_value(value) is not None
