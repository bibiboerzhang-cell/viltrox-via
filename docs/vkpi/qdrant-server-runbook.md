# Qdrant server runbook(本地文件后端 → docker Qdrant server)

架构 A1 · W1 速赢之一。**本文只写运维步骤,不改代码**:代码侧的 `QDRANT_URL` 分支早已存在
(`backend/app/domains/kol/profile_recall.py:_qdrant_client`、`scripts/build_kol_profile_index.py`、
`backend/app/services/via/vector_memory_backends.py`),切换就是一条环境变量 + 一次数据迁移。

## 0. 为什么要切

| 现象 | 证据 | 根因 |
|---|---|---|
| 智能搜索并发下静默降级成文本召回 | `profile_recall_storage.py:226`:`Storage folder ... already accessed by another instance`(实测 6 并发撞锁) | 内嵌 Qdrant(`QdrantClient(path=...)`)是**单进程文件锁**,16 车道 + web 2 进程共享同一目录 `runtime/vkpi_qdrant` |
| 召回整段降级 Errno 30 | `profile_recall_contract.py:20`(2026-07-26 起 14 次) | 发布树只读 + 沙箱 `ReadOnlyPaths=/opt/viltrox-2.0/runtime`,只靠 `ReadWritePaths=/opt/viltrox-2.0/runtime/vkpi_qdrant` 一个豁免口 |
| 搜索慢 | 24 秒里 20.3 在线(记忆 2026-08-31) | 文件后端无服务端并发,每个进程各自开库 |

Server 模式下所有进程走 HTTP 到同一个 Qdrant 进程,锁与只读问题一并消失。

## 1. 涉及的集合与路径(切换前先盘点)

| 用途 | 集合名 | 本地路径(切换前) | 由谁写 |
|---|---|---|---|
| KOL 画像向量召回(智能搜索主链) | `vkpi_kol_profile_index_v1`(1536 维 cosine,`text-embedding-3-small`) | `/opt/viltrox-2.0/runtime/vkpi_qdrant`(`VKPI_KOL_QDRANT_PATH` > `VKPI_RUNTIME_DATA_DIR/runtime/vkpi_qdrant` > 仓库 `runtime/`) | `scripts/build_kol_profile_index.py build` / `expand_kol_profile_index.py`(每日) |
| VIA 记忆(AI 助手会话记忆) | `via_memory`(`QDRANT_COLLECTION`,默认 `via_memory`) | `QDRANT_LOCAL_PATH`,默认 `data/via_qdrant` | `vector_memory_backends.py` |
| DSAR 擦除 | 复用 `vkpi_kol_profile_index_v1` | 同上 | `dsar_erasure.py`(复用 `_qdrant_client()`) |

**注意**:`QDRANT_URL` 是全局开关——设了以后上面三处全部走 server(`config.py:349`)。没有「只切 KOL 不切 VIA」的半开关;两个集合都要迁(VIA 记忆允许丢,见 §4 选项 C)。

## 2. 起 Qdrant server(同机 docker,只绑回环)

```bash
# 在 prod 主机(root)执行
sudo mkdir -p /opt/vkpi-qdrant/storage
sudo chown 1000:1000 /opt/vkpi-qdrant/storage        # 镜像内 qdrant 用户 uid 1000
docker pull qdrant/qdrant:v1.12.4                     # 钉版本(写本文时的稳定版);升级另开班车
# key 先落到 root 私有文件,再喂给容器;不要直接内联在 shell 历史里
( umask 077 && openssl rand -hex 24 | sudo tee /root/vkpi-qdrant.key >/dev/null )
docker run -d --name vkpi-qdrant --restart unless-stopped \
  -p 127.0.0.1:6333:6333 -p 127.0.0.1:6334:6334 \
  -v /opt/vkpi-qdrant/storage:/qdrant/storage \
  -e QDRANT__SERVICE__API_KEY="$(sudo cat /root/vkpi-qdrant.key)" \
  qdrant/qdrant:v1.12.4
curl -s -H "api-key: $(sudo cat /root/vkpi-qdrant.key)" http://127.0.0.1:6333/collections   # 期望 {"result":{"collections":[]},...}
```

