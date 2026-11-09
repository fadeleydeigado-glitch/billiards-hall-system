# 台球厅店务管理系统

一个面向单店台球厅的前台业务系统，使用 FastAPI + MySQL + Vue 3，覆盖
「开台 → 加商品 → 关台 → 建单 → 收款」完整业务闭环。

项目重点不是堆技术栈，而是把几个容易出错的地方处理清楚：

- 同一张桌被重复开台；
- 同一笔台位费被重复关台；
- 同一张订单被重复收款；
- 并发扣库存导致超卖；
- 金额计算出现浮点误差；
- 核心备份写入失败后无人发现。

> 当前定位：可本地运行、可测试的单店业务系统，不是高并发平台，也不是
> 完整 SaaS。项目目前没有登录、角色权限、租户隔离和真实支付回调；
> AI 员工部分只有设计文档，尚未进入代码实现。

## 功能

| 流程 | 说明 |
|---|---|
| 开台 | 选择空闲桌台，生成待结算台位费并开始计时 |
| 加商品 | 在桌消费中追加商品，写入消费记录并扣减库存 |
| 关台 | 按实际时长和每小时价格计算台费，写入关台时间和金额 |
| 建订单 | 勾选多笔待结算消费，合并生成一张未支付订单 |
| 收款 | 确认收款，将订单从「未支付」改为「已支付」 |

前端提供桌台状态、商品选择、结账明细和收款预览；金额只展示后端返回值，
不在浏览器里重新计算。

## 技术栈

- 后端：Python 3.12、FastAPI、Uvicorn
- 数据库：MySQL 8.x（utf8mb4）
- 数据库驱动：PyMySQL；部分运维/查看脚本使用 mysql-connector-python
- 前端：Vue 3、原生 CSS、浏览器 ES Modules
- 测试：Python `unittest` + Node.js `node:test` + 无头 Chrome 冒烟脚本

## 系统结构

```text
浏览器
  └─ Vue 3 + 原生 ES Modules
       └─ FastAPI 路由（backend/api_layer.py）
            └─ 控制层（backend/controller_layer.py）
                 │  流程编排、事务边界、业务日志
                 └─ 业务层（backend/business_layer.py）
                      │  纯校验、纯计算、数据组装
                      └─ IO 层（backend/io_layer.py）
                           │  MySQL 读写、事务管理
                           ├─ 主库 billiards_hall
                           │    10 张业务表 + outbox_events
                           └─ outbox worker
                                 → 独立备份库 billiards_backup
                                   core_backup / core_backup_products
```

函数名前缀表示所属层级：

| 前缀 | 所在层 | 职责 |
|---|---|---|
| `api_*` | API 层 | 接收 HTTP 参数，转交控制层 |
| `three_*` | 控制层 | 组织业务流程，决定事务边界，写业务日志 |
| `two_*` | 业务层 | 结构检查、业务判断、金额和数据组装 |
| `one_*` | IO 层 | 连接 MySQL、执行 SQL、管理事务、写日志 |

层间统一返回四键结果字典：

```python
{
    "status": "success" | "error",
    "error_type": str | None,
    "data": object | None,
    "message": str,
}
```

业务层不导入 IO 层，不直接读数据库、不获取系统当前时间；当前时间和备份
写入由控制层传入或编排。

## 关键设计

### 1. 开台：行锁 + 事务内二次检查

开台不是简单插入一条记录。事务外先读取桌台和待结算消费，进入事务后：

1. 用 `SELECT ... FOR UPDATE` 锁住 `billiards_tables` 对应桌台行；
2. 用事务内的 cursor 重新读取该桌待结算消费；
3. 重新确认没有未关台的台位费；
4. 才写入台位费主表和明细表。

这样后到的请求会等待行锁，并在拿到锁后看到最新状态，避免两个前台同时
开同一张桌。

### 2. 关台、收款、库存：条件更新

关键状态转换不依赖应用层「先查再写」，而是把状态条件放进 SQL：

```sql
-- 关台：只有还没关过才允许关
UPDATE ongoing_table_fees
SET `关台时间` = %s, `金额` = %s
WHERE consumption_id = %s AND `关台时间` IS NULL;

-- 收款：只有未支付才允许收款
UPDATE orders
SET `支付状态` = '已支付', `支付时间` = %s
WHERE order_id = %s AND `支付状态` = '未支付';

-- 扣库存：只有库存足够才允许扣
UPDATE products
SET `库存` = `库存` - %s
WHERE id = %s AND `库存` >= %s;
```

更新命中 0 行时会再查一次，区分「记录不存在」和「状态已变化」，返回不同的
错误类型。

### 3. 订单：同一事务内完成

创建订单时，以下操作在同一个事务里完成：

- 写入 `orders` 主表；
- 按消费类型写入订单台费/商品明细；
- 从待结算表中删除已进入订单的消费。

