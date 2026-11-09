-- ============================================================
-- 台球厅管理系统 — 独立备份库建库语句
-- 数据库名: billiards_backup
-- 说明:
--   1. 本库只存核心备份，不存业务事实源；
--   2. 由 outbox worker 和补录脚本写入；
--   3. 主库业务事务绝不直接连本库。
-- ============================================================

CREATE DATABASE IF NOT EXISTS billiards_backup
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE billiards_backup;

-- 1. 台位费核心备份表
CREATE TABLE IF NOT EXISTS core_backup (
    consumption_id  VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号        VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    开台时间        BIGINT         NOT NULL COMMENT '开台时间戳',
    关台时间        BIGINT         NOT NULL COMMENT '关台时间戳',
    INDEX idx_cb_table (桌台编号)
) COMMENT '核心备份表-只写不读,数据修复用';

-- 2. 商品消费核心备份表
CREATE TABLE IF NOT EXISTS core_backup_products (
    consumption_id  VARCHAR(30)    NOT NULL PRIMARY KEY COMMENT '消费记录编号',
    桌台编号        VARCHAR(10)    NOT NULL COMMENT '所属桌台',
    商品编号        VARCHAR(10)    NOT NULL COMMENT '商品编号',
    商品名称        VARCHAR(50)    NOT NULL COMMENT '商品名称快照',
    单价            DECIMAL(10,2)  NOT NULL COMMENT '销售单价快照',
    数量            INT            NOT NULL COMMENT '购买数量',
    金额            DECIMAL(10,2)  NOT NULL COMMENT '小计金额',
    创建时间        BIGINT         NOT NULL COMMENT '消费创建时间戳',
    INDEX idx_cbp_table (桌台编号),
    INDEX idx_cbp_product (商品编号)
) COMMENT '商品消费核心备份表-只写不读,数据修复用';