- **只绑 127.0.0.1**:Cloudflare 前置的公网口不暴露 6333;systemd 沙箱里的 web/车道单元
  `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` 允许回环 TCP,无需改单元。
- 内存:1802 条 × 1536 维 float32 ≈ 11 MB,加 HNSW 索引远小于 100 MB;不用给资源上限。
- 备份:`vkpi-backup-r2.timer` 目前**不包含** `/opt/vkpi-qdrant/storage`。切换后二选一:
  (a) 把该目录加进备份脚本(需改 `scripts/ops/systemd/vkpi-backup-r2.service`——非本车);
  (b) 接受「丢了就重建」:`build_kol_profile_index.py build` 全量重嵌成本 ≈ 1802 × ~500 token
  × $0.02/1M ≈ **$0.02**,几分钟跑完。推荐 (b),把重建命令写进 §6 回退表即可。

## 3. 环境变量(`/opt/viltrox-2.0/.env`,root:viltrox 0640)

```
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=<上一步的 key>
# QDRANT_COLLECTION 保持默认 via_memory;KOL 集合名是代码常量,不用配
```

- `.env` 由 web 单元、16 车道单元、redis worker、sync/sentinel timer 一起 `EnvironmentFile=` 读取,
  **改完必须重启**这些单元才生效(长命进程不认新 env——记忆「长命进程不认新闸」)。
- `.env` 不随 rsync 走(部署排除 `.env*`),这两行是 prod 手工项,本地 `.env` 若也要切请单独设。
- 车道覆盖文件 `/etc/vkpi/vkpi-lane-overrides.env` **不要**放 QDRANT_*:那是数字白名单,部署会整文件盖回。

## 4. 数据迁移(本地文件 → server)

三种做法按可靠性排序;**A 是默认推荐**。

### A. 全量重建(最干净,$0.02,推荐)

```bash
cd /opt/viltrox-2.0/current
sudo -u viltrox env QDRANT_URL=http://127.0.0.1:6333 QDRANT_API_KEY=<KEY> \
  /opt/viltrox-2.0/.venv/bin/python -B scripts/build_kol_profile_index.py build
sudo -u viltrox env QDRANT_URL=http://127.0.0.1:6333 QDRANT_API_KEY=<KEY> \
  /opt/viltrox-2.0/.venv/bin/python -B scripts/build_kol_profile_index.py stats
```

- `build` 走 `ensure_collection`(不存在则建 1536/cosine),`ON CONFLICT` 幂等写 `vkpi_kol_profile_index_entries`;
  同一 `profile_text_hash` 会复用,已嵌过的行不会重复计费。
- 本地文件目录**原样保留**,不动——它就是回退点。
- LLM 走代理:该脚本读 `.env` 的 `HTTPS_PROXY`,prod 直连可达则无需额外设置。

### B. 点位搬运(不重嵌,零 LLM 花费;需要短暂独占本地目录)

本地文件后端是单实例锁,搬运期间**必须先停所有会打开该目录的进程**(web + 16 车道 + `expand_kol_profile_index` 定时任务),否则搬运脚本自己就会撞 `already accessed by another instance`。因此 B 的停机窗口 ≈ 一次发布,通常不如 A 划算;只在 A 的 embedding 供应商不可达时用。

```python
# 一次性脚本,在 prod 用 .venv 跑;源只读(scroll),目标 upsert;不改任何 PG 表
from qdrant_client import QdrantClient
from qdrant_client.http import models as m
SRC = QdrantClient(path="/opt/viltrox-2.0/runtime/vkpi_qdrant")
DST = QdrantClient(url="http://127.0.0.1:6333", api_key="<KEY>")
COL = "vkpi_kol_profile_index_v1"
if not DST.collection_exists(COL):
    DST.create_collection(COL, vectors_config=m.VectorParams(size=1536, distance=m.Distance.COSINE))
offset, moved = None, 0
while True:
    points, offset = SRC.scroll(COL, limit=256, offset=offset, with_vectors=True, with_payload=True)
    if not points: break
    DST.upsert(COL, points=[m.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points], wait=True)
    moved += len(points)
    if offset is None: break
print("moved", moved, "server_count", DST.count(COL, exact=True).count, "source_count", SRC.count(COL, exact=True).count)
```

