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
> AI 员工部分已决定暂不实现，只保留设计文档。

## 功能

| 流程 | 说明 |
|---|---|
| 开台 | 选择空闲桌台，生成待结算台位费并开始计时 |
| 加商品 | 在桌消费中追加商品，写入消费记录并扣减库存 |
| 关台 | 按实际时长和每小时价格计算台费，写入关台时间和金额 |
| 建订单 | 勾选多笔待结算消费，合并生成一张未支付订单 |
| 收款 | 确认收款，将订单从「未支付」改为「已支付」 |

前端提供桌台状态、商品选择、结账明细和收款预览；金额只展示后端返回值，
不在浏览器里重新计算。页面可见时每 4 秒静默同步一次，后台暂停、回前台
立即补拉；刷新与冲突恢复都会同步当前打开的面板。

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

### 1. 开台：行锁 + 事务内二次检查 + 数据库唯一约束

开台不是简单插入一条记录。事务外先读取桌台和待结算消费，进入事务后：

1. 用 `SELECT ... FOR UPDATE` 锁住 `billiards_tables` 对应桌台行；
2. 用事务内的 cursor 重新读取该桌待结算消费；
3. 重新确认没有未关台的台位费；
4. 才写入台位费主表和明细表。

这样后到的请求会等待行锁，并在拿到锁后看到最新状态，避免两个前台同时
开同一张桌。`ongoing_table_fees.active_marker` 是数据库生成列：未关台时为
`1`，关台后为 `NULL`；`UNIQUE(桌台编号, active_marker)` 再从存储层保证
同一桌最多只有一条未关台记录。应用捕获 MySQL `1062` 后返回 `409` 和
“该桌台已处于开台状态”。

如果桌台已经没有未关台的台位费，但还有已关台未结账的台费或商品消费，
开台会先返回 `409 TableHasUnsettledConsumptions`，`data` 中带
`消费明细` 和 `汇总`。前端确认后带 `confirm_unsettled=true` 重试，避免
上一轮消费和新一轮台费混在同一张结账单里。

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

### 5. 业务编号：毫秒时间 + 随机后缀

开台、商品消费和订单编号统一由 `backend/id_utils.py` 生成：

```text
T_<桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>
S_<桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>
<桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>
```

这样做的原因是：

- 同一桌同一秒内发生多次操作时，不再生成相同编号；
- 编号最多仍为 30 个字符，不需要改动现有 id 列和外键；
- 旧编号 `T_桌号_秒级时间戳` / `S_桌号_秒级时间戳` 仍能被解析；
- 业务时间字段继续保存秒级 Unix 时间，前端时间显示和旧数据不受影响。

`订单编号` 只作为不透明唯一标识使用；前端展示需要的桌号和创建时间从
`orders` 表的字段读取，不再从编号字符串里反解析。

### 6. 加商品：Idempotency-Key + 事务防重

`POST /products/consumption` 必须携带 `Idempotency-Key` 请求头。前端在准备
提交时生成 UUID；请求失败重试继续使用同一个 Key，收到成功响应后才清空。

数据库用 `idempotency_records.idempotency_key` 主键作为并发闸门：

1. 事务开始先查防重表，已成功过就直接回放首次结果；
2. 没有记录时先插入 `processing` 占位，主键唯一约束会挡住并发双击；
3. 同一个事务内完成插商品消费、扣库存、写 outbox、保存首次成功结果；
4. 后到事务收到 MySQL `1062 Duplicate entry` 时整体回滚，再查询并返回
   首次成功结果。

`response_json` 保存完整四键结果，金额在入库前转成字符串，因此重试响应与
第一次 HTTP JSON 响应内容一致。

### 7. 核心备份：事务性 outbox + 独立 worker

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

### 8. 测试与数据检查

项目包含三类验证：

1. **底层写函数冒烟测试**：9 个主库写函数 + 2 个备份库写函数在事务中执行，
   逐步校验后统一回滚，并用新连接确认没有残留数据。
2. **完整流程测试**：覆盖 6 个正常检查点、10 个边界场景和 1 个故障注入
   回滚场景，验证「商品消费已插入但扣库存失败」时整笔事务回滚。
3. **数据一致性巡检**：检查主表/明细表行数、创建时间、孤儿记录、台费
   和商品金额。

测试脚本会真实写库，必须在测试库中运行，并遵守脚本里的安全闸门。

### 9. 本地缓存：短 TTL + 写后失效 + 版本号防旧值回填

商品目录和桌台基础配置使用进程内 `TTLCache`，不引入 Redis：

- 只缓存 `products:list` 和 `tables:config`；
- 默认 TTL 为 300 秒，使用 `time.monotonic()` 惰性过期；
- 缓存命中返回深拷贝，避免业务层修改共享内存；
- 每个 key 维护独立版本号，查库前记录版本，回填前再次核对；如果期间有写
  操作提交并递增了版本号，旧查询结果直接丢弃；
- 开台、关台提交后失效桌台配置缓存；新增商品消费、修改商品或库存变动提交后
  失效商品列表缓存；
