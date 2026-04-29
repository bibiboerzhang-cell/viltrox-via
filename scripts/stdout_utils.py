from __future__ import annotations

import json
import sys
from typing import Any


def out(message: str = "", *, flush: bool = False) -> None:
    sys.stdout.write(f"{message}\n")
    if flush:
        sys.stdout.flush()


def out_json(payload: Any, *, flush: bool = True, **json_kwargs: Any) -> None:
    sys.stdout.write(json.dumps(payload, **json_kwargs) + "\n")
    if flush:
        sys.stdout.flush()
