# V-KPI 本地算力 Worker(Mac CLI)

把员工 Mac 变成安全的「视频类任务算力节点」:领活 → 本地执行 → 提交结果。
服务端契约:`/api/admin/vkpi/local-workers/*`(设备注册 / 心跳 / 租约 / 提交 / 列表)。

## 安全说明(先读这段)

- **本地永不持有长期 API key。** LLM / Apify / R2 等所有数据源 key 全部留在服务端;
  本 worker 从不下载、不缓存、不打印任何长期 key。
- **一切凭「短期任务 token」。** 每领到一个任务(lease),服务端签发一枚
  HMAC 短期 token(只对该 lease 有效,几分钟内过期)。worker 把它当不透明字符串,
  提交结果时带回;过期即作废,泄露也只影响单个任务的一次提交。
- **本地结果不直写业务表。** 提交只落服务端 lease/staging 表,服务端校验
  (token、hash、结构、URL 匹配)通过后,才由服务端校验桥走既有函数落库。
  本 worker 不直连数据库,不碰 evidence / kol_pool / 任何分数列。
- **`--token` 是员工登录 bearer**(访问 `/api/admin/*` 的身份凭证,和网页登录同源),
  不是数据源 key;只在内存里持有,不写入磁盘。`~/.vkpi_worker/device.json`
  只存设备档案,不含任何 token。
- **任务类型白名单只有四类**(worker 与服务端双侧硬编码,禁止扩类):
  `video_precheck` / `metadata_extract` / `download_frames` / `comment_clean`。

## 安装依赖

```bash
# 1. Python 3.10+(仓库 .venv 自带 requests;裸机则 pip install requests)
# 2. 系统命令(视频类任务需要;缺失时启动自检会用人话提示,并自动缩小可领任务范围)
brew install yt-dlp ffmpeg
```

墙内网络:worker 会自动读 `YTDLP_PROXY` 环境变量(或 `--proxy` 参数)给
yt-dlp 与 HEAD 请求走代理,和服务端管线同一习惯。

## 启动

```bash
cd /path/to/V-KPI——marketing
source .venv/bin/activate   # 或任何带 requests 的 Python 3.10+

# 1. 注册本机(一次即可;写 ~/.vkpi_worker/device.json)
python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> register --name my-mac

# 2. 开始领活循环(心跳 → 领任务 → 执行 → 提交;Ctrl-C 安全退出)
python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> run

# 3. 查看设备与在线态
python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> status
```

`--token` 也可用环境变量 `VKPI_WORKER_STAFF_TOKEN` 传入,避免出现在 shell 历史里。

### run 的常用参数

| 参数 | 说明 |
| --- | --- |
| `--interval 10` | 轮询间隔秒(默认 10) |
| `--once` | 处理完一个任务即退出(冒烟/调试) |
| `--max-loops N` | 最多轮询 N 轮后退出(cron 场景) |
| `--task-types a,b` | 手动限定可领类型(仍受四类白名单约束) |
| `--proxy http://...` | 代理(缺省读 `YTDLP_PROXY`) |

## 四类任务做什么

| task_type | 本地动作 | 产物 |
| --- | --- | --- |
| `video_precheck` | HTTP HEAD + `yt-dlp --simulate` 判可达性 | 结果 JSON(reachable / http_status / ytdlp_rc) |
| `metadata_extract` | `yt-dlp --dump-json` 提取元数据(不下载) | 白名单字段的 metadata JSON |
| `download_frames` | yt-dlp 下载 720p + ffmpeg 抽帧(1 帧/5 秒) | `~/.vkpi_worker/work/<lease_id>/` 下视频+帧图,`files_meta` 报 name/sha256/bytes |
| `comment_clean` | 清洗 payload 里的评论数组(去 HTML/重复/垃圾)+ 语言词表标注 | cleaned 数组 + 统计 |

`download_frames` 的文件留在本机,提交的只是哈希清单(`files_meta`);
服务端凭 sha256 校验一致性,后续取回走独立通道。工作目录可随时手动清理:
`rm -rf ~/.vkpi_worker/work/*`。

## 断网 / 中断行为

- 执行中任何异常都被捕获并作为失败结果诚实上报(带 `error_code`),不会炸循环。
- 提交失败(断网等)时结果暂存 `~/.vkpi_worker/pending/`,下轮自动补交;
  token 过期或 lease 已终态则放弃并清理(短期 token 模型的代价,符合预期)。
- Ctrl-C 中断:退出前会尝试补交一次暂存结果。

## 状态行含义

```
[HH:MM:SS] [空闲]   无任务
[HH:MM:SS] [运行中] lease=3 job=17 type=video_precheck
[HH:MM:SS] [完成]   lease=3 ... 提交 accepted=True validated=True
[HH:MM:SS] [失败]   lease=4 error_code=unreachable ...
```

常见 `error_code`:`tool_missing`(缺 yt-dlp/ffmpeg)、`bad_payload`(任务缺 url/comments)、
`unreachable`(预检不可达)、`ytdlp_failed` / `ffmpeg_failed` / `timeout`、
`submit_failed`(已暂存待重试)、`network_error`(整轮跳过)。