- 缓存读取和失效异常只记录告警并回退数据库，不影响主业务流程。

桌台实时占用状态、待结算消费、未支付订单和支付状态不进入本地缓存，仍然每次
以 MySQL 为准。本地缓存只适用于单进程部署；如果改为多个 Uvicorn worker，需要
禁用本地缓存或升级为 Redis 等共享缓存。

当前项目还没有新增商品和修改商品的 HTTP 接口；对应的提交后失效入口已封装为
`invalidate_products_cache_after_commit()`，后续新增管理接口时必须在事务提交
成功后调用。

### 10. 热点抢购：内存发牌 + 单线程异步落库

在原有同步接口之外新增一条试验性热点路径，老接口行为和事务防线不变：

```text
POST /flash-sale/*
  └─ 内存锁内 popleft 单件牌
       ├─ 没牌：立即拒绝
       └─ 有牌：生成 ticket_id，任务进 deque，返回 queued
                  ↓
          单线程 daemon worker
            ├─ 调用原有 controller 写库
            ├─ 瞬时数据库错误最多重试 2 次
            ├─ 成功：succeeded + 事务提交后缓存失效
            └─ 失败：failed + 牌子退回池尾
```

实现边界：

- 商品牌按“每次 1 件”拆成独立 token，不做 TTL 自动过期；
- 桌台使用唯一使用权牌子，老路开台提交后摘牌，老路关台提交后补牌；
- 老路成功卖出商品时也会从商品牌池尾部移除对应数量，减少双路偏差；
- `ticket_id` 同时作为异步写库的稳定幂等键，重试不会重复扣库存；
- 同一商品 worker 单线程顺序落库，失败任务不阻塞后续任务；
- 内存池、任务队列和 ticket 状态都只在当前进程内，重启会丢失；ticket 状态
  当前不自动清理，可由管理接口重置；
- 当前是单进程试验链路，不是分布式抢购系统，也不能据此宣称生产级高并发。

管理/测试接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/flash-sale/admin/products/preheat` | 初始化指定商品的单件牌数量 |
| POST | `/flash-sale/admin/tables/preheat` | 初始化指定桌台的使用权牌 |
| GET | `/flash-sale/admin/pools` | 查看内存池和队列状态 |
| POST | `/flash-sale/admin/reset` | 队列空闲时重置内存池 |

这些管理接口目前没有登录和权限控制，只适合本地管理或测试环境。

对比结果见
[2026-11-17 热点抢购 A/B 压测](docs/benchmarks/2026-11-17-flash-sale-ab-benchmark.md)：
入口削峰明显，但单 worker 的后台落库速率仍是独立瓶颈。

## 项目结构

```text
backend/
  api_layer.py            FastAPI 路由
  schemas.py              Pydantic 请求模型
  errors.py               HTTP 状态码映射与统一错误响应
  controller_layer.py     流程编排、事务边界、业务日志
  business_layer.py       纯校验、纯计算、数据组装
  io_layer.py             MySQL 读写、事务管理
  local_cache.py          单进程 TTL 缓存与版本号回填
  flash_sale_queue.py     热点发牌池、任务队列、ticket 状态和 worker
  flash_sale_service.py   热点 worker 与现有 controller 写库接线
  outbox.py               outbox 事件与 worker 核心
  outbox_worker.py        独立 worker 启动入口
  json_utils.py           Decimal 的 JSON 出口转换

database/
  schema.sql              主库建表（10 张业务表 + outbox_events + idempotency_records）
  backup_schema.sql       独立备份库建表（2 张表）
  migrations/             已有库的增量迁移 SQL

frontend/
  index.html              Vue 3 页面结构
  app.js                  页面状态与接口交互
  js/                     API、格式化、桌台状态、桌卡组件
  vendor/                 本地 Vue 运行时
  tests/                  前端单测、模拟接口、浏览器冒烟测试

scripts/
  bench_stock_writes.py    加商品扣库存写接口压测
  bench_flash_sale_ab.py   热点抢购入口、排空与一致性 A/B 压测
  bench_refresh.py         首页刷新接口只读压测
  check_data.py            数据一致性巡检（只读）
  check_outbox.py          outbox 积压/死信查看与重排
  check_backup_gap.py      台位费备份缺口检查/补录
  check_product_backup_gap.py  商品备份缺口检查/补录
  migrate_backups_to_backup_db.py  旧备份迁移到独立备份库

tests/
  test_local_cache.py      缓存命中、TTL、版本号和写后失效测试
  test_flash_sale_queue.py 发牌、并发、重试、退牌与 API 契约测试
  test_flash_sale_mysql.py 热点抢购真实库异步落库与双路同步测试
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
Get-Content database/migrations/2026-11-15_add_idempotency_records.sql -Raw | mysql -u root -p
Get-Content database/migrations/2026-11-15_add_ongoing_table_active_unique.sql -Raw | mysql -u root -p
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
| POST | `/tables/open?table_id=1[&confirm_unsettled=true]` | 开台；有待结算消费时先返回 409，确认后重试 |
| POST | `/tables/close?table_id=1` | 关台 |
| POST | `/orders/unpaid` | 创建未支付订单 |
| POST | `/payments/confirm` | 确认收款 |
| POST | `/products/consumption` | 加商品消费，必须带 `Idempotency-Key` 请求头 |
| POST | `/flash-sale/products/consume` | 抢单件商品牌，异步扣库存 |
| POST | `/flash-sale/tables/open` | 抢空闲桌台牌，异步开台 |
| GET | `/flash-sale/tickets/{ticket_id}` | 查询热点任务最终状态 |

