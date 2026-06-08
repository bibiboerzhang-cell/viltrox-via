# Stage 1 - Apify -> R2 + Local KOL assets

Only fetches YouTube channel/video/transcript data and writes the same relative
keys to local SSD and R2. Stage 1 does not call any model.

Locked key contract:

```text
{kol_id}/_channel.json
{kol_id}/{video_id}/meta.json
{kol_id}/{video_id}/transcript.json
{kol_id}/{video_id}/storyboard/{n}.jpg
{kol_id}/_manifest.json
_global_manifest.json
```

Run:

```bash
pip install apify-client boto3

python -m stage1_apify_ingest.runner
python -m stage1_apify_ingest.runner --commit --only <kol_id>
python -m stage1_apify_ingest.runner --commit --only <kol_id> --retry-failed
```

Targeted production queue:

```bash
DATABASE_URL=postgres://...@.../<prod> python -m stage1_apify_ingest.run_target \
  --daily-hours 8 \
  --priority campaign,assignment,subscribers \
  --transcript-window all \
  --transcript-cap 0 \
  --per-channel-timeout 900 \
  --commit \
  --checkpoint .state/stage1_target.json \
  --report-dir reports/stage1/
```

Metadata-only first pass:

```bash
set -a; source .env; set +a
export DATABASE_URL='postgres://...@.../<prod>'
.venv/bin/python -m stage1_apify_ingest.run_target --phase metadata --commit \
  --priority campaign,assignment,subscribers \
  --report-dir reports/stage1/
```