任一步失败，整个事务回滚，不留下「订单建了但消费没清」或「消费清了但订单
没建」的中间状态。

### 4. 金额：全链路 Decimal

- 数据库金额列使用 `DECIMAL(10,2)`；
- 后端读取后保持 `Decimal`，不经过 `float`；
- 台费按「时长秒数 × 每小时价格 ÷ 3600」精确计算，再向下取整到整元；
- 商品小计和订单合计保留两位小数；
- HTTP JSON 出口把金额转成字符串，避免标准库 JSON 无法序列化 `Decimal`；
- 前端只做展示，不重新计算台费或订单金额。

### 5. 核心备份：事务性 outbox + 独立 worker

核心备份不直接在业务事务里写第二个数据库，而是使用 outbox：

```text
关台 / 加商品事务
  ├─ 写业务表
  └─ 插入 outbox_events（pending）
        ↓ 同一事务提交
  业务成功，备份任务一定存在
        ↓
outbox_worker
  ├─ 从主库领取 pending 事件
  ├─ 使用独立连接写 billiards_backup
  ├─ 成功：done
  ├─ 失败：退避重试
  └─ 超限：dead，等待人工重排
```

实现细节：

- `FOR UPDATE SKIP LOCKED` 领取事件，支持多个 worker 并行；
- 状态：`pending / processing / done / dead`；
- 指数退避重试，默认最多 5 次；
- `processing` 超过 300 秒自动回收为 `pending`；
- 备份写入按 `consumption_id` 幂等，重复处理不会产生重复行；
- 提供 outbox 积压/死信查看、死信重排、备份缺口检查和 `--fix` 补录脚本。

outbox 只用于核心备份，不用于业务日志。业务日志仍然同步写入
`logs/business.log`，写失败只提示，不阻断主流程。

### 6. 测试与数据检查

项目包含三类验证：

1. **底层写函数冒烟测试**：9 个主库写函数 + 2 个备份库写函数在事务中执行，
   逐步校验后统一回滚，并用新连接确认没有残留数据。
2. **完整流程测试**：覆盖 6 个正常检查点、10 个边界场景和 1 个故障注入
   回滚场景，验证「商品消费已插入但扣库存失败」时整笔事务回滚。
3. **数据一致性巡检**：检查主表/明细表行数、创建时间、孤儿记录、台费
   和商品金额。

测试脚本会真实写库，必须在测试库中运行，并遵守脚本里的安全闸门。

## 项目结构

```text
backend/
  api_layer.py            FastAPI 路由
  controller_layer.py     流程编排、事务边界、业务日志
  business_layer.py       纯校验、纯计算、数据组装
  io_layer.py             MySQL 读写、事务管理
  outbox.py               outbox 事件与 worker 核心
  outbox_worker.py        独立 worker 启动入口
  json_utils.py           Decimal 的 JSON 出口转换

database/
  schema.sql              主库建表（10 张业务表 + outbox_events）
  backup_schema.sql       独立备份库建表（2 张表）
  migrations/             已有库的增量迁移 SQL

frontend/
  index.html              Vue 3 页面结构
  app.js                  页面状态与接口交互
  js/                     API、格式化、桌台状态、桌卡组件
  vendor/                 本地 Vue 运行时
  tests/                  前端单测、模拟接口、浏览器冒烟测试

scripts/
  check_data.py            数据一致性巡检（只读）
  check_outbox.py          outbox 积压/死信查看与重排
  check_backup_gap.py      台位费备份缺口检查/补录
  check_product_backup_gap.py  商品备份缺口检查/补录
  migrate_backups_to_backup_db.py  旧备份迁移到独立备份库

tests/
  test_decimal_money.py    金额计算和 JSON 序列化测试
  test_product_backup.py   商品备份数据组装测试
  test_mysql_write.py      底层写函数事务回滚冒烟
  test_full_flow.py        完整流程、边界和故障注入测试

docs/
  OUTBOX_ASYNC_BACKUP.md   outbox 异步备份设计
  AI_EMPLOYEE.md           AI 员工目标架构与落地路线（设计文档，未实现）
  设计思路总结.md           主路径/旁路与异步边界
  项目架构复盘与改进清单.md  阶段性复盘（部分结论已被后续提交更新）
```

## 本地运行

### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

### 2. 配置环境变量

复制模板并填入本机 MySQL 密码：

```powershell
Copy-Item .env.example .env
```

主要变量：

```text
MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE

MYSQL_BACKUP_HOST
MYSQL_BACKUP_PORT
MYSQL_BACKUP_USER
MYSQL_BACKUP_PASSWORD
MYSQL_BACKUP_DATABASE
```

`.env` 已被 `.gitignore` 忽略，不要提交真实密码。

### 3. 初始化数据库

