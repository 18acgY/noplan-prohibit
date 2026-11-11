# 次日交易许可单（最小 demo）

用来约束股票交易里的「随手单」：**先填计划，再谈价格**。网页复刻你 Excel 的《次日交易许可单》，
FastAPI + SQLite 存数据，跑在 Linux + Docker 上；AI 分析由宿主机上的 worker 调 TradingAgents 完成。

> 当前是 demo：按你原表的**全部字段**和**公式**实现，但日期闸门（必须前一交易日填写）暂未启用，
> Excel 导出也暂未实现。单用户、HTTP 明文，只在自用范围内使用。

## 功能

### 许可单

- 单密码登录（密码只放环境变量，不进代码；会话用签名 Cookie）
- 新建 / 编辑 / 删除 / 列表 / 详情
- 全字段：基本信息、交易逻辑、100 分评分、交易参数、触发/取消/退出条件、8 项情绪检查、承认错误信号
- 实时自动计算（页面边填边算，保存时服务端用同一套公式再复核）：
  - 总分 = 大盘 + 板块 + 个股 + 组合相关性
  - 预计交易金额 = 买入价 × 计划数量
  - 止损距离 = |买入价 − 启动低点| / |买入价|
  - 风险（计划总额）= |买入价 − 启动低点| × 计划数量
  - 预期上涨空间 = 主压力位 / 买入价 − 1
  - 盈亏比 = (主压力位 − 买入价) / (买入价 − 启动低点)
  - 预算检查 = 计划风险额 ≤ 单笔风险上限 ? 预算内 : 超预算
- 自动预审（照搬 Excel A38 的五段判断）：
  1. 日期闸门（demo 关闭）
  2. 资料不完整 → 红色「资料不完整：禁止交易」
  3. 情绪 8 项任一「是」→ 红色「纪律红灯：禁止交易」
  4. 参数门槛不达标（上涨空间 <15%、盈亏比 ≤2、止损点 ≥ 买入价、目标价 ≤ 买入价、超预算）
     → 红色「买入/加仓参数未达个人门槛：禁止交易」
  5. 总分 <60 → 黄色「预审通过但禁止重仓：仅轻仓，严格按单执行」
  6. 否则 → 绿色「预审通过：仅按单执行」
- 「我的交易参数」可在线修改（上涨空间 15%、盈亏比 2、评分 60），改完立即影响预审
- 「交易纪律」页只读展示原表 10 条规则

### AI 分析

- 详情页「运行分析」：按证券代码触发 TradingAgents，摘要回写详情页，可点开完整报告
- 同 `ticker + 分析日` 已有成功结果时直接复用，不重复花钱
- 每日成功分析上限默认 10 次（`NOPLAN_DAILY_ANALYSIS_LIMIT`），只数 `done`
- 分析失败不自动重试，详情页给「重新分析」
- worker 每 30 秒心跳；离线时按钮禁用
- 报告 Markdown 渲染（标题、列表、表格、代码块正常显示）
- 进度按**真实阶段**显示：准备 → 分析师团队 → 研究辩论 → 交易计划 → 风控辩论 → 组合决策 → 整理报告；
  不给百分比，也不给预计剩余（单次 8–20 分钟，估不准会误导）
- 排队中和运行中都能「取消分析」；取消不消耗当日次数，之后可重新分析
- 分析结果不改变预审、不写台账，卡片固定注明“仅供参考”

### 运行截图

<img width="2549" height="1191" alt="image" src="https://github.com/user-attachments/assets/c0898ed4-a761-4c2a-9fd0-d1dbb49f8902" />

<img width="2549" height="1191" alt="image" src="https://github.com/user-attachments/assets/bb3ad1fb-021e-4f3d-a5f6-273f14322dba" />

<img width="2549" height="1191" alt="image" src="https://github.com/user-attachments/assets/8f1f561b-4435-49ae-b891-d24d1b130580" />



### 运维

- 应用只监听 `127.0.0.1:8080`，公网只开 nginx 的 80 端口（见 `deploy/nginx/noplan.conf`）
- 数据就是一个 SQLite 文件，备份即 `cp data/noplan.db`；建表与补列在启动时自动完成

## 项目结构

