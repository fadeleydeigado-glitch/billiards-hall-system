# 🎱 台球厅管理系统

> 个人独立开发的全栈小项目：从 JSON 文件存储完整迁移到 MySQL，围绕一张球桌的完整生命周期，实现「开台 → 关台 → 加商品 → 创建订单 → 收款」五大业务闭环，并为后续接入 AI 智能助手（Function Calling）做好准备。

## 1. 项目简介

个人独立设计与开发。系统覆盖台球厅日常经营中最核心的收银业务：

- **开台**：选择空闲桌台开台，生成待结算台位费；
- **关台**：按「每小时价格 × 实际时长」计算台费并完成关台结算；
- **加商品**：在桌消费中追加商品，同时扣减库存；
- **建订单**：勾选多笔待结算消费，合并生成一张未支付订单；
- **收款**：确认收款，订单转为「已支付」，流程闭环。

前端为单页界面（开台面板 / 商品区 / 未支付订单 / 收款确认），后端由 FastAPI 提供 HTTP 接口。

## 2. 🛠 技术栈

- **语言**：Python 3
- **Web 框架**：FastAPI + Uvicorn
- **数据库**：MySQL 8.x（utf8mb4，中文列名）
- **数据库驱动**：PyMySQL（主程序与测试）；mysql-connector-python（迁移 / 查看脚本，`check_data.py` 会自动探测二选一）
- **前端**：原生 HTML / CSS / JavaScript 单页应用
- **测试**：纯 Python 断言脚本 + Monkey Patch 回滚测试（不依赖 pytest / unittest）

## 3. 🏗 系统架构

自下而上四层架构，层间只依赖相邻层：

```text
┌──────────────────────────────────────────────────────────────┐
│  前端页面   frontend/index.html（由 FastAPI 托管）            │
└──────────────────────────────────────────────────────────────┘
                             │ HTTP
┌──────────────────────────────────────────────────────────────┐
│  API 层     api_layer.py         FastAPI 路由                │
│  开台 / 关台 / 加商品 / 订单 / 收款 / 前端数据接口             │
└──────────────────────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  控制层     controller_layer.py      three_* 函数            │
│  流程编排 · 事务边界 · 并发防重 · 业务日志（[人] / [AI]）      │
└──────────────────────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  业务层     business_layer.py        two_* 函数              │
│  纯检查与计算：不碰数据库、不写文件，独立于存储实现            │
└──────────────────────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  IO 层      io_layer.py              one_* 函数              │
│  MySQL 读写 · 统一事务管理器 · JSON 文件读写 · 业务日志        │
└──────────────────────────────────────────────────────────────┘
                             ▼
                MySQL 8.x 数据库：billiards_hall
```

- 函数名前缀即所属层级：`one_*` → IO 层，`two_*` → 业务层，`three_*` → 控制层，`api_*` → 接口层；
- 层与层之间用统一的**四键字典**返回结果：`{"status", "error_type", "data", "message"}`。

## 4. 🔥 核心亮点

### 4.1 并发防重与锁机制

- **悲观锁——开台防抢桌**：开台事务内先执行 `SELECT ... FOR UPDATE` 锁住桌台行，再用事务内游标重新读取该桌待结算消费、二次确认空闲后才写入台位费；两人同时开同一桌时，后到者排队等锁，拿到锁后发现已被占用即返回 `TableOccupied`。
- **乐观锁——关台 / 收款防重复**：
  - 关台条件更新：`UPDATE ... WHERE consumption_id = %s AND 关台时间 IS NULL`；
  - 收款条件更新：`UPDATE ... WHERE order_id = %s AND 支付状态 = '未支付'`；
  - 二者都通过 `rowcount` 判断是否命中；未命中时再区分「记录不存在」与「已被操作过」，分别返回 `TableFeeNotFound / TableFeeAlreadyClosed`、`OrderNotFound / OrderNotUnpaid`——并发下重复关台、重复收款也只能成功一次。

### 4.2 事务与一致性

- **统一事务管理器** `one_execute_in_transaction(operation_function)`：开连接 → `begin()` → 执行写操作 → 成功 `commit` / 失败 `rollback` → `finally` 关闭连接；五大写流程全部经由它执行。
- **加商品与扣库存同成功、同回滚**：商品消费记录插入与库存扣减在同一事务内完成，任一步失败整体回滚，杜绝「记了消费、没扣库存」的脏数据。

### 4.3 AI Function Calling 准备

- **四键 JSON 规范**：所有流程函数统一返回 `status / error_type / data / message`——成功与失败可被程序精确判定、错误类型机器可读，直接可作为 AI Function Calling 的返回契约。
- **日志区分 `[人]` / `[AI]`**：业务日志逐行记录 `[时间] [来源] [流程名] [参数] [成功/失败]`；来源默认 `[人]`（网页 / 终端操作），AI 调用时显式传 `[AI]`，AI 的每一次操作都可审计。