请求体和查询参数由 Pydantic 模型与 FastAPI `Query` 约束校验；校验失败返回 HTTP 422
（JSON 语法错误返回 400），响应仍是四键结构。业务错误按 `error_type`
映射状态码：

| 情况 | HTTP 状态 |
|---|---:|
| 成功 | 200 |
| 请求体不是合法 JSON | 400 |
| 字段缺失、类型错误、数量小于等于 0 | 422 |
| 桌台、商品、订单或消费记录不存在 | 404 |
| 桌已占、有待结算消费、订单已付、库存不足、重复关台 | 409 |
| 数据库异常或其他服务端错误 | 500 |

Pydantic 只检查请求形状和字段边界；桌台是否空闲、库存是否足够、订单
是否未支付等业务规则仍然由 controller / business 层判断。

## 测试与检查

### 不写库的检查

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_api_errors tests.test_request_schemas tests.test_id_utils tests.test_decimal_money tests.test_product_backup tests.test_table_exists_check tests.test_idempotency tests.test_open_table_unique tests.test_open_table_unsettled tests.test_local_cache tests.test_flash_sale_queue

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

### 加商品幂等真实库测试

该脚本会并发提交同一个 `Idempotency-Key`，验证只扣一次库存、只生成一条
消费记录，并在结束后清理测试数据：

```powershell
$env:MYSQL_DATABASE = "billiards_hall_test"
$env:ALLOW_WRITE_TESTS = "1"
python tests/test_idempotency_mysql.py
```

### 开台唯一约束真实库测试

该脚本在事务中验证同一桌第二条未关台记录会被数据库唯一索引拦截，
关台后可以再次开台，最后统一回滚：

```powershell
$env:MYSQL_DATABASE = "billiards_hall_test"
$env:ALLOW_WRITE_TESTS = "1"
python tests/test_open_table_unique_mysql.py
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

### 热点抢购异步落库真实库测试

该脚本会真实写入测试库，验证 5 张商品牌、1 张桌台牌的高并发发牌，
异步落库、库存精准扣减、退牌补偿以及老路关台后的桌台补牌：

```powershell
$env:MYSQL_DATABASE = "billiards_hall_test"
$env:MYSQL_BACKUP_DATABASE = "billiards_backup_test"
$env:ALLOW_WRITE_TESTS = "1"
python tests/test_flash_sale_mysql.py
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

这个项目已经能完整跑通单店业务闭环，后端工程化也已完成一部分：

- 已使用独立应用账号 `billiards_app`，不再用 root 跑业务；
- 已有主库/备份库连接池，并记录了 `/frontend/refresh` 的改前/改后压测；
- 已有 Pydantic 请求模型和 HTTP 错误状态码映射；
- 已有毫秒时间 + 随机后缀的业务编号；
- 已有商品目录和桌台配置的单进程 TTL 缓存、写后失效和版本号保护；
- 已有进程内热点发牌、ticket 查询、单线程异步落库和失败退牌试验链路；
- 已有 outbox 积压、死信和备份缺口检查脚本。

以下能力仍未实现，不应在简历或对外介绍中写成已完成：

- 登录、角色权限、租户隔离；
- 真实支付回调（当前「确认收款」只记录状态，不执行扣款）；
- 生产级监控、告警和部署验证；
- `/health`、请求 ID 和慢查询日志；
- 多实例和生产环境写路径验证（已有加商品扣库存接口的本机 1/10/25/50 并发
  基线，但尚不能替代真实部署压测）；
- 备份恢复演练；
- AI 员工的运行代码、工具注册表、规则引擎和评测集（暂不推进）；
- Redis、LangGraph、RAG 等 AI 应用组件（暂不纳入路线）。

当前是单店、小数据量场景，库存、桌台和支付状态必须保持 MySQL 强一致。
项目没有为了简历关键词把库存放进 Redis，也没有把业务日志塞进 outbox。
本地缓存只保存允许短暂旧值的配置型读数据，库存扣减、桌台状态和支付状态仍以
MySQL 事务为唯一事实源。

## 设计文档

- [后端剩余工作与下一步](docs/后端剩余工作与下一步.md)
- [Outbox 异步备份设计](docs/OUTBOX_ASYNC_BACKUP.md)
- [业务写路径与异步边界](docs/设计思路总结.md)
- [AI 员工目标架构与落地路线](docs/AI_EMPLOYEE.md)（历史设计，暂不实现）
- [阶段性工程复盘](docs/项目架构复盘与改进清单.md)（部分结论已被后续提交更新）
