# P3.9H Browser Risky Button QA

Date: 2026-05-13
Workspace: /Users/bibiboer/Documents/V-KPI——marketing
Backup: /Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39h-browser-risky-buttons-20260513-020220.tar.gz

## Scope

This round uses the browser to click a controlled subset of previously risky buttons. It focuses on visible user feedback and obvious 500/fake-button behavior. It does not intentionally create production business records unless required.

## Browser Context

URL tested:

- `http://127.0.0.1:5173/#dataAnalysis`
- `http://127.0.0.1:5173/#projects`

Visible version status during test:

- FE: `33593dbc`
- BE: `checking`
- Known separate issue: `/health` still reports client/server version mismatch in previous checks.

## Results

| Area | Button / action | Browser result | Verdict |
|---|---|---|---|
| Header export | `导出 PDF` | Click showed `PDF 已就绪。`; no visible 500. | Real action, browser PASS. Need final download/open verification later. |
| Header export | `导出 CSV` | Click showed `CSV 已就绪。`; no visible 500. | Real action, browser PASS. Need final download/open verification later. |
| Header reports | `生成周报` | Click did not show 500, but also did not leave a clear success/failure message in the page after waiting. | Endpoint smoke PASS, browser UX incomplete. Needs visible toast/status/download confirmation. |
| Account detail | `刷新该账号` | Click showed blocking reason: `平台抓取开关未开启。`; no visible 500. | Real action, browser PASS. Block is configuration-state, not fake button. |
| Project page | Stage/cost buttons | Page currently has `0 条真实项目` and `暂无可推进项目`; no destructive click performed. | API smoke PASS, browser blocked by empty data state. Needs seeded/test project browser QA. |

## Important Findings

1. PDF and CSV are no longer fake from the browser perspective: both produce visible ready states.
2. Weekly report still has a UX gap: the backend path is smoke-covered, but the user gets no durable visible confirmation after clicking.
3. Account refresh behaves correctly under a closed crawl gate: the button is real and returns a blocking reason instead of failing silently.
4. Project stage/cost browser QA cannot be completed against the current page because there are no real project rows.
5. The page still has a version visibility issue (`BE checking` / prior `client_matches_server=false`), which can confuse QA because users cannot trust whether they see the newest frontend.

## Remaining Work

P3.9I should focus on two things only:

1. Add or expose a controlled test project path so browser QA can safely click:
   - create project
   - complete current step
   - register shipping/promotion cost
   - upload/view evidence
2. Improve weekly report UX:
   - show `正在生成周报...`
   - show `周报已生成` or a precise error
   - expose the download/open link visibly

## Acceptance Status

P3.9H controlled browser QA: PARTIAL PASS.

Passed:

- PDF click
- CSV click
- Account refresh gate click
- Project empty-state detection

Not fully accepted yet:

- Weekly report visible confirmation
- Project stage/cost real browser click with a seeded/test project
- Download file open verification
