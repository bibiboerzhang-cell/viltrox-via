from __future__ import annotations

import io

import pytest

from scripts.stdout_utils import out


class _FlushProbe(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_out_preserves_print_style_values_separators_and_endings() -> None:
    stream = io.StringIO()

    out("alpha", 2, None, sep="|", end="!", file=stream)
    out(file=stream)

    assert stream.getvalue() == "alpha|2|None!\n"


def test_out_defaults_none_sep_and_end_like_print() -> None:
    stream = io.StringIO()

    out("alpha", "beta", sep=None, end=None, file=stream)

    assert stream.getvalue() == "alpha beta\n"


def test_out_flushes_the_selected_stream() -> None:
    stream = _FlushProbe()

    out("ready", file=stream, flush=True)

    assert stream.getvalue() == "ready\n"
    assert stream.flush_count == 1


@pytest.mark.parametrize(("keyword", "value"), [("sep", 7), ("end", object())])
def test_out_rejects_non_string_separators_and_endings(keyword: str, value: object) -> None:
    with pytest.raises(TypeError, match=keyword):
        out("value", **{keyword: value})
