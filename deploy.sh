#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i "s/^NOPLAN_SECRET=.*/NOPLAN_SECRET=${secret}/" .env
  echo "已生成 .env 并写入随机 session 密钥。"
  echo "请先修改 .env 里的 NOPLAN_PASSWORD（这就是登录密码），再重新执行本脚本。"
  exit 1
fi

docker compose up -d --build
docker compose ps
echo
echo "启动完成：http://<服务器公网IP>:8080"
echo "数据文件：$(pwd)/data/noplan.db（备份直接复制这个文件）"