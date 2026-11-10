#!/usr/bin/env bash
# 服务器冒烟：跑一次真实分析，记录墙钟与峰值内存。
# 用法：bash smoke.sh [YYYY-MM-DD]
set -euo pipefail

BASE="${TA_BASE:-/home/workspace}"
export TA_REPO="${TA_REPO:-$BASE/TradingAgents}"
export TA_DATA="${TA_DATA:-$BASE/.runtime}"
export PYTHONUTF8=1
export TZ=Asia/Shanghai

TICKER="${TICKER:-600009.SS}"
DATE="${1:-$(date +%F)}"
ANALYSTS="${TA_ANALYSTS:-market,news,fundamentals}"

mkdir -p "$TA_DATA/logs" "$TA_DATA/cache" "$TA_DATA/memory"
LOG="$TA_DATA/smoke-$(date +%Y%m%d-%H%M%S).log"

echo "ticker=$TICKER date=$DATE analysts=$ANALYSTS"
echo "TA_REPO=$TA_REPO"
echo "TA_DATA=$TA_DATA"
echo "log=$LOG"

cd "$TA_REPO"
/usr/bin/time -v "$TA_REPO/.venv/bin/python" "$BASE/relay_launcher.py" \
  --ticker "$TICKER" --date "$DATE" \
  --analysts "$ANALYSTS" \
  --no-checkpoint --save --no-show --no-html 2>&1 | tee "$LOG"

echo
echo "=== 最新报告目录 ==="
ls -dt "$TA_DATA"/logs/reports/*/ 2>/dev/null | head -3
echo "=== decision.md 前 8 行 ==="
latest="$(ls -dt "$TA_DATA"/logs/reports/*/ 2>/dev/null | head -1 || true)"
if [ -n "$latest" ]; then
  sed -n '1,8p' "${latest}5_portfolio/decision.md" 2>/dev/null || true
fi