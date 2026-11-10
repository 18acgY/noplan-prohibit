#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i "s/^NOPLAN_SECRET=.*/NOPLAN_SECRET=${secret}/" .env
  sed -i "s/^NOPLAN_WORKER_TOKEN=.*/NOPLAN_WORKER_TOKEN=${token}/" .env
  echo "已生成 .env，并写入随机 session 密钥与 worker token。"
  echo "请先修改 .env 里的 NOPLAN_PASSWORD（这就是登录密码），再重新执行本脚本。"
  echo "部署分析 worker 时，/etc/noplan-worker.env 里的 NOPLAN_WORKER_TOKEN 要用同一个值："
  echo "  ${token}"
  exit 1
fi

docker compose up -d --build
docker compose ps
echo
echo "启动完成：http://<服务器公网IP>:8080"
echo "数据文件：$(pwd)/data/noplan.db（备份直接复制这个文件）"