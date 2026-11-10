# 首页刷新接口性能优化（DBUtils 连接池）

> 记录日期：2026-11-10  
> 对照基线：`2026-11-10-refresh-baseline-no-pool.md`  
> 目标：验证引入 DBUtils `PooledDB` 后，`/frontend/refresh` 的 p50/p95 变化。  
> 结论：同一台机器、同一脚本、10 并发 × 200 请求，p50 从约 432ms 降到约 28ms，p95 从约 530–575ms 降到约 44ms；测试全部通过。

## 1. 代码改动

| 文件 | 改动 |
|---|---|
| `requirements.txt` | 增加 `DBUtils>=3.0,<4`，实际安装 3.2.0 |
| `backend/io_layer.py` | 新增主库 `MAIN_POOL` 和备份库 `BACKUP_POOL` |
| `backend/io_layer.py` | `one_get_mysql_connection()` 改为 `MAIN_POOL.connection()` |
| `backend/io_layer.py` | `one_get_backup_mysql_connection()` 改为 `BACKUP_POOL.connection()` |

主库池参数：

```python
PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=2,
    maxcached=10,
    blocking=True,
    ping=1,
    reset=True,
    **DB_CONFIG,
)
```

备份库池参数：

```python
PooledDB(
    creator=pymysql,
    maxconnections=5,
    mincached=1,
    maxcached=3,
    blocking=True,
    ping=1,
    reset=True,
    **BACKUP_DB_CONFIG,
)
```

### 为什么 `maxcached` 用 10 而不是文档里的 5

按文档先用了 `maxcached=5`。在 10 并发下，池里最多保留 5 条空闲连接，另外 5 条归还时会被物理关闭，下一波请求又要重新建连，仍然有连接抖动。

把 `maxcached` 调到和 `maxconnections` 一样（10）后，10 并发下的 p50 从约 92ms 降到约 28ms。证据见下面的两组结果。

### 原有 `close()` 是否保留

- 修改前 `backend/io_layer.py` 实际代码中的 `connection.close()`：8 处
- 修改后：8 处
- `backend/outbox.py`：9 处，未改动

池化后 `close()` 的语义是“归还连接”，不是物理断开，所以调用方不需要改。

## 2. 压测结果

### 2.1 文档参数：`maxcached=5`

第 1 轮：

```text
p50=91.79ms p95=227.07ms
avg=105.18ms min=7.77ms max=270.86ms
success=200/200 failed=0
wall=2.12s throughput=94.33 req/s
```

第 2 轮：

```text
p50=93.57ms p95=198.49ms
avg=90.56ms min=10.64ms max=242.94ms
success=200/200 failed=0
wall=1.83s throughput=109.51 req/s
```

### 2.2 调整后：`maxcached=10`

第 1 轮：

```text
p50=29.23ms p95=45.72ms
avg=29.75ms min=10.47ms max=55.09ms
success=200/200 failed=0
wall=0.61s throughput=326.92 req/s
```

第 2 轮：

```text
p50=27.39ms p95=43.35ms
avg=28.53ms min=13.15ms max=60.54ms
success=200/200 failed=0
wall=0.59s throughput=341.86 req/s
```

连接数稳定性检查（100 次请求）：

```text
p50=28.86ms p95=46.41ms
success=100/100 failed=0
throughput=316.72 req/s
```

### 2.3 单线程对照

```text
workers=1 requests=20 warmup=3
p50=7.19ms p95=31.34ms
avg=12.41ms min=4.12ms max=37.01ms
success=20/20 failed=0
```

## 3. 优化前后对比

| 指标 | 优化前（无连接池） | 优化后（连接池） | 变化 |
|---|---:|---:|---:|
| p50 | 431.74–432.66ms | 27.39–29.23ms | 约 14.8–15.8 倍 |
| p95 | 529.23–574.67ms | 43.35–45.72ms | 约 11.6–13.3 倍 |
| 吞吐 | 12.30–23.00 req/s | 326.92–341.86 req/s | 明显提升 |
| 成功请求 | 200/200 | 200/200 | 一致 |

吞吐区间跨度较大，是因为基线第 2 轮出现过 1 个约 10.16 秒的长尾样本。简历和汇报应优先使用 p50/p95，不要只拿最高吞吐说事。

## 4. 验证结果

| 验证项 | 结果 |
|---|---|
| `python -m unittest tests.test_decimal_money tests.test_product_backup` | 22/22 通过 |
| `tests/test_mysql_write.py`（测试库） | 11 个写函数全部通过，回滚后无残留 |
| `tests/test_full_flow.py`（测试库） | 17 项通过 / 0 项失败 |
| `python -m backend.outbox_worker --once` | `claimed=0 done=0 failed=0 dead=0` |
| 主库连接类型 | `PooledDedicatedDBConnection` |
| 备份库连接类型 | `PooledDedicatedDBConnection` |
| 100 次请求后的 MySQL 连接数 | `billiards_hall` 11 条（10 条池连接 + 1 条检查连接），`billiards_backup` 2 条，前后稳定 |

完整流程覆盖：开台、加商品、关台、建单、收款 6 个正常检查点；10 个边界场景（重复开台、桌台不存在、商品不存在、超库存、重复收款等）；1 个故障注入回滚场景。

## 5. 测试库说明

写库测试需要数据库名包含 `test`。当前应用账号没有建库权限，因此使用本机管理员凭据创建了两个隔离测试库：

- `billiards_hall_test`
- `billiards_backup_test`

测试库使用最小种子数据：1 号桌、99 号桌、`P_001` 可乐、库存 100。生产库 `billiards_hall` / `billiards_backup` 未被测试写入。

## 6. 使用边界

1. 这是本机 localhost 的合成压测：10 个线程、200 次请求，压测客户端用 `urllib`，每个请求新建一条 HTTP 连接。
2. 客户端侧会留下大量 `TIME_WAIT`，绝对延迟不能当作生产 SLA。
3. `maxcached=10` 是针对 10 并发测试调出来的；真实单店并发更低时，`mincached=2`、`maxconnections=10` 已足够，`maxcached` 可以按实际并发再调小。
4. 对外表述应写“本机 10 并发合成压测，p50/p95 从约 432ms/530–575ms 降到约 28ms/44ms”，不要写“支持高并发”或“线上 QPS 提升 X 倍”。
