# M0：把 TradingAgents 迁到 Linux 服务器并跑通一次

> 目标：在服务器上**不依赖 DSH** 跑通一次分析，拿到真实耗时和峰值内存，用来定超时。
> 本目录的 `relay_launcher.py` 是跨平台版：路径走 `TA_REPO` / `TA_DATA`，`winreg` 只在 Windows 生效。

## 0. 解压

```bash
cd /home/workspace
tar -xzf tradingagents-freeze-20261110.tar.gz --strip-components=1
ls
# 期望看到：TradingAgents/  relay_launcher.py  smoke.sh  .env.linux.example
```

## 1. 看资源，配 zram（2C2G 推荐，替代大盘 swap）

```bash
free -h; nproc; df -h /
```

zram 用 CPU 压缩内存当 swap，不写磁盘，避免云盘 IO 被换页打满、拖死 SSH 登录：

```bash
sudo apt-get install -y zram-tools
sudo tee /etc/default/zramswap >/dev/null <<'EOF'
ALGO=zstd
PERCENT=75
PRIORITY=100
EOF
sudo systemctl restart zramswap
zramctl; swapon --show; free -h
```

再留 1 GB 磁盘 swap 兜底（优先级 10，zram 满了才会用）：

```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon -p 10 /swapfile
echo '/swapfile none swap sw,pri=10 0 0' | sudo tee -a /etc/fstab

printf 'vm.swappiness=100\nvm.page-cluster=0\n' | sudo tee /etc/sysctl.d/99-zram.conf
sudo sysctl --system
```

冒烟时另开窗口看有没有 thrash：`vmstat 1 5`（`si`/`so` 持续很高就是换页严重）、`zramctl`（看压缩后实际占用）。

## 2. Python 3.12 venv + 依赖

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv time

cd /home/workspace/TradingAgents
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e . --index-url https://pypi.tuna.tsinghua.edu.cn/simple
# akshare 不在 pyproject 依赖里，必须显式装（版本与本机一致）
pip install akshare==1.20.21 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

## 3. 配置 .env

```bash
cd /home/workspace/TradingAgents
cp /home/workspace/.env.linux.example .env
nano .env          # 填 OPENAI_COMPATIBLE_API_KEY（从本机 .env 复制）
chmod 600 .env
```

`TRADINGAGENTS_RESULTS_DIR=/home/workspace/.runtime/logs`，报告会落在它下面的 `reports/`。

## 4. 冒烟（会真花一次 LLM 费用）

```bash
cd /home/workspace
chmod +x smoke.sh
bash smoke.sh 2026-11-10
```

`/usr/bin/time -v` 会输出 `Elapsed (wall clock)` 和 `Maximum resident set size`，日志同时存到 `.runtime/smoke-*.log`。

## 5. 回贴这些给我

```bash
tail -n 40 /home/workspace/.runtime/smoke-*.log
ls /home/workspace/.runtime/logs/reports/
sed -n '1,6p' /home/workspace/.runtime/logs/reports/*/5_portfolio/decision.md
free -h
df -h /
```

我要的四个数：**墙钟耗时、峰值内存、报告路径、评级是否正常**。

## 常见错误

| 现象 | 处理 |
|---|---|
| `No module named akshare` | 回第 2 步显式安装 |
| `No module named tradingagents` | 在仓库根目录执行，且先 `pip install -e .` |
| `400 Non-streaming requests are not allowed` | 没用 `relay_launcher.py` 启动 |
| `503 Service temporarily unavailable` | relay 模型波动；换 `deepseek-v4.1-flash-1017` 或 `glm-5.3` 重试一次 |
| `YFRateLimitError` / Yahoo 失败 | A 股走 akshare，可忽略；不影响主链路 |
| `当前 IP 被临时限流` | 东方财富限流；等 5 分钟，**不要循环重试** |
| `python3.12-venv` 找不到 | 先 `apt-get install python3.12-venv` |

## 冻结说明

- 包内 `TradingAgents/` 是当前工作区快照（含未提交的 akshare vendor 与测试）
- `local-changes.patch` 是相对官方 v0.7.1 的差异，供以后 `git pull` 后重放
- `TradingAgents/.env` **没有**打进包，按第 3 步单独创建