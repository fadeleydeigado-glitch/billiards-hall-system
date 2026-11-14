-- 为已有 billiards_hall 库增加加商品接口的幂等防重表。
-- 全新环境直接执行 database/schema.sql 即可，本文件用于已有库增量升级。

USE billiards_hall;

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key VARCHAR(128)  NOT NULL COMMENT '前端 Idempotency-Key 请求头',
    operation       VARCHAR(50)   NOT NULL COMMENT '写操作标识，如 add_product_consumption',
    status          VARCHAR(12)   NOT NULL DEFAULT 'processing' COMMENT 'processing / completed',
    response_json   JSON          NULL COMMENT '第一次成功返回的四键结果',
    created_at      BIGINT        NOT NULL COMMENT '占位创建时间戳',
    completed_at    BIGINT        NULL COMMENT '成功完成时间戳',
    PRIMARY KEY (idempotency_key),
    INDEX idx_idempotency_operation (operation)
) COMMENT '接口幂等防重记录表';

-- 如果应用账号使用表级授权，建表后需要用 root 补一次授权：
-- GRANT SELECT, INSERT, UPDATE
-- ON billiards_hall.idempotency_records
-- TO 'billiards_app'@'localhost';
