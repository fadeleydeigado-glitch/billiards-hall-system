# 首页刷新接口性能基线（未使用连接池）

> 记录日期：2026-11-10  
> 用途：作为引入 DBUtils 连接池前的对照基线；后续用同一脚本、同一参数复测。

## 1. 测试对象与代码状态

- 接口：`GET http://127.0.0.1:8000/frontend/refresh`
- 代码基线：`baff1cd`
- 连接方式：`backend/io_layer.py` 仍直接调用 `pymysql.connect(**DB_CONFIG)`，每次请求新建连接，用完关闭
- 本次未修改 `backend/io_layer.py`，未安装 DBUtils
- 压测脚本：`scripts/bench_refresh.py`

工作区中已有的 `frontend/styles.css`、`.gitignore` 改动与本轮后端基线无关，未做处理。

## 2. 运行环境

| 项目 | 值 |
|---|---|
| 操作系统 | Windows 11（10.0.26200） |
| Python | 3.12.14（Codex runtime） |
| Web 框架 | FastAPI 0.162.2 |
| ASGI 服务器 | Uvicorn 0.54.0 |
| MySQL 驱动 | PyMySQL 2.2.8 |
| MySQL | 8.4.9 Community Server |
| MySQL `max_connections` | 151 |
| 后端进程 | PID 16252，启动于 2026-11-10 16:33:05 |
| 启动方式 | `uvicorn`，监听 `127.0.0.1:8000`，未开启 `--reload` |
| 部署形态 | 应用、MySQL、压测客户端都在同一台机器 |

## 3. 测试方法

正式命令：

```text
C:\Users\16913\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\bench_refresh.py --label baseline-no-pool --workers 10 --requests 200 --warmup 10
```

- 10 个并发线程，模拟 10 个用户同时刷新首页
- 正式请求 200 次，正式测试前预热 10 次
- 单请求超时 30 秒
- 成功判定：HTTP 200，且响应 JSON 的 `status` 为 `success`
- p50/p95 口径：nearest-rank；将成功请求耗时升序排列后取对应位置
- 请求只读，不修改业务数据

补充事实：接口响应体约 526 字节，数据量很小。因此本次测到的时间主要不是网络传输或大数据量查询，而是请求处理与数据库连接建立/销毁的开销。

## 4. 正式测试结果

### 第 1 轮

```text
label=baseline-no-pool
url=http://127.0.0.1:8000/frontend/refresh
workers=10 requests=200 warmup=10
p50=431.74ms p95=574.67ms
avg=431.27ms min=200.92ms max=633.37ms
success=200/200 failed=0
wall=8.70s throughput=23.00 req/s
```

### 第 2 轮

```text
label=baseline-no-pool-run2
url=http://127.0.0.1:8000/frontend/refresh
workers=10 requests=200 warmup=10
p50=432.66ms p95=529.23ms
avg=473.33ms min=163.45ms max=10161.59ms
success=200/200 failed=0
wall=16.26s throughput=12.30 req/s
```

第 2 轮出现 1 个约 10.16 秒的长尾样本，p50/p95 仍与第 1 轮基本一致。长尾可能来自瞬时连接建立或系统调度抖动；加入连接池后应在相同条件下复测，观察是否消失。

### 单线程对照

```text
workers=1 requests=20 warmup=3
p50=90.63ms p95=120.72ms
avg=93.70ms min=67.13ms max=155.07ms
success=20/20 failed=0
```

### 直接测底层 IO

在独立进程里连续调用 10 次，得到大致范围：

| 操作 | 单次耗时 |
|---|---|
| `one_load_all_ongoing_from_mysql()` | 约 44–51ms |
| `one_load_products_from_mysql()` | 约 28–50ms |
| `one_get_mysql_connection()` + `close()` | 约 20–49ms |

`/frontend/refresh` 每次会读取一次待结算消费、一次商品数据，也就是至少建立两次主库连接。连接建立和销毁占了主要耗时，这正是连接池要解决的问题。

### 连接开销拆分

为了确认时间到底花在“连接”还是“查询”，又做了一组更细的拆分：每次新建连接，执行一条 `SELECT 1`，再关闭。

| 操作 | 单次耗时 |
|---|---|
| 建立连接 | 25.7–55.5ms，平均约 43ms |
| 执行 `SELECT 1` | 0.3–0.8ms |
| 关闭连接 | 0.1–1.1ms |

对比两种做法：

- 10 次独立连接 + 10 次查询：总计约 431ms
- 复用 1 条连接做 10 次查询：总计约 29ms

结论：耗时几乎全在建立连接，查询本身不到 1ms。首页刷新每次要建两条连接，所以单线程约 90ms；10 并发时连接建立相互竞争，p50 被推到 430ms 左右。

## 5. 结论与使用边界

1. 未加连接池时，本机 10 并发、200 次请求的基线为：**p50 约 432ms，p95 约 529–575ms**。
2. 单线程下接口本身已有约 90ms 延迟；并发后连接建立开销叠加，p50 上升到 430ms 左右。
3. 该数据只代表本机 localhost、10 并发、合成请求的结果，不能据此宣称“高并发”或“QPS 提升 X 倍”。
4. 引入连接池后，应保持相同机器、相同 MySQL、相同 Uvicorn 启动参数、相同脚本参数复测；对比时同时记录 p50、p95、成功率和长尾最大值。
