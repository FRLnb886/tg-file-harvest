# tg-file-harvest

GitHub Actions 定时采集 Telegram 频道文件 → Cloudflare R2 存档 → GitHub Pages 静态索引。

## 数据流

```
cron → collect.yml → collector/main.py
                        ├─ 读 state（R2，游标 + 去重表）
                        ├─ poller 按 min_id 增量拉消息
                        ├─ naming 提取文件名
                        ├─ downloader 下载到临时盘并算 sha256
                        └─ storage_r2 上传（同 key 已存在则跳过）
                                ↓
                   scripts/build_manifest.py → web/data/manifest.json（提交回仓库）
                                ↓
                             GitHub Pages 渲染
```

状态和文件都在 R2，仓库只提交 `web/data/manifest.json`，前端不接触任何凭据。

## 目录

| 路径 | 职责 |
| --- | --- |
| `.github/workflows/collect.yml` | 每 30 分钟采集一轮，串行、10 分钟超时、可回写 manifest |
| `.github/workflows/prune.yml` | 每周按 `retention_days` 清理过期记录与 R2 对象 |
| `.github/workflows/web-deploy.yml` | 把 `web/` 发布到 GitHub Pages；定时任务用 `GITHUB_TOKEN` 提交触发不了 push，所以额外挂 `workflow_run`，且只在 manifest 真变了时部署 |
| `collector/main.py` | 编排：单频道异常隔离、逐频道落盘状态 |
| `collector/config.py` | `settings.yaml` + `channels.yaml` + 环境变量合并与校验 |
| `collector/models.py` | `Channel` / `FileRecord` / 各配置数据类 |
| `collector/store.py` | 游标、去重表、FloodWait 冷却、保留期裁剪 |
| `collector/telegram_client.py` | Telethon 封装：StringSession、限速、FloodWait、重连 |
| `collector/poller.py` | 增量拉取（`min_id`，从旧到新）与入参过滤 |
| `collector/naming.py` | 文件名提取优先级链 + 规范化 |
| `collector/archive.py` | zip 安全解压（挡目录穿越/符号链接/zip bomb） |
| `collector/downloader.py` | 大小预检、`.tmp_` 下载、sha256、原子改名 |
| `collector/storage_r2.py` | R2 上传、直链、幂等头检查、批量删除 |
| `collector/notifier.py` | 可选 Bot 通知，失败只记日志 |
| `scripts/login.py` | 本地一次性生成 `TG_SESSION_STRING` |
| `scripts/build_manifest.py` | state → 前端 manifest，内容不变则不写文件 |
| `scripts/verify.py` | 离线自检；`--telegram` / `--r2` 做真实探测 |
| `panel/` | 本地控制台后端（FastAPI）：账号登录、频道与关键词、触发采集、运行记录与日志 |
| `web/` | 前端控制台（概览/文件库/频道/账号/日志）；无后端时自动降为静态只读 |

## 本地面板（可选）

`web/` 既是 Pages 的静态页，也是本地面板的界面。启后端后就能在网页里登录账号、改频道/关键词、点按钮采集、看运行日志：

```bash
pip install -r collector/requirements.txt -r panel/requirements.txt
python -m panel            # 默认 http://127.0.0.1:8787
```

启动时会打印访问令牌（也存在 `work/panel-token`，权限 600），首次打开页面粘贴进去即可。面板默认只监听本机，需要远程访问就自己加反向代理（建议再叠一层认证）。

- 登录账号时 session 写在 `.env`（权限 600），面板不把 session 发给浏览器
- 频道与关键词表单直接改写 `config/channels.yaml`，保存前用采集侧同一套校验，失败自动回滚
- 「立即采集」就是在本机跑一次 `collector.main`，输出写到 `work/logs/collect-<时间>.log`
- 没有后端（例如 GitHub Pages 上）时页面自动降级为只读文件库，只读 `web/data/manifest.json`

## 部署

> 本目录就是仓库根目录：`.github/workflows/` 必须直接在仓库根下，否则 GitHub 不会注册这些工作流。

1. 本地 `pip install -r collector/requirements.txt`，`python scripts/login.py` 生成 session。登录成功后三项凭据会写进项目根 `.env`（`collector` 和 `scripts` 启动时自动读取，已存在的环境变量优先，所以 Actions 里的 Secrets 不会被覆盖）。
2. 仓库 Secrets 配置：`TG_API_ID`、`TG_API_HASH`、`TG_SESSION_STRING`、`R2_ACCOUNT_ID`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_BUCKET`、`R2_PUBLIC_BASE`，通知可选 `BOT_TOKEN`、`BOT_CHAT_ID`。
3. 填 `config/channels.yaml`（频道 username 或数字 id），需要时改 `config/settings.yaml`。
4. 设置 → Pages → Source 选 GitHub Actions。
5. 先 `python -m collector.main --config config/ --dry-run --limit 3` 验证文件名提取；再跑 `python scripts/verify.py --telegram --r2`。

## 运维要点

- 单账号串行；`rate_limit` 在 Actions 出口 IP 上建议 1.5~2 秒，命中 FloodWait 会写冷却，下次 cron 直接跳过。
- `--limit N` 可限制单轮入库数量，避免首次跑满 10 分钟超时。
- 对象 key 为 `files/<channel>/<sha 前 12 位>/<文件名>`，同内容天然幂等；下载时通过 `Content-Disposition` 保留原始文件名。
- 频道的 `include_keywords` 非空时，只有文件名/描述命中其中任一关键词才采集；`exclude_keywords` 命中即跳过。
- `latest_only: true`：只取频道里**最新一个**匹配文件，每轮只检查是否有更新（追版本用，不扫历史）。
- `extract_zip: true`：zip 在服务器解压，包内每个文件各自上传并生成直链，manifest 里逐个展开；zip 本体不上传。解压会挡目录穿越、符号链接、超大/超多文件。
- 失败不再静默跳过：单个文件失败会保留游标、下次运行重试，连续失败 3 次（`MAX_ATTEMPTS`）后放弃并推进游标，同时留下 `failed` 记录和 `attempts` 次数。
- 每处理 50 条消息落盘一次状态，首次跑大频道被 Actions 10 分钟超时杀掉后，下一轮能接着游标继续，不用从头再来。
- 公开桶意味着「谁有链接谁下」：靠不可枚举的 key、`noindex`、必要时 Cloudflare Access 或签名 URL 控制。
- session 泄露等同账号被接管；最稳的做法是采集跑在自有机器，Actions 只负责构建前端。
