-- 为已有 billiards_hall 库增加待结算消费主表的桌台外键。
-- 全新环境直接执行 database/schema.sql 即可；本文件用于已有库增量升级。
--
-- 执行 ALTER 前必须先运行下面的预检 SQL，结果必须为空。
-- 如果返回数据，说明 ongoing_consumptions 中存在桌台配置表没有的桌号，
-- 应先核对并清理幽灵消费，再执行 ALTER。

USE billiards_hall;

SELECT
    oc.id,
    oc.`桌台编号`,
    oc.type_code,
    oc.`创建时间`
FROM ongoing_consumptions AS oc
LEFT JOIN billiards_tables AS bt
    ON bt.id = oc.`桌台编号`
WHERE bt.id IS NULL
ORDER BY oc.id;

ALTER TABLE ongoing_consumptions
    ADD CONSTRAINT fk_ongoing_table
    FOREIGN KEY (`桌台编号`) REFERENCES billiards_tables(id);
