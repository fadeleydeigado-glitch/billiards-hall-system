# 🎱 台球厅管理系统

个人独立开发的全栈小项目。系统已从 JSON 文件存储迁移到 MySQL，并将原生
HTML 前端迁移到 Vue 3，围绕一张球桌实现「开台 → 关台 → 加商品 → 创建订单
→ 收款」完整业务闭环。

## 功能

- **开台**：选择空闲桌台开台，生成待结算台位费。
- **关台**：按「每小时价格 × 实际时长」计算台费并完成关台结算。
- **加商品**：在桌消费中追加商品，同时扣减库存。
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
                             │
                    MySQL: billiards_hall
```

函数名前缀表示所属层级：`one_*` 为 IO 层，`two_*` 为业务层，
`three_*` 为控制层，`api_*` 为接口层。层间统一返回四键结果字典：
`status / error_type / data / message`。

## 项目结构

```text
项目根目录
├── backend/                   # FastAPI 后端包
│   ├── api_layer.py
│   ├── controller_layer.py
│   ├── business_layer.py
│   ├── io_layer.py
│   └── __init__.py
├── frontend/                  # Vue 3 正式前端
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── js/
│   ├── vendor/
│   └── tests/
├── database/
│   └── schema.sql             # 数据库建表语句（11 张表）
├── scripts/
│   ├── check_data.py          # 数据一致性自检（只读）
│   ├── setup_mysql_service.ps1
│   └── views/                 # MySQL 控制台查看工具与 Windows 启动脚本
├── tests/
│   ├── test_full_flow.py      # 完整流程测试，会写入测试数据
│   └── test_mysql_write.py    # 写函数冒烟测试，结束时回滚
├── archive/
│   ├── native-frontend/       # 旧原生 HTML 前端
│   └── legacy-json/           # 旧 JSON 数据、迁移脚本与迁移比对测试
├── docs/
│   └── AI_EMPLOYEE.md         # AI 员工层设计
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

### 3. 初始化数据库

全新数据库可以执行：

```bash
mysql -u root -p billiards_hall < database/schema.sql
```

旧 JSON 数据迁移脚本已归档，只能用于可丢弃的测试库，详见归档说明。

### 4. 启动服务

从项目根目录执行：

```bash
uvicorn backend.api_layer:app --reload
```

打开 <http://127.0.0.1:8000/>。旧地址 `/preview` 会重定向到正式首页。

业务日志写入 `logs/business.log`。启动 Uvicorn 时如需同时保存启动日志，
可以自行重定向到 `logs/uvicorn.log`。

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

数据库写函数冒烟测试会在结束时回滚：

```powershell
python tests/test_mysql_write.py
```

`tests/test_full_flow.py` 会真实写入并保留测试数据，只应在测试库明确允许时运行。
