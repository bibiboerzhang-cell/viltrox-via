"""回归守卫:任何 logger.*(..., extra={...}) 的字面键都不得撞 logging.LogRecord 保留字段。

撞名(如 'created'/'filename'/'name'/'module'/'message')会在 makeRecord 抛
KeyError("Attempt to overwrite ...") → 把整段业务逻辑记成 failed(见履约 delivered_scan
被这一行 'created' 撞崩、project_shipment_sync 长期记错)。用 AST 静态扫死这个 bug 类。
"""
from __future__ import annotations

import ast
import pathlib
import unittest

# logging.LogRecord.__init__ 写入 __dict__ 的标准字段(makeRecord 据此判 extra 撞名)。
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"


def _violations() -> list[str]:
    out: list[str] = []
    for py in _APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS):
                continue
            for kw in node.keywords:
                if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                    continue
                for k in kw.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value in _RESERVED:
                        out.append(f"{py.relative_to(_APP_DIR.parents[1])}:{getattr(k, 'lineno', '?')} extra key '{k.value}'")
    return out


class LoggingExtraReservedKeyTests(unittest.TestCase):
    def test_no_extra_key_collides_with_logrecord_reserved(self) -> None:
        bad = _violations()
        self.assertEqual(
            bad, [],
            "logger extra={} 用了 LogRecord 保留字段(会 KeyError 崩日志行):\n  " + "\n  ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
