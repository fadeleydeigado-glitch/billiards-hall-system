# -*- coding: utf-8 -*-
"""开台唯一约束的兜底处理测试；不连接真实数据库。"""

import unittest
from unittest.mock import patch

import pymysql

from backend import io_layer
from backend.errors import status_code_for_result


class DuplicateActiveMarkerCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "INSERT INTO ongoing_table_fees" in sql:
            raise pymysql.err.IntegrityError(
                1062,
                "Duplicate entry '1-1' for key "
                "'ongoing_table_fees.uniq_otf_table_active'",
            )


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.began = False
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def begin(self):
        self.began = True

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class OpenTableUniqueTests(unittest.TestCase):
    def test_duplicate_active_table_rolls_back_with_friendly_error(self):
        cursor = DuplicateActiveMarkerCursor()
        connection = FakeConnection(cursor)

        with patch.object(
            io_layer,
            "one_get_mysql_connection",
            return_value=connection,
        ):
            result = io_layer.one_execute_in_transaction(
                lambda tx_cursor: io_layer.one_insert_ongoing_table_fee_to_mysql(
                    tx_cursor,
                    "1",
                    "T_1_unique_test",
                    1700000000,
                    30,
                )
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "TableOccupied")
        self.assertEqual(result["message"], "该桌台已处于开台状态")
        self.assertEqual(status_code_for_result(result), 409)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertEqual(len(cursor.executed), 2)


if __name__ == "__main__":
    unittest.main()
