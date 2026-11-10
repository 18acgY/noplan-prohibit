# 次日交易许可单（最小 demo）

用来约束股票交易里的「随手单」：**先填计划，再谈价格**。网页复刻你 Excel 的《次日交易许可单》，后端用 SQLite 存数据，服务端跑在 Linux + Docker 上。

> 当前是 demo：按你原表的**全部字段**和**公式**实现，但日期闸门（必须前一交易日填写）暂未启用，Excel 导出也暂未实现。

**2026-11-10 这版**：AI 分析报告改成 Markdown 渲染、分析过程显示真实阶段进度、排队中与运行中都能取消分析。

## 已实现

- 单密码登录（密码只放在环境变量里，不进代码）
- 新建 / 编辑 / 删除 / 列表 / 详情
- 许可单全字段：基本信息、交易逻辑、100 分评分、交易参数、触发/取消/退出条件、8 项情绪检查、承认错误信号
- 实时自动计算（页面上直接算，保存后服务端再按同一公式复核）：
  - 总分 = 大盘 + 板块 + 个股 + 组合相关性
  - 预计交易金额 = 买入价 × 计划数量
  - 止损距离 = |买入价 − 启动低点| / |买入价|
  - 风险（计划总额）= |买入价 − 启动低点| × 计划数量
  - 预期上涨空间 = 主压力位 / 买入价 − 1
  - 盈亏比 = (主压力位 − 买入价) / (买入价 − 启动低点)
  - 预算检查 = 计划风险额 ≤ 单笔风险上限 ? 预算内 : 超预算
- 自动预审（照搬 A38 公式的五段判断）：
  1. 日期闸门（demo 关闭）
  2. 资料完整性 → 红色「资料不完整：禁止交易」
  3. 情绪 8 项任一「是」→ 红色「纪律红灯：禁止交易」
  4. 开仓/加仓参数门槛（上涨空间 <15%、盈亏比 ≤2、止损点 ≥ 买入价、目标价 ≤ 买入价、超预算）→ 红色「买入/加仓参数未达个人门槛：禁止交易」
  5. 总分 <60 → 黄色「预审通过但禁止重仓：仅轻仓，严格按单执行」
  6. 否则 → 绿色「预审通过：仅按单执行」
- 「我的交易参数」可在线修改（上涨空间 15%、盈亏比 2、评分 60），改完立即影响预审
- 「交易纪律」页只读展示原表 10 条规则
- AI 分析：一键触发 TradingAgents，报告 Markdown 渲染，进度按真实阶段显示，排队中和运行中都能取消

## nginx 反向代理（学习中）

基础笔记见 [cache/teaching/docs/nginx-basics.md](cache/teaching/docs/nginx-basics.md)，起始配置见 [deploy/nginx/noplan.conf](deploy/nginx/noplan.conf)。

应用已改为只监听 `127.0.0.1:8080`，公网只开 nginx 的 80 端口。

## TradingAgents 分析集成

许可单详情页有「运行分析」按钮：按证券代码触发 TradingAgents，摘要回写详情页，可点开完整报告。

要点：

- 同 `ticker + 分析日` 已有成功结果时直接复用，不重复花钱
- 每日成功分析上限默认 10 次（`NOPLAN_DAILY_ANALYSIS_LIMIT`）
- 分析失败不自动重试，详情页给「重新分析」
- worker 每 30 秒心跳；离线时按钮禁用
- 排队中或运行中都可以「取消分析」；取消不消耗当日次数，之后可重新分析
- 分析结果不改变预审、不写台账，卡片固定注明“仅供参考”

### 分析进度（到哪一步了）

阶段：准备 → 分析师团队 → 研究辩论 → 交易计划 → 风控辩论 → 组合决策 → 整理报告。

链路：

1. `deploy/tradingagents/relay_launcher.py` 在 TA 进程内给
   `message_buffer.update_agent_status` 包一层，把 agent 状态追加到
   `NOPLAN_PROGRESS_FILE`（JSONL）；
2. `worker/analysis_worker.py` 边跑边 tail 这个文件，用 worker token 发到
   `POST /internal/analysis/{id}/progress`；
3. 页面每 3 秒轮询 `GET /analyses/{id}/status.json`，完成/失败时原地刷新。

只显示真实阶段和已用时间，不折算百分比或预计剩余（单次 8–20 分钟，估不准会误导）。

### 取消分析

- 排队中：直接置 `cancelled`，立即生效。
- 运行中：先打 `cancel_requested` 标记（不改状态，避免被 worker 的回报覆盖）；
  worker 每 5 秒查一次 `GET /internal/analysis/{id}/state`，看到标记就终止整个
  进程组（POSIX 先 SIGTERM，10 秒不退再 SIGKILL），然后回报 `cancelled`。
- 页面轮询看到标记会显示「取消中…」，状态落定后自动刷新。
- `cancelled` 不计入每日成功次数，也不挡住同 `ticker + 分析日` 的重新分析。
- 查询间隔用 `TA_CANCEL_POLL_SEC` 调（默认 5 秒），只在宿主机 worker 侧。

