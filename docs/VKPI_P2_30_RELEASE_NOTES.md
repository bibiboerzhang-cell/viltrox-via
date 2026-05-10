# V-KPI P2.30 Clean Package Release Notes

更新时间: 2026-05-10

## 交付定位

P2.30 是 Viltrox Marketing / V-KPI 的团队交付包刷新轮次，不新增业务功能，不继续拆分文件。

本轮目标:

- 基于当前 git HEAD 生成源码级纯净包。
- 给团队提供可读的 release notes。
- 对交付包做密钥、缓存、运行数据和大文件扫描。
- 保持 P2 回归基线通过。

## 当前功能边界

已验收的 P2 能力:

- Dashboard / KOL / Project / Link / Cost / KPI ledger。
- Industry data / platform crawler registry。
- Comments / sentiment / pillars / weekly reports。
- Provider settings / platform crawl budget gates。
- UI-facing API route acceptance gate。
- Browser login compatibility QA。
- Project creation -> existing KOL -> product selection -> detail drawer evidence flow。
- Single-account refresh guard for YouTube and non-YouTube platforms。

## 本轮新增或更新

- `docs/VKPI_P2_30_RELEASE_NOTES.md`
  - 本文件，作为团队交付包说明。
- `docs/VKPI_P2_RELEASE_STATUS.md`
  - 将 P2.30 加入收口表。
  - 将全量 smoke 基线保留为 `77/77`。
  - 将下一步切到 P2.31 可选 live 小样本或后续 P3 规划。

## 交付包策略

P2.30 采用源码级纯净包，不使用旧 `scripts/package_share.sh`。

原因:

- 旧脚本输出名仍是 `viltrox-2.0`，不符合当前 Viltrox Marketing / V-KPI 命名。
- 旧脚本会把 `frontend/dist` 放回包里；P2.30 交付目标是开发源码包，不需要 build artifact。
- `git archive HEAD` 只包含 git 跟踪文件，天然排除 `.env`、runtime、uploads、node_modules、缓存和本地数据库。

包内应包含:

- `backend/`
- `frontend/`
- `scripts/`
- `migrations/`
- `docs/`
- `tests/`
- `deploy/`
- `requirements.txt`
- `frontend/package.json`
- `frontend/package-lock.json`
- `.env.example`
- README / deployment / engineering docs

包内不应包含:

- `.env` 或任何 `.env.*` 真实密钥文件。
- `.git/`
- `.venv/`
- `node_modules/`
- `frontend/dist/`
- `runtime/`
- `uploads/`
- `*.db` / SQLite runtime files。
- Postgres runtime data / WAL。
- `.pytest_cache/`、`__pycache__/`、`.DS_Store`。

## 验证命令

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

npm run build --prefix frontend
./scripts/run_smoke.sh --all
git diff --check
```

期望:

- Frontend build PASS。
- Full smoke PASS `77/77`。
- Diff whitespace check PASS。

## 交付包使用方式

```bash
cd ~/Downloads
unzip viltrox-marketing-v3-p2.30-clean-*.zip -d viltrox-marketing-v3-p2.30-clean
cd viltrox-marketing-v3-p2.30-clean/V-KPI-marketing

cp .env.example .env
# 按团队本地环境填写 DATABASE_URL / JWT_SECRET / provider keys

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm ci
npm run build
```

本地启动参考:

```bash
cd /path/to/V-KPI-marketing
./scripts/start_admin.sh

cd frontend
npm run dev
```

## 安全说明

- 交付包不包含真实 `.env`。
- 交付包不包含用户运行数据。
- provider key 只应在团队本地 `.env` 或 Secret Manager 中配置。
- 如团队要跑 live crawler，必须显式开启对应 smoke/live flag；默认 smoke 不消耗外部 API。

## 下一步

- P2.31: 如需要，对 Instagram / Bilibili / Xiaohongshu 各做一次显式 live 小样本，不纳入默认 CI。
- P3: 可进入团队接手后的功能迭代或部署硬化，不建议继续无边界拆分。
