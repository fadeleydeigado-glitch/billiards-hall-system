# 核心备份的异步化：Outbox + Worker + 独立备份库

> 日期：2026-11-07  
> 范围：台位费与商品消费的核心备份  
> 一句话：业务事务只负责写“要备份”的便签，独立 worker 再拿着便签，用新连接把数据送进备份库。

---

## 1. 边界

### 留在主路径

- 开台、关台、建订单、收款；
- 商品消费写入、库存扣减；
- 桌台状态、订单支付状态。

这些都必须在返回成功前落库，保持强一致。

### 走旁路

- 台位费核心备份 `core_backup`；
- 商品消费核心备份 `core_backup_products`。

旁路允许延迟，但不允许“永远缺着”。

### 保持不变

- 业务日志仍同步写 `logs/business.log`，失败只提示，不阻断；
- 库存、桌台、支付绝不进入 outbox。

---

## 2. 为什么这样拆

如果业务事务提交后再写备份，进程可能在两步之间崩溃：

- 业务已经成功；
- 备份任务却没留下；
- 这个缺口只能靠事后对账发现。

Outbox 把“决定要备份”放进业务事务：

```text
关台事务 / 加商品事务
  ├─ 写业务表
  └─ 写 outbox_events（pending）
        ↓ 同一个事务提交
  业务成功，且备份任务一定存在
```

真正的备份写入放在事务外：

- 备份库慢，不拖主库；
- 备份库挂，业务照常；
- worker 可以重试、补偿、告警。

一句话：**决定进主事务，执行出主事务。**

---

## 3. 两个库

### 主库 `billiards_hall`

- 10 张业务表；
- 1 张 `outbox_events` 待办表。

### 备份库 `billiards_backup`

- `core_backup`：台位费备份；
- `core_backup_products`：商品消费备份。

备份库使用独立连接配置：

```text
MYSQL_BACKUP_HOST
MYSQL_BACKUP_PORT
MYSQL_BACKUP_USER
MYSQL_BACKUP_PASSWORD
MYSQL_BACKUP_DATABASE
```

不填时复用主库连接信息，只切换数据库名。  
worker 写备份时必须调用 `one_get_backup_mysql_connection()` 新建连接，
**不允许复用主库的 connection / cursor**。

---

## 4. 事件状态

```text
pending → processing → done
                │
                └─ 失败 → pending（退避重试）
                          │
                          └─ 超限 → dead
```

- `pending`：待处理，`next_retry_at` 到了才会被领取；
- `processing`：已被某个 worker 领取，带 `locked_at / locked_by`；
- `done`：备份已写入；
- `dead`：超过最大重试次数，等待人工或脚本重排。

默认参数：

- 最大重试 5 次；
- 指数退避，单次最多等 60 秒；
- 单批领取 20 条；
- processing 超过 300 秒未完成，自动回收为 pending；
- 空闲轮询间隔 1 秒。

均可用 `OUTBOX_*` 环境变量覆盖。

---

## 5. 一次关台 / 加商品的时序

```text
API
  → 控制层
    → 业务层校验、组装备份数据
    → 主库事务：
        更新业务表
        INSERT outbox_events(pending)
    → 提交，返回“备份已加入异步队列”

worker
  → 主库：SELECT ... FOR UPDATE SKIP LOCKED
  → 主库：标记 processing
  → 新连接 billiards_backup
  → 幂等写入 core_backup / core_backup_products
  → 主库：标记 done
```

备份写入按 `consumption_id` 幂等：

```sql
INSERT INTO core_backup (...)
VALUES (...)
ON DUPLICATE KEY UPDATE consumption_id = consumption_id;
```

重复处理不会产生重复行。

---

## 6. 故障处理

| 故障 | 行为 |
|---|---|
| 备份库连接失败 | 事件回到 pending，按退避重试；业务不受影响 |
| worker 进程挂掉 | 事件留在主库；重启后继续 |
| worker 处理到一半崩溃 | processing 超时回收为 pending；幂等重放 |
| 同一事件重复处理 | 备份表按主键幂等，不重复 |
| 事件一直失败 | 标记 dead，记录 `last_error` |
| outbox 写入失败 | 主业务事务回滚，避免“业务成功、备份任务丢失” |
| outbox 与备份库仍对不上 | 跨库缺口脚本兜底，从主库业务表补到备份库 |

---

## 7. 运维命令

启动 worker：

```powershell
python -m backend.outbox_worker
```

只处理一轮：

```powershell
python -m backend.outbox_worker --once
```

查看积压 / 死信：

```powershell
python scripts/check_outbox.py
```

重排死信：

```powershell
python scripts/check_outbox.py --retry-dead
python scripts/check_outbox.py --retry-event 123
```

跨库缺口检查与补录：

```powershell
python scripts/check_backup_gap.py
python scripts/check_backup_gap.py --fix
python scripts/check_product_backup_gap.py
python scripts/check_product_backup_gap.py --fix
```

worker 日志写入：

```text
logs/outbox_worker.log
```

---

## 8. 为什么不把日志也放进 outbox

业务日志频率高，而且它本身不是业务事实源。当前保留同步写文件：

- 写失败只打印提示，不阻断业务；
- 不占用 outbox 表；
- 不增加主库写入压力。

核心备份不同：它需要可追溯、可补偿，所以用 outbox。

---

## 9. 以后接 Redis Streams

Outbox 仍然是事实源，Redis Streams 只做传输层：

```text
主库 outbox → 推送到 Redis Stream → 消费者组处理 → ack
```

即使 Redis 丢了，outbox 里的 pending/done 状态仍在，可以重新推送。
顺序是：先把 MySQL outbox 跑稳，再考虑 Redis，不反过来。