上线这版时，服务器上的 worker 也要同步更新；网站侧不用手工迁移，
`analyses.cancel_requested` 列会在下次启动时自动补上。

部署这版时，`relay_launcher.py` 与 `progress_hook.py` **两个文件都要**同步到服务器：

```bash
sudo cp deploy/tradingagents/relay_launcher.py /home/workspace/tradingagents/
sudo cp deploy/tradingagents/progress_hook.py  /home/workspace/tradingagents/
sudo systemctl restart noplan-worker
```

> 2026-11-10 修过 `relay_launcher.py` 的 `Path` 未导入问题（分析一启动就退出码 1），
> 现在由 `cache/tests/test_relay_launcher.py` 兜回归。

旧版本创建、还在 `queued/running` 的任务没有阶段字段；上线前把它们停掉（或重跑）：

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

### worker 部署（宿主机，systemd）

```bash
# 1. 引擎（见 deploy/tradingagents/README-m0.md），然后确认冒烟能跑通
# 2. 配置文件
sudo cp deploy/systemd/noplan-worker.env.example /etc/noplan-worker.env
sudo nano /etc/noplan-worker.env      # 填 TA_* 与 NOPLAN_WORKER_TOKEN（与网站 .env 一致）
sudo chmod 600 /etc/noplan-worker.env

# 3. systemd
sudo cp deploy/systemd/noplan-worker.service /etc/systemd/system/noplan-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now noplan-worker
sudo systemctl status noplan-worker --no-pager
sudo journalctl -u noplan-worker -f
```

首次启动后，详情页按钮从“离线”变为可点，说明心跳已通。

## 本地运行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
NOPLAN_PASSWORD=你的密码 \
NOPLAN_SECRET=随便一串随机字符 \
NOPLAN_DB=./data/noplan.db \
.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。

## 部署到 Ubuntu 24.04 云服务器

1. 装 Docker（Ubuntu 24.04）：

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # 重新登录后生效
   ```

2. 把本目录传到服务器（`git clone` 或 `scp -r` 都行）。

3. 配置密码：

   ```bash
   cd noplan-prohibit
   cp .env.example .env
   # 编辑 .env，把 NOPLAN_PASSWORD 改成你的登录密码
   ```

4. 启动：

   ```bash
   chmod +x deploy.sh
   ./deploy.sh
   ```

5. 放行端口（如果开了 ufw）：

   ```bash
   sudo ufw allow 8080/tcp
   ```

6. 浏览器访问 `http://<服务器公网IP>:8080`，输入第 3 步的密码。

> 你选了「先走 HTTP 明文」，所以没有 HTTPS。密码和数据都会明文经过网络，demo 阶段可接受；以后加个域名 + Caddy 就能自动上证书。

### 备份

数据就是一个文件：

```bash
cp data/noplan.db data/noplan.db.$(date +%F)
```

### 更新

```bash
git pull
bash deploy.sh        # deploy.sh 不一定带执行位，用 bash 跑
```

发版注意：

- 改了 `requirements.txt` 必须一起同步，否则容器会因缺包反复重启（这次就漏了 `markdown-it-py`）。
- 服务器直连 PyPI 容易超时，`Dockerfile` 默认用阿里云镜像，可用 `PIP_INDEX_URL` 覆盖。
- 网站和 worker 有接口依赖时，先更新网站、再重启 worker。

## 测试

```bash
.venv/bin/python -m pytest        # Windows: .venv\Scripts\python -m pytest
```

预审公式有 15 个单元测试，其余页面/接口/进度/取消/worker/launcher 测试加起来共 131 个
（其中 1 个进程组信号测试只在 POSIX 跑）。

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
- 单用户、HTTP 明文、无 CSRF 防护（单机自用够用，公开前要补）
- 证券代码不校验真伪
- worker 离线时，运行中的取消会停在「取消中…」，等 worker 回来才落定
- 阶段进度依赖 TA 侧 `progress_hook`，TA 升级后需确认 hook 仍能挂上

## 目录

```
app/
  main.py            # 路由、登录、表单解析、分析接口
  review.py          # 计算 + 预审（照搬 Excel 公式）
  db.py              # SQLite 与自动迁移
  progress.py        # 分析阶段表
  markdown_render.py # 报告 Markdown 渲染（禁 HTML、拦 javascript:）
  templates/         # 登录 / 列表 / 表单 / 详情 / 参数 / 纪律
  static/            # CSS + 表单实时计算 + 进度轮询
worker/analysis_worker.py   # 宿主机 worker：领任务、跑 TA、转发进度、执行取消
deploy/tradingagents/       # TA 侧 launcher 与进度钩子
deploy/systemd/             # worker 的 systemd 与 env 模板
deploy/nginx/noplan.conf    # 宿主机 nginx 站点配置
cache/             # 本地教学 / 测试 / 临时文件（gitignore，不部署）
Dockerfile
docker-compose.yml
deploy.sh
```