# Portable HTML build notes

- Delivery mode: portable HTML only.
- Audience: product stakeholders.
- Source artifact generated at: 2026-08-28T23:40:40Z.
- Source artifact URL: codex-sandbox://mcp-server-dataanalyticswidgets-d90d6b74b2c37858.web-sandbox.oaiusercontent.com/?app=skybridge&backgroundMode=transparent
- Preservation rule: all 19 blocks, 4 cards, 2 charts, 5 tables, 9 datasets, and 8 canonical sources are retained.
- Required structure mapping: title; Executive Summary; findings and visual evidence; recommendations; further questions; caveats.

## Chart map

| Section | Question | Family | Fields | Supported takeaway | Delivery |
|---|---|---|---|---|---|
| 本地多 SKU A/B | 新版是否提高本地证据覆盖 | Grouped bar | SKU × arm → evidence_survivors | 3 SKU 改善、1 持平、1 退化；不是精准率 | Native artifact chart in portable reader |
| 联网多 SKU A/B | 新版是否提高官方 YouTube 首轮幸存 | Grouped bar | SKU × arm → provider_survivors | 55mm 持平，DC-A1/EPIC 新版零召回；Strict 全零 | Native artifact chart in portable reader |

## Visual QA contract

- Both charts use a category-comparison bar family with a meaningful second dimension (`arm`).
- Both chart datasets retain query, latency, candidate, evidence, and strict-qualification context beyond plotted fields.
- Each chart has adjacent narrative interpretation and a neutral visual title.
- The portable builder owns light/dark tokens, semantic fallbacks, source affordances, responsive layout, and browser verification.