### 4.4 严谨的测试保障

- **`test_full_flow.py`**：五大写流程完整用户故事测试（6 个检查点）+ **10 个边界场景拦截检查**（要求全部被拦截）+ 事务回滚测试（1 个检查点）；每一步同时断言「返回值」与「数据库真实状态」。
- **Monkey Patch 回滚测试**：临时替换扣库存函数模拟中途失败，验证商品消费与库存变动**同回滚、无残留**；测试结束在 `finally` 中立刻还原并校验函数对象一致——只作用于内存，不修改磁盘。
- **`check_data.py`**：迁移后数据一致性自检（行数对齐 / 时间字段 / 孤儿记录 / 金额验算），只读不改。
- **`test_mysql_read.py` / `test_mysql_write.py`**：IO 层 MySQL 读写函数专项测试。

## 5. 📁 项目目录结构说明

```text
项目根目录
├── api_layer.py               # API 层：FastAPI 路由（网页与外部唯一入口）
├── controller_layer.py        # 控制层：五大流程编排、事务、并发防重、业务日志
├── business_layer.py          # 业务层：纯检查 / 计算函数（不碰数据库）
├── io_layer.py                # IO 层：MySQL 读写、事务管理器、JSON、日志
├── frontend/
│   └── index.html             # 单页前端：开台 / 商品 / 订单 / 收款
├── test_full_flow.py          # 五大流程 + 边界 + 回滚综合测试
├── test_mysql_read.py         # MySQL 读函数专项测试
├── test_mysql_write.py        # MySQL 写函数专项测试
├── check_data.py              # 数据一致性自检（只读）
├── migrate_json_to_mysql.py   # JSON → MySQL 迁移脚本（自动建表）
├── migration_ddl.sql          # 数据库结构 DDL（11 张表）
├── view_orders.py             # 控制台查看：订单
├── view_ongoing.py            # 控制台查看：待结算消费
├── view_backup.py             # 控制台查看：核心备份
├── view_config.py             # 控制台查看：桌台 / 商品配置
├── 查看*.bat                  # 双击运行对应控制台查看脚本（Windows）
├── setup_mysql_service.ps1    # Windows：MySQL 服务安装脚本（可选）
├── .env.example               # 环境变量模板（参考它配置 MYSQL_PASSWORD）
├── .gitignore                 # 忽略缓存 / 日志 / .env 等，防止误传
└── *.json                     # 示例 / 基线数据（database.json 等）
```

> 运行后会自动生成 `business.log`（业务日志）与 `__pycache__/`（字节码缓存），二者均已被 `.gitignore` 忽略，不会提交到仓库。

## 6. 🚀 本地运行指南

### 6.1 环境要求

- Python 3.8+（开发环境 3.12+）
- MySQL 8.x（字符集建议 utf8mb4）

### 6.2 安装依赖

```bash
pip install fastapi uvicorn pymysql
# 可选：运行数据迁移 / 控制台查看脚本时另需
pip install mysql-connector-python
```

### 6.3 配置数据库密码（必填，切勿硬编码）

代码从环境变量读取数据库密码，不在代码中保存任何明文密码：

```powershell
# 方式一：写入用户环境变量（重开终端后永久生效）
setx MYSQL_PASSWORD "你的数据库密码"

# 方式二：仅当前终端会话生效
$env:MYSQL_PASSWORD = "你的数据库密码"
```

macOS / Linux 对应写法：`export MYSQL_PASSWORD="你的数据库密码"`。

其他可选环境变量（均有默认值，一般无需设置）：`MYSQL_HOST`（默认 `localhost`）、`MYSQL_USER`（默认 `root`）、`MYSQL_DATABASE`（默认 `billiards_hall`）。也可以参考 `.env.example` 建立本地 `.env` 保存配置（该文件已被 `.gitignore` 忽略，不会提交；注意 `.env` 需加载为环境变量后代码才能读取，可自行搭配 python-dotenv）。

### 6.4 初始化数据库（二选一）

1. 执行 `migration_ddl.sql`，创建全部 11 张表；
2. 或运行一键脚本（自动建表 + 导入仓库自带的示例 JSON 数据）：

```bash
python migrate_json_to_mysql.py
```

> ⚠️ 注意：该脚本会先删除旧表再重建，请勿在已有重要数据的库上直接运行。

### 6.5 启动服务

```bash
uvicorn api_layer:app --reload
```

浏览器打开 <http://127.0.0.1:8000/> 即可使用。

### 6.6 可选：运行测试与自检

```bash
python test_full_flow.py   # 五大流程 + 边界 + 回滚（注意：会真实读写数据库）
python check_data.py       # 数据一致性自检（只读，不修改任何数据）
```
