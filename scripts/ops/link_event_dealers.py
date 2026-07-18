#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Link dealer_event opportunities to their host dealers (relation_type=host).

The reviewed event catalog carries no dealer references, so
``vkpi_event_opportunity_dealers`` stayed empty and the radar list could not
show which published dealer hosts each event.  This linker matches
``dealer_event`` lane opportunities to published dealers by conservative
name-token overlap **in the same city** — e.g. "Nikon Days at Samy's Camera –
Pasadena" → the Samy's Camera Pasadena dealer row.

Conservative on purpose: it links only when exactly ONE published dealer in
the opportunity's city shares a distinctive token with the title/organizer.
Zero or ambiguous matches are reported, never guessed.  Dry-run by default.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402

_STOP = frozenset(
    "the a at of and photo video camera cameras store shop days series "
    "beginner photography roadshow creativity to cashflow navigating light "
    "portrait canon nikon sony".split()
)


def _tokens(text: object) -> frozenset[str]:
    return frozenset(
        w
        for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
        if len(w) > 2 and w not in _STOP
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write links (default: dry-run)")
    parser.add_argument("--organization-id", type=int, default=1)
    args = parser.parse_args(argv)

    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        stdout_out("error: linker requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()
    opportunities = [
        dict(r)
        for r in conn.execute(
            "SELECT id, title, organizer, city, region FROM vkpi_event_opportunities "
            "WHERE lane='dealer_event' AND organization_id=?",
            (args.organization_id,),
        ).fetchall()
    ]
    dealers = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, city, state FROM vkpi_dealers WHERE publication_status='published'"
        ).fetchall()
    ]
    linked = ambiguous = unmatched = 0
    for opp in opportunities:
        existing = conn.execute(
            "SELECT 1 FROM vkpi_event_opportunity_dealers WHERE opportunity_id=? AND organization_id=?",
            (opp["id"], args.organization_id),
        ).fetchone()
        if existing is not None:
            continue
        opp_tokens = _tokens(opp["title"]) | _tokens(opp["organizer"])
        opp_city = str(opp["city"] or "").strip().lower()
        if not opp_tokens or not opp_city:
            unmatched += 1
            continue
        candidates = [
            d
            for d in dealers
            if str(d["city"] or "").strip().lower() == opp_city and (_tokens(d["name"]) & opp_tokens)
        ]
        if len(candidates) == 1:
            host = candidates[0]
            linked += 1
            stdout_out(
                f"{'link' if args.apply else 'would link'} "
                f"{str(opp['title'])[:44]} -> #{host['id']} {str(host['name'])[:36]}"
            )
            if args.apply:
                conn.execute(
                    """
                    INSERT INTO vkpi_event_opportunity_dealers(
                      opportunity_id, organization_id, dealer_id, relation_type, created_at
                    ) VALUES (?, ?, ?, 'host', NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (opp["id"], args.organization_id, host["id"]),
                )
        elif len(candidates) > 1:
            ambiguous += 1
            stdout_out(
                f"ambiguous: {str(opp['title'])[:44]} -> "
                f"{[str(c['name'])[:24] for c in candidates[:3]]}"
            )
        else:
            unmatched += 1
    if args.apply:
        conn.commit()
    stdout_out(f"linked={linked} ambiguous={ambiguous} unmatched={unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
