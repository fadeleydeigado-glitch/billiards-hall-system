# 🎱 台球厅管理系统

个人独立开发的全栈小项目。系统已从 JSON 文件存储迁移到 MySQL，并将原生
HTML 前端迁移到 Vue 3，围绕一张球桌实现「开台 → 关台 → 加商品 → 创建订单
→ 收款」完整业务闭环。

## 功能

- **开台**：选择空闲桌台开台，生成待结算台位费。
- **关台**：按「每小时价格 × 实际时长」计算台费并完成关台结算。
- **加商品**：在桌消费中追加商品、扣减库存，并写入商品消费核心备份。
- **建订单**：勾选多笔待结算消费，合并生成一张未支付订单。
- **收款**：确认收款，订单转为「已支付」，流程闭环。

## 技术栈

- **后端**：Python、FastAPI、Uvicorn
- **数据库**：MySQL 8.x（utf8mb4）
- **数据库驱动**：PyMySQL；迁移和历史查看脚本另用 mysql-connector-python
- **前端**：Vue 3、原生 CSS、浏览器 ES Modules
- **测试**：Node.js 前端测试 + Python 集成测试脚本

## 系统架构

```text
┌──────────────────────────────────────────────────────────────┐
│  前端      frontend/index.html（Vue 3，由 FastAPI 托管）      │
└──────────────────────────────────────────────────────────────┘
                             │ HTTP
┌──────────────────────────────────────────────────────────────┐
│  API 层    backend/api_layer.py       FastAPI 路由           │
├──────────────────────────────────────────────────────────────┤
│  控制层    backend/controller_layer.py  流程编排与事务边界    │
├──────────────────────────────────────────────────────────────┤
│  业务层    backend/business_layer.py    纯检查与计算          │
├──────────────────────────────────────────────────────────────┤
│  IO 层     backend/io_layer.py          MySQL、事务与日志     │
└──────────────────────────────────────────────────────────────┘
          │ 业务事务                         │ outbox 待办事件
          ▼                                  ▼
  MySQL: billiards_hall              outbox_events
  （10 张业务表 + outbox_events）            │
                                             │ worker 领取
                                             ▼
                              backend/outbox_worker.py（独立进程）
                                  新建连接，不复用主库连接
                                             │
                                             ▼
                              MySQL: billiards_backup
                              core_backup / core_backup_products
```

函数名前缀表示所属层级：`one_*` 为 IO 层，`two_*` 为业务层，
`three_*` 为控制层，`api_*` 为接口层。层间统一返回四键结果字典：
`status / error_type / data / message`。

### 金额精度约定

- 后端金额从 MySQL 的 `DECIMAL` 读入后始终保持 `Decimal`，不经过 `float`。
- 台费按「时长秒数 × 每小时价格 ÷ 3600」精确计算，再向下取整到整元。
- HTTP JSON 中的金额字段以字符串返回（例如 `"30.00"`），避免标准库
  `json.dumps` 无法序列化 `Decimal`；前端读取后用 `Number()` 转成数值展示。

### 核心备份与异步 outbox

- 主库 `billiards_hall` 只保留业务事实源和 `outbox_events` 待办表。
- 关台 / 加商品时，业务事务里顺手写一条 outbox 事件；业务提交，事件一定在。
- `python -m backend.outbox_worker` 是独立后台进程：
  - 从主库领取 `pending` 事件；
  - **新建到 `billiards_backup` 的连接**，不复用主库连接；
  - 按 `consumption_id` 幂等写入备份表；
  - 成功标 `done`，失败按退避重试，超限标 `dead`。
- 备份库 `billiards_backup`：
  - `core_backup`：台位费核心备份；
  - `core_backup_products`：商品消费核心备份。
- 业务日志仍然同步写 `logs/business.log`，不走 outbox。
- 运维命令：

```powershell
python scripts/check_outbox.py
python scripts/check_outbox.py --retry-dead
python scripts/check_outbox.py --retry-event 123
python scripts/check_backup_gap.py
python scripts/check_backup_gap.py --fix
python scripts/check_product_backup_gap.py
python scripts/check_product_backup_gap.py --fix
```

> 注意：worker 尚未处理的 `pending` 事件，可能暂时出现在缺口检查里。
> 先看 `check_outbox.py` 是否有积压，再判断是否真的缺失。

## 项目结构

