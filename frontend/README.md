# Vue 前端

这是台球厅管理系统的正式前端，由 FastAPI 在 `/` 托管。旧的原生
HTML 前端已归档到 `archive/native-frontend/`。

## 结构

```text
frontend/
├── index.html                 页面结构与 Vue 模板
├── styles.css                 统一视觉样式
├── app.js                     页面状态、接口调用和业务交互
├── js/useTables.js            桌台字典与共享时钟
├── js/api.js                  后端接口适配
├── js/format.js               时间、时长的纯展示格式化
├── js/components/TableCard.js 唯一的桌卡模板
├── vendor/vue.global.prod.js  本地 Vue 3.5.13，无 CDN 依赖
└── tests/                     单元测试、模拟接口和浏览器冒烟测试
```

页面通过 `/assets/` 加载 CSS、JavaScript 和本地 Vue 运行时。

## 设计边界

- 桌台、商品和订单状态由 FastAPI 返回，前端只维护页面展示所需的响应式状态。
- 已用时间由 `当前时间 - 后端开始时间` 派生，不单独保存。
- 前端不计算台费、订单合计或收款金额；金额只展示后端返回值。
- `TableCard` 是所有桌台共用的唯一模板。

## 本地运行

从项目根目录启动：

```powershell
uvicorn backend.api_layer:app --reload
```

打开 <http://127.0.0.1:8000/>。旧地址 `/preview` 会重定向到 `/`。

## 检查

不需要安装前端依赖，使用本机 Node 即可：

```powershell
cd frontend
node --check app.js
node --test tests/frontend.test.mjs
```

如果暂时没有启动 MySQL 和后端，也可以使用项目内的模拟接口检查页面：

```powershell
# 终端一
python frontend/tests/mock_server.py

# 终端二
cd frontend
node tests/browser_smoke.mjs
```

模拟页面地址是 <http://127.0.0.1:8765/>。浏览器检查会验证：

- 后端返回四张桌台时页面自动生成四张卡；
- 开台成功后桌卡自动变化；
- 秒表变化期间不会重复请求接口；
- 商品、结账和收款区域由数据驱动；
- 结账前不显示前端计算的合计，收款页只显示后端总金额。
