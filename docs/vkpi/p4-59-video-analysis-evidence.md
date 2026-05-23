# P4.59 Video Analysis Evidence Contract

Date: 2026-05-23

## Scope

P4.59 connects stored video-analysis fields to the Intelligence Card evidence
contract. It does not run Gemini and does not create a batch executor.

## Source

Only stored rows from `submissions.video_analysis` may produce
`source=video_analysis` leaves.

A row is shown only when:

- `video_analysis.analyzed=true`;
- at least one contracted field is present;
- the evidence leaf names `source_table=submissions`;
- the evidence leaf names the concrete `source_id`;
- the original video URL is preserved when available.

## Fields

The section can expose these fields when already stored:

- `target_audience`
- `production_quality`
- `quality_scores`
- `quality_overall`
- `quality_summary`
- `competitor_products`
- `brand_integration_depth`
- `marketing_potential`
- `reference_value`
- `timestamps`
- `improvements`
- `content_genre`
- `content_topic`
- `content_summary`
- `products_found`

Missing fields remain missing. A Gemini preflight, dry-run plan, or future batch
proposal must not be shown as completed video analysis.

## API Shape

`/api/admin/vkpi/kol-pool/{id}/intelligence-card` now includes:

```json
{
  "video_analysis": {
    "status": "ready",
    "method": "read_only_stored_video_analysis_v0",
    "row_count": 1,
    "analyzed_count": 1,
    "evidence_count": 1,
    "field_counts": {"target_audience": 1},
    "evidence": [
      {
        "source": "video_analysis",
        "source_table": "submissions",
        "source_id": 123,
        "source_url": "https://youtube.com/watch?v=...",
        "fields": {"target_audience": "camera creators"}
      }
    ],
    "provider_calls": false,
    "llm_calls": false,
    "write_db": false
  }
}
```

If there is no stored analyzed row, the section returns `status=empty` and
`empty_reason=no_stored_analyzed_video_rows`.

## Acceptance

Run:

```bash
.venv/bin/python scripts/vkpi_video_analysis_evidence_acceptance.py --kol-pool-id 4217 --json
```

Expected:

- `passed=true`;
- `provider_calls=false`;
- `llm_calls=false`;
- `write_db=false`;
- ready sections are traceable to `submissions`;
- empty sections do not include fake fields.