全新环境执行主库和备份库建库脚本：

```powershell
Get-Content database/schema.sql -Raw | mysql -u root -p
Get-Content database/backup_schema.sql -Raw | mysql -u root -p
```

已有主库升级时执行增量迁移：

```powershell
Get-Content database/migrations/2026-11-07_add_outbox_events.sql -Raw | mysql -u root -p
Get-Content database/migrations/2026-11-07_create_billiards_backup.sql -Raw | mysql -u root -p
python scripts/migrate_backups_to_backup_db.py
python scripts/migrate_backups_to_backup_db.py --apply
```

迁移脚本默认只读检查；只有加 `--apply` 才会把旧备份复制到独立备份库。

### 4. 启动 API

```powershell
uvicorn backend.api_layer:app --reload --reload-dir backend
```

打开：<http://127.0.0.1:8000/>

旧地址 `/preview` 会重定向到正式首页。

### 5. 启动 outbox worker

另开一个终端：

```powershell
python -m backend.outbox_worker
```

只处理一轮：

```powershell
python -m backend.outbox_worker --once
```

业务日志写入 `logs/business.log`，worker 日志写入
`logs/outbox_worker.log`。

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/frontend/refresh` | 桌台与商品刷新数据 |
| GET | `/frontend/products` | 商品列表 |
| GET | `/frontend/unpaid-orders` | 未支付订单 |
| GET | `/tables/checkout-options?table_id=1` | 单桌结账明细 |
| POST | `/tables/open?table_id=1` | 开台 |
| POST | `/tables/close?table_id=1` | 关台 |
| POST | `/orders/unpaid` | 创建未支付订单 |
| POST | `/payments/confirm` | 确认收款 |
| POST | `/products/consumption` | 加商品消费 |

当前 API 业务错误通常仍返回 HTTP 200，错误信息放在四键结果字典中；这是
当前前端可用的内部约定，不是完整的对外 REST 错误契约。

## 测试与检查

### 不写库的检查

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_decimal_money tests.test_product_backup

cd frontend
node --check app.js
node --test tests/frontend.test.mjs
```

### 事务回滚冒烟

`tests/test_mysql_write.py` 会写入测试数据并在结束时统一回滚。运行前确认
连接的是测试库：

```powershell
python tests/test_mysql_write.py
```

### 完整流程测试

`tests/test_full_flow.py` 会真实写库并保留测试数据，必须先显式授权，且
主库/备份库名称都必须包含 `test`：

```powershell
$env:MYSQL_DATABASE = "billiards_hall_test"
$env:MYSQL_BACKUP_DATABASE = "billiards_backup_test"
$env:ALLOW_WRITE_TESTS = "1"
python tests/test_full_flow.py
```

### 数据一致性巡检

```powershell
python scripts/check_data.py
```

该脚本只读，不会修改数据库。

## outbox 运维

查看积压和死信：

```powershell
python scripts/check_outbox.py
```

重排全部死信或指定事件：

```powershell
python scripts/check_outbox.py --retry-dead
python scripts/check_outbox.py --retry-event 123
```

检查台位费/商品备份缺口：

```powershell
python scripts/check_backup_gap.py
python scripts/check_product_backup_gap.py
```

确认缺失清单后再补录：

```powershell
python scripts/check_backup_gap.py --fix
python scripts/check_product_backup_gap.py --fix
```

补录只写备份库，不碰业务表；按 `consumption_id` 去重，可重复执行。

> worker 尚未处理的 `pending` 事件，可能暂时出现在缺口检查中。先看
> `check_outbox.py` 是否有积压，再判断是否真的缺失。

## 当前边界

这个项目已经能完整跑通单店业务闭环，但以下能力尚未实现，不应在简历或
对外介绍中写成已完成：

- 登录、角色权限、租户隔离；
- 真实支付回调（当前「确认收款」只记录状态，不执行扣款）；
- 生产级监控、告警和部署验证；
- 数据库连接池；
- 正式的 Pydantic 请求模型和 HTTP 错误状态码契约；
- 真实并发压测和多实例验证；
- AI 员工的运行代码、工具注册表、规则引擎和评测集；
- Redis、LangGraph、RAG 等 AI 应用组件。

当前是单店、小数据量场景，库存、桌台和支付状态必须保持 MySQL 强一致。
项目没有为了简历关键词把库存放进 Redis，也没有把业务日志塞进 outbox。

## 设计文档

- [Outbox 异步备份设计](docs/OUTBOX_ASYNC_BACKUP.md)
- [业务写路径与异步边界](docs/设计思路总结.md)
- [AI 员工目标架构与落地路线](docs/AI_EMPLOYEE.md)（设计，未实现）
- [阶段性工程复盘](docs/项目架构复盘与改进清单.md)（部分结论已被后续提交更新）
