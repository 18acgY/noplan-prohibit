# 次日交易许可单（最小 demo）

用来约束股票交易里的「随手单」：**先填计划，再谈价格**。网页复刻你 Excel 的《次日交易许可单》，后端用 SQLite 存数据，服务端跑在 Linux + Docker 上。

> 当前是 demo：按你原表的**全部字段**和**公式**实现，但日期闸门（必须前一交易日填写）暂未启用，Excel 导出也暂未实现。

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

## nginx 反向代理（学习中）

基础笔记见 [cache/teaching/docs/nginx-basics.md](cache/teaching/docs/nginx-basics.md)，起始配置见 [deploy/nginx/noplan.conf](deploy/nginx/noplan.conf)。

应用已改为只监听 `127.0.0.1:8080`，公网只开 nginx 的 80 端口。

## TradingAgents 分析集成（开发中）

许可单可以一键触发 TradingAgents 分析，摘要回写详情页。迁移与冒烟步骤见
[deploy/tradingagents/README-m0.md](deploy/tradingagents/README-m0.md)。

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
git pull && ./deploy.sh
```

## 测试

```bash
.venv/bin/python -m pytest        # Windows: .venv\Scripts\python -m pytest
```

预审公式有 15 个单元测试，页面流程有 11 个集成测试。

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

## 已知限制 / 下一步

- 日期闸门未启用：demo 只记录填单日期，不判断「前一交易日」
- 暂无 Excel 导出归档
- 无台账页（实际成交、偏离、复盘）
- 单用户、HTTP 明文、无 CSRF 防护（单机自用够用，公开前要补）
- 证券代码不校验真伪

## 目录

```
app/
  main.py        # 路由、登录、表单解析
  review.py      # 计算 + 预审（照搬 Excel 公式）
  db.py          # SQLite
  templates/     # 登录 / 列表 / 表单 / 详情 / 参数 / 纪律
  static/        # CSS + 实时计算 JS
deploy/nginx/noplan.conf   # 宿主机 nginx 站点配置
cache/           # 本地教学 / 测试 / 临时文件（gitignore，不部署）
Dockerfile
docker-compose.yml
deploy.sh
```