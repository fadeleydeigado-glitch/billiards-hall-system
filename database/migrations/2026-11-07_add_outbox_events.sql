-- ============================================================
-- 为现有 billiards_hall 库增加事务性 outbox 事件表。
-- 可重复执行：CREATE TABLE IF NOT EXISTS。
-- ============================================================

USE billiards_hall;

CREATE TABLE IF NOT EXISTS outbox_events (
    event_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '事件编号',
    event_type    VARCHAR(40)     NOT NULL COMMENT '事件类型：core_backup / core_backup_product',
    payload       JSON            NOT NULL COMMENT '事件载荷',
    status        VARCHAR(12)     NOT NULL DEFAULT 'pending' COMMENT 'pending/processing/done/dead',
    retry_count   INT             NOT NULL DEFAULT 0 COMMENT '已失败次数',
    next_retry_at BIGINT          NOT NULL COMMENT '下次可处理时间戳',
    locked_at     BIGINT          NULL COMMENT '开始处理时间戳',
    locked_by     VARCHAR(80)     NULL COMMENT '处理者标识',
    last_error    VARCHAR(500)    NULL COMMENT '最近一次错误',
    created_at    BIGINT          NOT NULL COMMENT '创建时间戳',
    updated_at    BIGINT          NOT NULL COMMENT '更新时间戳',
    PRIMARY KEY (event_id),
    INDEX idx_outbox_status_retry (status, next_retry_at, event_id),
    INDEX idx_outbox_type (event_type)
) COMMENT '事务性 outbox 事件表';
