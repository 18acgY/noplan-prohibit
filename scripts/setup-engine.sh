#!/usr/bin/env bash
# 建/更新引擎的 Python 环境。仓库里不含 .venv，任何机器 clone 后跑这一个脚本即可。
#
#   ./scripts/setup-engine.sh              # 默认建在 <仓库>/engine/.venv
#   ENGINE_VENV=/opt/venvs/ta ./scripts/setup-engine.sh   # 建在仓库外（仓库搬家不用重建）
#
# 需要：python3.11+、pip 能访问索引（可用 PIP_INDEX_URL 指到镜像）。
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
engine="$repo/engine"
venv="${ENGINE_VENV:-$engine/.venv}"
index="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
python="${PYTHON:-python3}"

if [ ! -f "$engine/pyproject.toml" ]; then
  echo "找不到引擎：$engine/pyproject.toml 不存在" >&2
  exit 1
fi

echo "仓库: $repo"
echo "引擎: $engine"
echo "环境: $venv"

if [ ! -x "$venv/bin/python" ]; then
  echo "== 创建 venv =="
  mkdir -p "$(dirname "$venv")"
  "$python" -m venv "$venv"
fi

echo "== 安装依赖（引擎，akshare 版本由 pyproject.toml 钉住）=="
"$venv/bin/python" -m pip install -q --upgrade pip -i "$index"
"$venv/bin/python" -m pip install -q -e "$engine" -i "$index"
# akshare 已写进 engine/pyproject.toml 并锁定版本：它的接口在次版本里会改名，
# 不锁就会在重装环境时把线上的 A 股/ETF 数据链打断（2026-11-21 就这样断过一次）。

echo "== 验收 =="
"$venv/bin/python" - <<'PY'
import sys

import akshare  # noqa: F401
import tradingagents
import cli.main  # noqa: F401

print("python:", sys.version.split()[0])
print("tradingagents:", tradingagents.__file__)
print("akshare:", akshare.__version__)
print("engine OK")
PY

echo
echo "完成。下一步：把 TA_PYTHON 指到 $venv/bin/python（若不在默认位置）。"