-- 为已有 billiards_hall 库增加“同一桌只能有一条未关台记录”的数据库约束。
-- 全新环境直接执行 database/schema.sql 即可；本文件用于已有库增量升级。
--
-- 执行 ALTER 前必须先运行下面的预检 SQL，结果必须为空。
-- 如果返回数据，说明同一桌存在多条关台时间为 NULL 的历史脏数据；
-- 应先核对实际消费，补上关台时间或清理重复记录，再执行 ALTER。

USE billiards_hall;

SELECT
    `桌台编号`,
    COUNT(*) AS unclosed_count,
    GROUP_CONCAT(consumption_id ORDER BY consumption_id SEPARATOR ',') AS consumption_ids
FROM ongoing_table_fees
WHERE `关台时间` IS NULL
GROUP BY `桌台编号`
HAVING COUNT(*) > 1
ORDER BY `桌台编号`;

-- active_marker 在未关台时为 1，关台后自动变成 NULL。
-- 联合唯一索引利用 MySQL“多个 NULL 可共存”的规则：
-- - 同一桌最多一条未关台记录，由 (桌台编号, 1) 唯一约束保证；
-- - 同一桌可以保留任意多条已关台历史记录，因为它们都是 (桌台编号, NULL)。
ALTER TABLE ongoing_table_fees
    ADD COLUMN active_marker TINYINT GENERATED ALWAYS AS (
        CASE WHEN `关台时间` IS NULL THEN 1 ELSE NULL END
    ) VIRTUAL COMMENT '未关台唯一标记',
    ADD UNIQUE KEY uniq_otf_table_active (`桌台编号`, active_marker);

-- 本地 billiards_hall_test 需要同步验证时，将上面的 USE 改为：
-- USE billiards_hall_test;
