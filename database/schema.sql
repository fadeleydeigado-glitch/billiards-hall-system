-- ============================================================
-- 台球厅管理系统 — JSON → MySQL 数据库建表语句
-- MySQL 版本: 8.4
-- 数据库名: billiards_hall
-- 说明: 本文件包含所有 11 张表的 CREATE TABLE 语句
-- ============================================================

CREATE DATABASE IF NOT EXISTS billiards_hall
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE billiards_hall;
-- ============================================================
-- 第一部分：基础配置表（3 张）
-- 来源: database.json
-- ============================================================

-- 1. 桌台表
CREATE TABLE IF NOT EXISTS billiards_tables (
    id          VARCHAR(10)    NOT NULL PRIMARY KEY COMMENT '桌台编号',
    每小时价格  DECIMAL(10,2)  NOT NULL COMMENT '桌台每小时收费（元）'
) COMMENT '桌台配置表';

-- 2. 商品表
CREATE TABLE IF NOT EXISTS products (
    id          VARCHAR(10)    NOT NULL PRIMARY KEY COMMENT '商品编号',
    商品名称    VARCHAR(50)    NOT NULL COMMENT '商品名称',
    单价        DECIMAL(10,2)  NOT NULL COMMENT '当前售价（元）',
    库存        INT            NOT NULL DEFAULT 0 COMMENT '当前剩余库存'
) COMMENT '商品配置表';

-- 3. 消费类型参照表
CREATE TABLE IF NOT EXISTS consumption_types (
    type_code   CHAR(1)        NOT NULL PRIMARY KEY COMMENT '类型代码',
    type_name   VARCHAR(20)    NOT NULL COMMENT '类型名称'
) COMMENT '消费类型参照表';

INSERT INTO consumption_types (type_code, type_name) VALUES
    ('T', '台位费'),
    ('S', '商品');

-- ============================================================
-- 第二部分：待结算消费表（3 张）
-- ============================================================

-- 4. 待结算消费主表
CREATE TABLE IF NOT EXISTS ongoing_consumptions (
    id          VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号    VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    type_code   CHAR(1)        NOT NULL COMMENT '消费类型代码',
    创建时间    BIGINT         NOT NULL COMMENT '消费创建时间戳',
    INDEX idx_ongoing_table (桌台编号),
    INDEX idx_ongoing_type (type_code),
    CONSTRAINT fk_ongoing_type FOREIGN KEY (type_code) REFERENCES consumption_types(type_code)
) COMMENT '待结算消费主表';

-- 5. 待结算台位费明细表
CREATE TABLE IF NOT EXISTS ongoing_table_fees (
    consumption_id  VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号        VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    开台时间        BIGINT         NOT NULL COMMENT '开台时间戳',
    关台时间        BIGINT         NULL     COMMENT '关台时间戳,NULL表示尚未关台',
    每小时价格      DECIMAL(10,2)  NOT NULL COMMENT '每小时价格',
    金额            DECIMAL(10,2)  NULL     COMMENT '台位费金额,NULL表示尚未计算',
    INDEX idx_otf_table (桌台编号),
    CONSTRAINT fk_otf_consumption FOREIGN KEY (consumption_id) REFERENCES ongoing_consumptions(id)
) COMMENT '待结算台位费明细';

-- 6. 待结算商品消费明细表
CREATE TABLE IF NOT EXISTS ongoing_product_consumptions (
    consumption_id  VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号        VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    商品编号        VARCHAR(10)    NOT NULL COMMENT '商品编号',
    商品名称        VARCHAR(50)    NOT NULL COMMENT '商品名称快照',
    单价            DECIMAL(10,2)  NOT NULL COMMENT '销售单价快照',
    数量            INT            NOT NULL COMMENT '购买数量',
    金额            DECIMAL(10,2)  NOT NULL COMMENT '小计金额',
    INDEX idx_opc_table (桌台编号),
    CONSTRAINT fk_opc_consumption FOREIGN KEY (consumption_id) REFERENCES ongoing_consumptions(id)
) COMMENT '待结算商品消费明细';
-- ============================================================
-- 第三部分：订单表（4 张）
-- 合并 unpaid order.json + history_orders.json
-- ============================================================

