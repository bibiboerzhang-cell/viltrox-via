# V-KPI P10-2 Learning Snapshot API

## Scope

P10-2 exposes the read-only learning snapshot through admin API.

## API

```text
GET /api/admin/vkpi/learning/snapshot
```

Permissions:

```text
require_tab("vkpi", "read")
```

## Files

```text
backend/app/api/routers/vkpi_learning.py
backend/app/main.py
docs/vkpi/p10-2-learning-snapshot-api.md
```

## Verified Result

```text
scenario=p10_learning_snapshot
gaps=5
provider_calls=False
write_db=False
ai_cost_before=0
ai_cost_after=0
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/learning_loop.py passed
python3 -m py_compile backend/app/api/routers/vkpi_learning.py passed
python3 -m py_compile backend/app/main.py passed
git diff --check passed
```

## Next

P10-3 can add a compact frontend snapshot panel, but scoring changes remain blocked until real feedback exists.
