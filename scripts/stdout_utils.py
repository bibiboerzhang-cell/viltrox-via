from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def out(
    *values: object,
    sep: str | None = " ",
    end: str | None = "\n",
    file: TextIO | None = None,
    flush: bool = False,
) -> None:
    """Write CLI output with the public behavior of :func:`print`.

    Release hardening forbids direct ``print()`` calls in operational scripts
    so output has one auditable seam.  Keeping the familiar ``sep``/``end``/
    ``file``/``flush`` contract lets legacy scripts migrate without changing
    their byte-for-byte CLI output.
    """

    if sep is None:
        sep = " "
    elif not isinstance(sep, str):
        raise TypeError(f"sep must be None or a string, not {type(sep).__name__}")
    if end is None:
        end = "\n"
    elif not isinstance(end, str):
        raise TypeError(f"end must be None or a string, not {type(end).__name__}")
    stream = sys.stdout if file is None else file
    stream.write(sep.join(str(value) for value in values) + end)
    if flush:
        stream.flush()


def out_json(payload: Any, *, flush: bool = True, **json_kwargs: Any) -> None:
    sys.stdout.write(json.dumps(payload, **json_kwargs) + "\n")
    if flush:
        sys.stdout.flush()