```
app/
  main.py            # FastAPI 入口：路由、登录、表单解析、分析接口
  review.py          # 计算与预审（照搬 Excel 公式），阈值来自 settings
  db.py              # SQLite 访问、建表与自动迁移、分析队列与取消
  analysis.py        # 证券代码 → ticker 映射、本地日期
  progress.py        # 分析阶段表、agent → 阶段映射
  markdown_render.py # 报告 Markdown 渲染（禁 HTML、拦 javascript:）
  templates/
    base.html        # 布局、导航
    login.html list.html form.html detail.html
    analysis.html    # 完整报告页
    settings.html discipline.html
    _progress.html   # 步骤条宏（详情页用 compact，报告页用完整版）
  static/
    app.css          # 样式，含报告排版与徽章
    form.js          # 表单实时计算
    progress.js      # 进度轮询、已用时间、取消中…
worker/
  analysis_worker.py # 宿主机 worker：心跳、领任务、跑 TA、转发进度、执行取消
deploy/
  nginx/noplan.conf              # 宿主机 nginx 站点配置
  systemd/noplan-worker.service  # worker 的 systemd 单元
  systemd/noplan-worker.env.example
  tradingagents/
    relay_launcher.py            # TA 进程内的启动器（取代 TA 自带 CLI 入口）
    progress_hook.py             # 把 TA 的 agent 状态写成 JSONL
    README-m0.md smoke.sh env.linux.example
Dockerfile            # python:3.12-slim + 可配置 pip 镜像源
docker-compose.yml    # 容器 noplan，127.0.0.1:8080->8000，挂载 ./data
deploy.sh             # 建 .env（如缺）并 docker compose up -d --build
requirements.txt
pytest.ini            # testpaths = cache/tests
cache/                # 教学笔记、测试、临时文件（.gitignore，不部署）
```

## 怎么实现的

### 总览

```
浏览器 ──80──> nginx ──127.0.0.1:8080──> Docker 容器(noplan, uvicorn)
                                              │
                                              └──> data/noplan.db (SQLite)

宿主机 noplan-worker (systemd) ──HTTP+token──> 容器内 /internal/* 接口
        │
        └── Popen ──> TradingAgents 的 venv
                       └── relay_launcher.py + progress_hook.py
```

网站只做「排队 + 展示」，真正的重活（一次 8–20 分钟）交给宿主机 worker，
因为 TradingAgents 有自己的 Python 环境和 API Key，不适合塞进同一个容器。

### 1. 许可单与预审

`form.js` 在浏览器里边填边算；提交后 `main.py` 解析表单，`review.py` 用**同一套公式**重算一遍，
不一致以服务端为准。结论与理由（`reasons`）一起渲染到详情页横幅。
门槛值存在 `settings` 表，改「我的交易参数」后立即生效。

### 2. 分析任务的状态机

```
queued ──claim──> running ──finish(done/failed)──> 结束
   │                  │
   └─取消────────────> cancelled <────取消后 worker 终止────┘
```

- `claim_next_analysis()` 用一条带子查询的 `UPDATE ... RETURNING` 领取最早的排队任务，
  同时写 `status='running'`、`started_at`、`worker_claimed_at`、`stage='prepare'`
- worker 结束时报 `done` / `failed` / `cancelled`，`finish_analysis()` 落库并写 `finished_at`

### 3. 入队、复用与额度

`enqueue_analysis()` 的规则：

1. 同 `ticker + analysis_date` 已有 `queued/running` → 直接返回那条，不新建
2. 每日 `done` 数量 ≥ 上限 → 抛 `DailyLimitError`，接口转 429
3. 否则插入一条 `queued`

取消后同一天同一只票可以直接再点「重新分析」，因为活跃任务只认 `queued/running`，
而额度只数 `done`。

### 4. worker 怎么跑一次分析

1. 另起线程每 `TA_HEARTBEAT_SEC`（默认 30 秒）发心跳，网站据此判断「在线/离线」
2. 主循环每 `TA_POLL_SEC`（默认 5 秒）调 `POST /internal/analysis/claim`
3. 领到任务后用 `Popen(start_new_session=True)` 起子进程，跑
   `TA_PYTHON TA_LAUNCHER --ticker ... --date ...`，stdout 写日志
4. `wait_process()` 每 `TA_CANCEL_POLL_SEC`（默认 5 秒）查一次
   `GET /internal/analysis/{id}/state`；超时或收到取消就终止**整个进程组**
   （POSIX 先 SIGTERM，10 秒不退再 SIGKILL）
5. 正常结束后从结果目录找 `decision.md`，解析 Rating / Executive Summary /
   Price Target / Time Horizon，连同报告全文调 `finish`

