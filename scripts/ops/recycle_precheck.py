#!/usr/bin/env python3
"""Split failed YouTube recycle candidates before any requeue.

Input is TSV on stdin: ``job_id<TAB>url``. The script writes review artifacts
and never connects to the database. Generated SQL only tags already-failed jobs
as ``content_unavailable`` after human review.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

_REPO = pathlib.Path(__file__).resolve().parents[2]
for candidate in (_REPO / "backend", _REPO):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(1, str(_REPO / "scripts"))

from app.services.scraping.availability import probe_video_availability  # noqa: E402
from stdout_utils import out  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", required=True, help="directory for artifacts")
    parser.add_argument("--qps", type=float, default=2.0, help="probe rate limit")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--max",
        type=int,
        default=0,
        help="optional cap on candidates processed this run (0 = no cap)",
    )
    return parser.parse_args()


def _read_rows(limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            out(f"skip malformed line: {line[:120]}", file=sys.stderr)
            continue
        rows.append((parts[0].strip(), parts[-1].strip()))
        if limit and len(rows) >= limit:
            break
    return rows


def _write_sql(outdir: pathlib.Path, dead_ids: list[str]) -> None:
    sql_lines = [
        "-- recycle_precheck: tag terminal content_unavailable jobs.",
        "-- Review, then apply via psql inside a transaction.",
        "-- Touches ONLY already-failed jobs; never resurrects, never deletes.",
        "BEGIN;",
    ]
    if dead_ids:
        id_list = ", ".join(dead_ids)
        sql_lines += [
            "UPDATE apify_jobs",
            "   SET last_error_category = 'content_unavailable',",
            "       last_error = 'precheck: youtube oEmbed 404/410 (deleted_or_private)'",
            f" WHERE id IN ({id_list})",
            "   AND status = 'failed';",
        ]
    else:
        sql_lines.append("-- (no unavailable candidates this run)")
    sql_lines.append("COMMIT;")
    (outdir / "tag_unavailable.sql").write_text("\n".join(sql_lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(args.max)
    gap = (1.0 / args.qps) if args.qps > 0 else 0.0
    verdicts = []
    for index, (job_id, url) in enumerate(rows):
        if gap and index:
            time.sleep(gap)
        result = probe_video_availability(url, timeout=args.timeout)
        verdicts.append((job_id, result))
        out(
            f"[{index + 1}/{len(rows)}] job={job_id} -> {result.status}"
            f" ({result.http_code} {result.reason})",
            file=sys.stderr,
        )

    buckets: dict[str, list[str]] = {}
    with (outdir / "verdicts.tsv").open("w", encoding="utf-8") as handle:
        handle.write("job_id\tvideo_id\tstatus\thttp_code\treason\ttitle\n")
        for job_id, res in verdicts:
            buckets.setdefault(res.status, []).append(job_id)
            handle.write(
                f"{job_id}\t{res.video_id}\t{res.status}\t{res.http_code}\t{res.reason}\t{res.title}\n"
            )

    recyclable = (
        buckets.get("available", [])
        + buckets.get("restricted", [])
        + buckets.get("unknown", [])
    )
    (outdir / "recyclable_ids.txt").write_text(
        "\n".join(recyclable) + ("\n" if recyclable else ""),
        encoding="utf-8",
    )
    _write_sql(outdir, buckets.get("unavailable", []))

    summary = "\n".join(f"{status}: {len(ids)}" for status, ids in sorted(buckets.items()))
    if not summary:
        summary = "no input rows"
    (outdir / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    out("\n== precheck summary ==\n" + summary)
    out(f"artifacts -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