验收:`server_count == source_count`,且等于 PG `SELECT COUNT(*) FROM vkpi_kol_profile_index_entries WHERE status='indexed'`(以脚本 `stats` 输出为准)。

### C. VIA 记忆集合 `via_memory`

会话记忆是可再生的辅助数据;切换时**允许清空**:server 上不存在该集合时 `vector_memory_backends.py` 会 `ensure_collection` 自建。若要保留,用 §4B 同样的 scroll/upsert 把 `data/via_qdrant` 的 `via_memory` 搬过去。

## 5. 切换顺序(建议放在一次班车之后,不与发布同窗)

1. §2 起 server → `curl /collections` 200。
2. §4A 重建(此时 web/车道仍在用本地文件,互不影响——重建脚本只连 server)。
3. `stats` 数量与 PG 对上。
4. §3 写 `.env` 两行。
5. 重启读 `.env` 的单元(**不要用部署脚本**,那会整站停机;这里只滚动重启):
   ```bash
   sudo systemctl restart viltrox-2.0-test.service
   sudo systemctl restart vkpi-worker-interactive.service
   for i in $(seq 1 15); do sudo systemctl restart vkpi-worker-bulk@$i.service; sleep 5; done
   sudo systemctl restart vkpi-redis-worker.service
   ```
   车道 `TimeoutStopSec=1300`,正在跑的任务会跑完再停;逐条 restart 让 bulk 不空档。
6. 验收:
   - `journalctl -u viltrox-2.0-test -u vkpi-worker-interactive --since -10m | grep -i "already accessed\|Errno 30"` → 0 行;
   - 智能搜索一条正常 query,`diagnostics` 里 `vector_recall` 非降级(不再是纯文本兜底);
   - `curl -s -H "api-key: <KEY>" http://127.0.0.1:6333/collections/vkpi_kol_profile_index_v1 | jq .result.points_count` 与 `stats` 一致;
   - 6 并发搜索(本地 `scripts/benchmark_kol_smart_local_runtime.py` 口径)不再出现撞锁降级。
7. 观察 24h 后,把 `runtime/vkpi_qdrant` 目录留作回退点,**不删**。

## 6. 回退

| 场景 | 动作 |
|---|---|
| server 起不来 / 集合空 | `.env` 删掉 `QDRANT_URL`/`QDRANT_API_KEY` 两行,重启 §5 步 5 的单元 → 代码自动回到 `QdrantClient(path=...)`,本地目录未动 |
| server 数据坏 | `docker stop vkpi-qdrant && rm -rf /opt/vkpi-qdrant/storage/*` → 重跑 §4A(≈$0.02) |
| 想彻底停用 | `docker rm -f vkpi-qdrant`;`.env` 清两行;重启单元 |

回退不涉及 PG:`vkpi_kol_profile_index_entries.qdrant_point_id` 在 A/B 两种迁法下都不变(point id = `uuid5(collection:kol_pool_id:text_hash)`,与后端无关)。

## 7. 本车不做 / 后续班车

- **不改代码**:`_qdrant_client()`、单元模板 `ReadWritePaths=runtime/vkpi_qdrant`、`build_kol_profile_index.py` 全部原样。
  切完 server 后那条 `ReadWritePaths` 可以删(收紧沙箱),放后续班车。
- 备份脚本纳入 `/opt/vkpi-qdrant/storage` 或正式采用「丢了重建」口径——见 §2。
- Qdrant 独立小机(A1 可选项)不在 W1:同机 docker 已解决锁与只读问题;跨机只在主机 CPU 成为瓶颈时再议。
- 健康页暂无 Qdrant 探针:`/health?deep=1` 不含 Qdrant;需要时在 `main_health.build_deep_health_payload` 加只读 `GET /collections` 探针(另开刀)。