网络抖动时查询失败按「没取消」处理，不会误杀正在跑的分析。

### 5. 进度是怎么来的

1. `relay_launcher.py` 在 TA 进程里给 `message_buffer.update_agent_status` 包一层，
   把 agent 状态按行追加到 `NOPLAN_PROGRESS_FILE`（JSONL）
2. worker 起一个 tail 线程增量读这个文件，用 worker token 发到
   `POST /internal/analysis/{id}/progress`
3. `db.update_progress()` 把 agent 映射成阶段并**只前进不后退**，记录 `stage_detail`
   （例如研究辩论里谁在发言）
4. 页面每 3 秒轮询 `GET /analyses/{id}/status.json`，步骤条 + 已用时间原地更新；
   完成/失败/取消后自动刷新

钩子失败只写日志，绝不影响分析本身。

### 6. 取消是怎么做的

- **排队中**：直接 `status='cancelled'` 并写结束时间，立即生效
- **运行中**：只把 `cancel_requested=1`，不直接改状态（避免被 worker 随后的 finish 覆盖）；
  worker 下次轮询看到标记就杀进程组，再回报 `cancelled`，由 `finish_analysis()` 落库并清标记
- 页面看到 `cancel_requested` 立即显示「取消中…」，状态落定后刷新为「已取消」
- 已取消不消耗当日次数；步骤条停在取消时的阶段

`GET /internal/analysis/{id}/state` 只给 worker 用，需要 `NOPLAN_WORKER_TOKEN`。

### 7. 报告渲染

`markdown_render.py` 用 markdown-it-py，**关闭 HTML 解析**、拦掉 `javascript:` 链接，
作为 Jinja 过滤器 `markdown` 用在报告页；正文在 `.report.md` 容器里排版。

### 8. 数据库表与迁移

- `permits`：许可单全字段
- `analyses`：`status`、`rating`、`summary`、`target_price`、`horizon`、`full_report`、
  `error`、`run_dir`、`duration_s`、`created_at`、`started_at`、`worker_claimed_at`、
  `finished_at`、`stage`、`stage_detail`、`stage_started_at`、`progress_updated_at`、
  `cancel_requested`
- `settings`：三个门槛值
- `worker_state`：最后心跳时间与版本

`CREATE TABLE IF NOT EXISTS` 不会给旧库加列，所以 `init_db()` 里有个 `_migrate()`：
用 `PRAGMA table_info` 比对，缺哪列就 `ALTER TABLE` 补哪列。升级不用手工迁移。

### 9. 接口

页面与公开接口：`GET /`、`/login`、`POST /login`、`/logout`、`/permits`、`/permits/new`、
`POST /permits`、`/permits/{id}`、`POST /permits/{id}/edit`、`POST /permits/{id}/delete`、
`POST /permits/{id}/analysis`、`GET /analyses/{id}`、`GET /analyses/{id}/status.json`、
`POST /analyses/{id}/cancel`、`/settings`、`/discipline`、`/healthz`

worker 专用（`NOPLAN_WORKER_TOKEN`）：`POST /internal/worker/heartbeat`、
`POST /internal/analysis/claim`、`POST /internal/analysis/{id}/progress`、
`POST /internal/analysis/{id}/finish`、`GET /internal/analysis/{id}/state`

### 10. 安全边界

- 登录用签名 Cookie（`NOPLAN_SECRET`），所有页面与页面接口都要登录
- worker 接口用 Bearer token，与网站 `.env` 保持一致
- 取消接口的 `next` 只接受站内路径，防止跳转到外部站点
- Markdown 渲染禁 HTML、拦 `javascript:`
- 已知缺口：无 CSRF token、HTTP 明文、单用户——公开部署前必须补

## 本地运行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip
NOPLAN_PASSWORD=你的密码 \
NOPLAN_SECRET=随便一串随机字符 \
NOPLAN_DB=./data/noplan.db \
.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。本地没有 worker，点「运行分析」会提示服务离线。

## 部署到服务器

### 首次部署