-- 7. 订单主表
CREATE TABLE IF NOT EXISTS orders (
    order_id    VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '订单编号',
    桌台编号    VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    支付状态    VARCHAR(10)    NOT NULL DEFAULT '未支付' COMMENT '支付状态',
    总金额      DECIMAL(10,2)  NOT NULL COMMENT '订单总金额',
    创建时间    BIGINT         NOT NULL COMMENT '订单创建时间戳',
    支付时间    BIGINT         NULL     COMMENT '支付时间戳,未支付时为NULL',
    INDEX idx_orders_table (桌台编号),
    INDEX idx_orders_status (支付状态),
    INDEX idx_orders_time (创建时间)
) COMMENT '订单主表';

-- 8. 订单消费关联表
CREATE TABLE IF NOT EXISTS order_consumptions (
    consumption_id  VARCHAR(30)    NOT NULL COMMENT '消费记录编号',
    order_id        VARCHAR(30)    NOT NULL COMMENT '所属订单编号',
    type_code       CHAR(1)        NOT NULL COMMENT '消费类型代码',
    PRIMARY KEY (consumption_id, order_id),
    INDEX idx_oc_order (order_id),
    INDEX idx_oc_type (type_code),
    CONSTRAINT fk_oc_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
    CONSTRAINT fk_oc_type FOREIGN KEY (type_code) REFERENCES consumption_types(type_code)
) COMMENT '订单消费关联表';

-- 9. 订单台位费明细表
CREATE TABLE IF NOT EXISTS order_table_fees (
    consumption_id  VARCHAR(30)    NOT NULL COMMENT '消费记录编号',
    order_id        VARCHAR(30)    NOT NULL COMMENT '所属订单编号',
    开台时间        BIGINT         NOT NULL COMMENT '开台时间戳',
    关台时间        BIGINT         NOT NULL COMMENT '关台时间戳',
    每小时价格      DECIMAL(10,2)  NOT NULL COMMENT '每小时价格快照',
    金额            DECIMAL(10,2)  NOT NULL COMMENT '台位费金额',
    PRIMARY KEY (consumption_id, order_id),
    INDEX idx_otf_order (order_id),
    CONSTRAINT fk_otf_order FOREIGN KEY (order_id) REFERENCES orders(order_id)
) COMMENT '订单台位费明细';

-- 10. 订单商品明细表
CREATE TABLE IF NOT EXISTS order_product_consumptions (
    consumption_id  VARCHAR(30)    NOT NULL COMMENT '消费记录编号',
    order_id        VARCHAR(30)    NOT NULL COMMENT '所属订单编号',
    商品编号        VARCHAR(10)    NOT NULL COMMENT '商品编号',
    商品名称        VARCHAR(50)    NOT NULL COMMENT '商品名称快照',
    单价            DECIMAL(10,2)  NOT NULL COMMENT '销售单价快照',
    数量            INT            NOT NULL COMMENT '购买数量',
    金额            DECIMAL(10,2)  NOT NULL COMMENT '小计金额',
    PRIMARY KEY (consumption_id, order_id),
    INDEX idx_opc_order (order_id),
    CONSTRAINT fk_opc_order FOREIGN KEY (order_id) REFERENCES orders(order_id)
) COMMENT '订单商品明细';

-- ============================================================
-- 第四部分：安全底稿（1 张）
-- ============================================================

-- 11. 核心备份表
CREATE TABLE IF NOT EXISTS core_backup (
    consumption_id  VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号        VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    开台时间        BIGINT         NOT NULL COMMENT '开台时间戳',
    关台时间        BIGINT         NOT NULL COMMENT '关台时间戳',
    INDEX idx_cb_table (桌台编号)
) COMMENT '核心备份表-只写不读,数据修复用';
