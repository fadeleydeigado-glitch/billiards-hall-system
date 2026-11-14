# -*- coding: utf-8 -*-
"""真实 MySQL 验证开台唯一约束；只允许在 billiards_hall_test 中运行。"""

import os
import sys
import uuid
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import io_layer


def main():
    if os.environ.get("ALLOW_WRITE_TESTS") != "1":
        print("跳过：需要设置 ALLOW_WRITE_TESTS=1")
        return 0

    if os.environ.get("MYSQL_DATABASE") != "billiards_hall_test":
        print("拒绝运行：MYSQL_DATABASE 必须是 billiards_hall_test")
        return 2

    connection = pymysql.connect(**io_layer.DB_CONFIG)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT bt.id FROM billiards_tables AS bt "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM ongoing_table_fees AS otf "
                "  WHERE otf.`桌台编号` = bt.id AND otf.`关台时间` IS NULL"
                ") ORDER BY bt.id LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                raise AssertionError("测试库中没有可用于验证的空闲桌台")
            table_id = row[0]

            suffix = uuid.uuid4().hex[:12]
            first_id = f"T_UT1_{suffix}"
            second_id = f"T_UT2_{suffix}"
            third_id = f"T_UT3_{suffix}"
            start_time = 1700000000

            first = io_layer.one_insert_ongoing_table_fee_to_mysql(
                cursor, table_id, first_id, start_time, 30
            )
            assert first["status"] == "success", first

            second = io_layer.one_insert_ongoing_table_fee_to_mysql(
                cursor, table_id, second_id, start_time + 1, 30
            )
            assert second["status"] == "error", second
            assert second["error_type"] == "TableOccupied", second
            assert second["message"] == "该桌台已处于开台状态", second

            cursor.execute(
                "SELECT active_marker FROM ongoing_table_fees "
                "WHERE consumption_id = %s",
                (first_id,),
            )
            assert cursor.fetchone()[0] == 1

            closed = io_layer.one_update_ongoing_table_fee_in_mysql(
                cursor, first_id, start_time + 3600, 30
            )
            assert closed["status"] == "success", closed

            cursor.execute(
                "SELECT active_marker FROM ongoing_table_fees "
                "WHERE consumption_id = %s",
                (first_id,),
            )
            assert cursor.fetchone()[0] is None

            third = io_layer.one_insert_ongoing_table_fee_to_mysql(
                cursor, table_id, third_id, start_time + 3601, 30
            )
            assert third["status"] == "success", third

        connection.rollback()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM ongoing_table_fees "
                "WHERE consumption_id IN (%s, %s, %s)",
                (first_id, second_id, third_id),
            )
            assert cursor.fetchone()[0] == 0
    finally:
        connection.close()

    print("通过：同一桌第二条未关台记录被唯一索引拦截，并返回友好业务错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