1. 装 Docker（Ubuntu 24.04）：

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # 重新登录后生效
   ```

2. 把本目录传到服务器，配置密码：

   ```bash
   cd noplan-prohibit
   cp .env.example .env
   # 编辑 .env：NOPLAN_PASSWORD / NOPLAN_SECRET / NOPLAN_WORKER_TOKEN
   ```

3. 启动：

   ```bash
   bash deploy.sh        # deploy.sh 不一定带执行位，用 bash 跑
   ```

4. 放行端口（开了 ufw 的话）：`sudo ufw allow 80/tcp`（公网只开 nginx，应用只听 127.0.0.1:8080）

### 更新

```bash
git pull
bash deploy.sh
```

发版注意：

- 改了 `requirements.txt` 必须一起同步，否则容器会因缺包反复重启（踩过 `markdown-it-py`）；
- 服务器直连 PyPI 容易超时，`Dockerfile` 默认走阿里云镜像，可用 `PIP_INDEX_URL` 覆盖；
- 网站和 worker 有接口依赖时，**先更新网站、再重启 worker**（否则新 worker 调不到新接口）。

### worker（宿主机，systemd）

```bash
sudo cp deploy/systemd/noplan-worker.env.example /etc/noplan-worker.env
sudo nano /etc/noplan-worker.env      # TA_* 与 NOPLAN_WORKER_TOKEN（与网站 .env 一致）
sudo chmod 600 /etc/noplan-worker.env

sudo cp deploy/systemd/noplan-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noplan-worker
sudo journalctl -u noplan-worker -f
```

分析相关文件要放到宿主机：

```bash
sudo cp deploy/tradingagents/relay_launcher.py /home/workspace/tradingagents/
sudo cp deploy/tradingagents/progress_hook.py  /home/workspace/tradingagents/
```

`worker/analysis_worker.py` 建议与仓库同路径部署，systemd 的 `ExecStart` 指过去即可。
详情页按钮从「离线」变可点，说明心跳已通。

### 旧任务处理

带 `stage` 的版本上线前，把遗留的 `queued/running` 停掉（否则永远停在旧阶段）：

```bash
docker compose exec noplan python - <<'PY'
import os, sqlite3
conn = sqlite3.connect(os.environ.get("NOPLAN_DB", "/data/noplan.db"))
conn.execute(
    "UPDATE analyses SET status='failed', error='部署中断，请重新分析' "
    "WHERE status IN ('queued','running')"
)
conn.commit()
print("stopped:", conn.total_changes)
PY
```

### 备份

```bash
cp data/noplan.db data/noplan.db.$(date +%F)
```

## 测试

```bash
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest
```

共 131 个：预审公式 15 个，其余是页面/接口/进度/取消/worker/launcher 测试
（其中 1 个进程组信号测试只在 POSIX 跑）。

测试放在 `cache/tests/`（`.gitignore` 内，不进版本库），沙箱环境不能用 `tmp_path`，
测试统一在 `cache/tests/.db`、`cache/tests/.tmp` 下建 uuid 目录。

## 公式对照

| 单元格 | 公式 |
| --- | --- |
| B17 | `=SUM(B16,D16,F16,H16)` |
| H18 | `=F20/B20-1` |
| B21 | `=B20*H20` |
| D21 | `=ABS(B20-D20)/ABS(B20)` |
| F21 | `=ABS(B20-D20)*H20` |
| H21 | `=(F20-B20)/(B20-D20)` |
| H22 | `=IF(F22<=D22,"预算内","超预算")` |
| A38 | 五段 IF 预审结论 |

门槛值来自「我的交易参数」B6=0.15、B7=2、B8=60。

## 版本记录

- **2026-11-10**
  - 报告 Markdown 渲染（新增 `app/markdown_render.py`，禁 HTML、拦 `javascript:`）
  - 分析进度：7 个真实阶段 + 步骤条 + 3 秒轮询，不显示百分比或预计剩余
  - 取消分析：排队立即取消；运行中由 worker 杀进程组；不消耗当日次数
  - 修复 `relay_launcher.py` 的 `Path` 未导入（分析启动即退出码 1）
  - `Dockerfile` 改用可配置的 pip 镜像源 + 重试

## 已知限制 / 下一步

- 日期闸门未启用：demo 只记录填单日期，不判断「前一交易日」
- 暂无 Excel 导出归档
- 无台账页（实际成交、偏离、复盘）
- 单用户、HTTP 明文、无 CSRF 防护
- 证券代码不校验真伪
- worker 离线时，运行中的取消会停在「取消中…」，等 worker 回来才落定
- 阶段进度依赖 TA 侧 `progress_hook`，TA 升级后需确认 hook 仍能挂上

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。

本项目仅供个人研究与自用，不构成任何投资建议。
