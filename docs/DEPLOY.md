# 部署手册

一台干净的 Linux 服务器，从 clone 到能跑分析。整份文档只有一条主线，按顺序做即可。

> 代码一份：网站（`app/`）、worker（`worker/`）、分析引擎（`engine/`）都在同一个仓库里。
> 运行时数据**不在**仓库：数据库在 `data/`，引擎的记忆与报告在 `TA_DATA` 指向的目录。

---

## 0. 目录与端口约定

```
/opt/noplan-prohibit/          ← 仓库检出目录（本文统一用这个；你可换成 /home/workspace/noplan-prohibit）
├── app/   网站代码              ← docker 镜像里只装这一部分
├── worker/ 宿主机上的分析调度器
├── engine/ 分析引擎（TradingAgents 衍生版）+ 它自己的 .venv
├── deploy/ systemd / nginx / 启动器 / 冒烟脚本
├── scripts/setup-engine.sh
├── data/noplan.db               ← 网站数据库（docker 挂载）
└── .env                         ← 网站自己的密钥（不进 git）
```

| 端口 | 谁听 | 对外 |
| --- | --- | --- |
| 8000（容器内）/ 127.0.0.1:8080（宿主） | 网站 | 只走 nginx |
| 80 | nginx | 公网 |

引擎的数据目录（`TA_DATA`）**放在仓库外**，例如 `/var/lib/noplan/runtime`。
理由：它是 18 MB 的记忆 + 历史报告，每跑一次就变，不能进 git；搬家还会打断历史报告路径。

## 1. 系统依赖

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip docker.io docker-compose-plugin nginx
```

Python 需要 **3.11 以上**（引擎的 `requires-python >=3.11`）。

## 2. 取代码

```bash
git clone git@github.com:18acgY/noplan-prohibit.git /opt/noplan-prohibit
cd /opt/noplan-prohibit
```

仓库是私有的，用 SSH 地址；HTTPS 会要求账号密码。

## 3. 引擎环境（一次装好，约 500 MB）

```bash
cd /opt/noplan-prohibit
./scripts/setup-engine.sh
# 期望最后打印：engine OK
```

脚本做三件事：建 `engine/.venv`、`pip install -e engine`、补装 `akshare`
（`pyproject.toml` 没声明它，缺了会静默回退雅虎，A 股取价全失败）。

想放到仓库外（仓库搬家不用重建）：`ENGINE_VENV=/opt/venvs/ta ./scripts/setup-engine.sh`。

## 4. 引擎的模型配置

```bash
cp deploy/tradingagents/env.linux.example engine/.env
chmod 600 engine/.env
${EDITOR} engine/.env        # 填 OPENAI_COMPATIBLE_API_KEY，改模型名
```

引擎按 `TA_REPO` 的当前目录找 `.env`，所以它必须放在 **`engine/.env`**。
`TRADINGAGENTS_RESULTS_DIR` / `_CACHE_DIR` / `_MEMORY_LOG_PATH` 三条**必须**指向仓库外的数据目录，
例如：

```bash
TRADINGAGENTS_RESULTS_DIR=/var/lib/noplan/runtime/logs
TRADINGAGENTS_CACHE_DIR=/var/lib/noplan/runtime/cache
TRADINGAGENTS_MEMORY_LOG_PATH=/var/lib/noplan/runtime/memory/trading_memory.md
```

目录不用手建，引擎第一次写时会自己创建。

## 5. 网站（docker）

```bash
cd /opt/noplan-prohibit
cp .env.example .env
./deploy.sh          # 首次会生成随机密钥并退出，让你改密码
${EDITOR} .env       # 改 NOPLAN_PASSWORD
./deploy.sh          # 再跑一次，起容器
```

验收：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz   # 期望 200
```

`deploy.sh` 会把 `NOPLAN_WORKER_TOKEN` 打出来**一次**，下一步要用。

## 6. 分析 worker（systemd）

```bash
cp deploy/systemd/noplan-worker.env.example /etc/noplan-worker.env
chmod 600 /etc/noplan-worker.env
${EDITOR} /etc/noplan-worker.env
```

