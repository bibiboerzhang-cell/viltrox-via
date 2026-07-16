#!/usr/bin/env python3
"""dist 分包护栏(F2 工程税②):chunk 尺寸上限 + chunk 间静态 import 无环校验。

背景:
- R3 事故:manualChunks 把 React.lazy() 路由模块塞进具名 chunk → chunk 间静态
  import 成环 → 运行时 TDZ /「页面加载失败」(build 静态不报、tsc/vitest 抓不到)。
  本脚本把「成环」变成 CI 可抓的硬失败。
- 「dist 分包后 grep 入口=假阴性」教训:验证必须扫全部 chunk,不能只看入口文件。

用法:python3 scripts/check_chunk_graph.py [assets_dir] [max_kb]
  assets_dir 默认 frontend/dist/assets,max_kb 默认 600。
退出码:任一 JS chunk > max_kb,或静态 import 图有环 → 1;否则 0。
"""

from __future__ import annotations

from stdout_utils import out

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = REPO_ROOT / "frontend" / "dist" / "assets"
DEFAULT_MAX_KB = 600.0

# rollup 产物里的静态 import 形态:
#   import{a as b}from"./chunk-X.js";   import"./chunk-X.js";
# 动态 import("./chunk-X.js") 因引号前必有 "(" 而被排除。
_STATIC_IMPORT_RE = re.compile(r'(?:^|[;{}\s)])import\s*(?:[\w$*{},:\s]+?from\s*)?"\./([^"]+\.js)"')


def build_graph(assets: Path) -> tuple[dict[str, set[str]], dict[str, float]]:
    js_files = [f for f in os.listdir(assets) if f.endswith(".js")]
    sizes = {f: (assets / f).stat().st_size / 1024.0 for f in js_files}
    graph: dict[str, set[str]] = {}
    for name in js_files:
        text = (assets / name).read_text(encoding="utf-8", errors="ignore")
        deps = {m.group(1) for m in _STATIC_IMPORT_RE.finditer(text)}
        graph[name] = {d for d in deps if d in sizes}
    return graph, sizes


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    for root in graph:
        if color[root] != WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        color[root] = GRAY
        path = [root]
        while stack:
            node, pending = stack[-1]
            advanced = False
            while pending:
                nxt = pending.pop()
                if color[nxt] == GRAY:
                    return path[path.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, sorted(graph[nxt])))
                    path.append(nxt)
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


def main() -> int:
    assets = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ASSETS
    max_kb = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_KB
    if not assets.is_dir():
        out(f"[chunk-graph] FAIL: assets 目录不存在:{assets}")
        return 1
    graph, sizes = build_graph(assets)
    if not sizes:
        out(f"[chunk-graph] FAIL: {assets} 下没有 JS chunk(先跑 npm run build)")
        return 1
    edge_count = sum(len(v) for v in graph.values())
    out(f"[chunk-graph] chunks={len(sizes)} static-edges={edge_count} max={max(sizes.values()):.1f}KB (limit {max_kb:.0f}KB)")
    for name, kb in sorted(sizes.items(), key=lambda kv: -kv[1])[:8]:
        out(f"[chunk-graph]   {kb:8.1f}KB  {name}")

    ok = True
    oversize = sorted(((f, kb) for f, kb in sizes.items() if kb > max_kb), key=lambda kv: -kv[1])
    if oversize:
        out(f"[chunk-graph] FAIL: 超过 {max_kb:.0f}KB 的 chunk:")
        for name, kb in oversize:
            out(f"[chunk-graph]   {kb:8.1f}KB  {name}")
        ok = False
    else:
        out(f"[chunk-graph] OK: 所有 chunk <= {max_kb:.0f}KB")

    cycle = find_cycle(graph)
    if cycle:
        out(f"[chunk-graph] FAIL: chunk 静态 import 成环(R3 级运行时炸弹): {' -> '.join(cycle)}")
        ok = False
    else:
        out("[chunk-graph] OK: chunk 静态 import 图无环(DAG)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