```text
项目根目录
├── backend/                   # FastAPI 后端包
│   ├── api_layer.py
│   ├── controller_layer.py
│   ├── business_layer.py
│   ├── io_layer.py
│   ├── outbox.py              # outbox 事件与 worker 核心
│   ├── outbox_worker.py       # 独立异步 worker 启动入口
│   ├── json_utils.py          # 金额 Decimal 的 JSON 出口转换
│   └── __init__.py
├── frontend/                  # Vue 3 正式前端
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── js/
│   ├── vendor/
│   └── tests/
├── database/
│   ├── schema.sql             # 主库建表语句（10 张业务表 + outbox）
│   ├── backup_schema.sql      # 独立备份库建表语句（2 张表）
│   └── migrations/            # 现有库增量迁移 SQL
├── scripts/
│   ├── check_data.py          # 数据一致性自检（只读）
│   ├── check_outbox.py        # outbox 积压/死信查看与重排
│   ├── check_backup_gap.py    # 台位费备份缺口检查/补录
│   ├── check_product_backup_gap.py  # 商品备份缺口检查/补录
│   ├── migrate_backups_to_backup_db.py  # 旧备份迁移到独立备份库
│   ├── setup_mysql_service.ps1
│   └── views/                 # MySQL 控制台查看工具与 Windows 启动脚本
├── tests/
│   ├── test_full_flow.py      # 完整流程测试，会写入测试数据
│   ├── test_mysql_write.py    # 写函数冒烟测试，结束时回滚
│   └── test_product_backup.py # 商品备份数据组装测试，不连数据库
├── archive/
│   ├── native-frontend/       # 旧原生 HTML 前端
│   └── legacy-json/           # 旧 JSON 数据、迁移脚本与迁移比对测试
├── docs/
│   ├── AI_EMPLOYEE.md         # AI 员工层设计
│   └── OUTBOX_ASYNC_BACKUP.md # Outbox + 异步备份设计
├── logs/                      # 运行时日志（Git 忽略）
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

旧 JSON 运行时代的完整后端代码保留在 Git 标签
`pre-cleanup-2026-11-05` 中，具体说明见
[`archive/legacy-json/README.md`](archive/legacy-json/README.md)。

## 本地运行

### 1. 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 2. 配置数据库密码

后端和运维脚本都从项目根目录的 `.env` 或系统环境变量读取数据库密码：

```powershell
Copy-Item .env.example .env
# 然后编辑 .env，填入 MYSQL_PASSWORD
```

也可以只在当前终端设置：

```powershell
$env:MYSQL_PASSWORD = "你的数据库密码"
```

可选变量：`MYSQL_HOST`（默认 `localhost`）、`MYSQL_USER`（默认 `root`）、
`MYSQL_DATABASE`（默认 `billiards_hall`）、`MYSQL_PORT`（默认 `3306`）。
备份库变量：`MYSQL_BACKUP_HOST`、`MYSQL_BACKUP_PORT`、`MYSQL_BACKUP_USER`、
`MYSQL_BACKUP_PASSWORD`、`MYSQL_BACKUP_DATABASE`（默认 `billiards_backup`）。
不填备份库账号时自动复用主库账号；以后可以把备份库单独换到另一台主机或磁盘。

### 3. 初始化数据库

全新环境执行主库和备份库两份建库脚本：

```bash
mysql -u root -p < database/schema.sql
mysql -u root -p < database/backup_schema.sql
```

已有主库升级时，执行增量迁移并复制旧备份：

```bash
mysql -u root -p < database/migrations/2026-11-07_add_outbox_events.sql
mysql -u root -p < database/migrations/2026-11-07_create_billiards_backup.sql
python scripts/migrate_backups_to_backup_db.py          # 只读检查
python scripts/migrate_backups_to_backup_db.py --apply  # 复制旧备份
```

> 升级说明：主库旧的 `core_backup` / `core_backup_products` 表会保留，
> 不再被业务写入，作为迁移期回退依据；确认新链路稳定后再决定是否清理。

旧 JSON 数据迁移脚本已归档，只能用于可丢弃的测试库，详见归档说明。

### 4. 启动服务

先启动 API：

```bash
uvicorn backend.api_layer:app --reload
```

再开一个终端启动异步备份 worker：

```bash
python -m backend.outbox_worker
```

只处理一轮可用事件（测试或手动补跑）：

```bash
python -m backend.outbox_worker --once
```

打开 <http://127.0.0.1:8000/>。旧地址 `/preview` 会重定向到正式首页。

业务日志写入 `logs/business.log`；worker 日志写入 `logs/outbox_worker.log`。
启动 Uvicorn 时如需同时保存启动日志，可以自行重定向到 `logs/uvicorn.log`。

## 测试与检查

前端静态检查和单元测试：

```powershell
cd frontend
node --check app.js
node --test tests/frontend.test.mjs
```

Python 编译检查：

```powershell
python -m compileall backend tests scripts archive/legacy-json
```

金额计算回归测试（不连接数据库）：

```powershell
python -m unittest tests.test_decimal_money
```

商品备份数据组装测试（不连接数据库）：

```powershell
python -m unittest tests.test_product_backup
```

数据库写函数冒烟测试会同时检查主库和备份库，结束时两个库都回滚：

```powershell
python tests/test_mysql_write.py
```

`tests/test_full_flow.py` 会真实写入并保留测试数据，只应在测试库明确允许时运行。
运行前要把主库和备份库都切到测试库：

```powershell
$env:MYSQL_DATABASE = "billiards_hall_test"
$env:MYSQL_BACKUP_DATABASE = "billiards_backup_test"
$env:ALLOW_WRITE_TESTS = "1"
python tests/test_full_flow.py
```

它会跑完整五流程，并在关台/加商品后执行一次 outbox worker，检查备份是否落到
`billiards_backup_test`。