必须和你的实际情况对上的四个：

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `TA_REPO` | `/opt/noplan-prohibit/engine` | 引擎代码 |
| `TA_PYTHON` | `/opt/noplan-prohibit/engine/.venv/bin/python` | 第 3 步建的 |
| `TA_LAUNCHER` | `/opt/noplan-prohibit/deploy/tradingagents/relay_launcher.py` | 仓库内的启动器 |
| `TA_DATA` | `/var/lib/noplan/runtime` | **显式写，放在仓库外** |
| `NOPLAN_WORKER_TOKEN` | 与 `.env` 里一致 | 从第 5 步复制 |

装服务（service 文件里的 `WorkingDirectory` / `ExecStart` 也要对应你的检出目录）：

```bash
cp deploy/systemd/noplan-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now noplan-worker
```

**关键验收**——启动日志第一行会打印它实际用的路径：

```bash
journalctl -u noplan-worker -n 5 --no-pager | grep '\[worker\]'
```

```
[worker] api=http://127.0.0.1:8080 repo=/opt/noplan-prohibit/engine data=/var/lib/noplan/runtime timeout=30m
```

`repo` 和 `data` 两个都对才算配好。`data` 错了不会报错，只会静默用一个空记忆文件——
症状是「每次分析都像第一次」，这正是记忆功能最难发现的那种坏法。

## 7. nginx 反代

```bash
cp deploy/nginx/noplan.conf /etc/nginx/sites-available/noplan
ln -s /etc/nginx/sites-available/noplan /etc/nginx/sites-enabled/noplan
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

## 8. 上线验收（不花模型额度）

```bash
cd /opt/noplan-prohibit

# 1) 引擎能装能导入
engine/.venv/bin/python -c "import tradingagents, cli.main; print('import OK')"

# 2) 启动器能起来（只看参数解析）
engine/.venv/bin/python deploy/tradingagents/relay_launcher.py --help >/dev/null && echo "launcher OK"

# 3) 结算取价链路（A 股，不花 LLM；返回收益、alpha、持有天数、结算日）
engine/.venv/bin/python -c "
from tradingagents.memory import settlement
print(settlement.fetch_returns('600009.SS', '2026-11-09'))"

# 4) worker 与网站都活着
systemctl is-active noplan-worker
docker compose ps
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz
```

四条都对再谈跑真分析（真分析要花模型额度，跑之前先确认 `TA_TIMEOUT_MIN=30`）。

## 9. 备份（重要：数据不在 git 里，这是唯一副本）

```bash
tar czf /var/backups/noplan-$(date +%F).tgz \
  /opt/noplan-prohibit/data/noplan.db \
  /var/lib/noplan/runtime
```

放进 cron 每天一次，保留 14 天。恢复就是解包到原位后重启：

```bash
systemctl restart noplan-worker && docker compose restart noplan
```

## 10. 回滚

| 场景 | 做法 |
| --- | --- |
| 网站镜像坏了 | `git checkout <上一个提交> && ./deploy.sh` |
| 引擎升级坏了 | `git checkout <上一个提交> -- engine/` → 重启 worker |
| worker 配置坏了 | 恢复 `/etc/noplan-worker.env.bak-日期` → `systemctl restart noplan-worker` |
| 数据坏了 | 解包第 9 步的备份 |

## 11. 排错

| 症状 | 先看哪里 |
| --- | --- |
| 页面停在「准备」，分析其实在跑 | `progress_hook` 挂不上（引擎升级后常见）；看 `journalctl -u noplan-worker` |
| 页面评级/目标价空白，报告正文正常 | `worker/analysis_worker.py` 的字段正则没匹配上新的模型写法 |
| 分析永不产生「教训」 | `TA_DATA` 配错，或报告头出现 `could not be settled` |
| 取价报「数据不可用」 | 引擎 venv 缺 `akshare`，或数据源限流（东财限流会降级到新浪，属正常） |
| 第二个分析一直排队 | 单 worker 串行，这是设计；最长等 30 分钟 |

## 12. 与旧布局的差异（2026-11-21 起）

| | 旧 | 新 |
| --- | --- | --- |
| 引擎代码 | `/home/workspace/tradingagents/TradingAgents` | `<仓库>/engine` |
| 启动器 | `/home/workspace/tradingagents/relay_launcher.py`（手工拷） | `<仓库>/deploy/tradingagents/relay_launcher.py` |
| 引擎 venv | 在引擎旁边，手建 | `scripts/setup-engine.sh` 建 |
| 运行时数据 | `/home/workspace/tradingagents/.runtime` | 不变（显式 `TA_DATA`） |

老服务器迁移时，`TA_DATA` 保持老路径即可，历史报告与记忆一条都不用搬。