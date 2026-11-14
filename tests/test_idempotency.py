# -*- coding: utf-8 -*-
"""加商品幂等链路测试；不连接真实数据库。"""

import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import pymysql

from backend import api_layer, controller_layer, io_layer
from backend.errors import status_code_for_result


OPERATION = "add_product_consumption"


class FakeCursor:
    def __init__(
        self,
        fetchone_result=None,
        execute_error=None,
        execute_error_sql_contains=None,
    ):
        self.fetchone_result = fetchone_result
        self.execute_error = execute_error
        self.execute_error_sql_contains = execute_error_sql_contains
        self.executed = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if (
            self.execute_error is not None
            and (
                self.execute_error_sql_contains is None
                or self.execute_error_sql_contains in sql
            )
        ):
            raise self.execute_error

    def fetchone(self):
        return self.fetchone_result


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.began = False
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def begin(self):
        self.began = True

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def success_result(data=None, message="成功"):
    return {
        "status": "success",
        "error_type": None,
        "data": data,
        "message": message,
    }


class ApiHeaderTests(unittest.TestCase):
    def test_missing_header_is_rejected_before_controller(self):
        with patch.object(api_layer, "three_add_product_consumption") as handler:
            response = api_layer.api_add_product_consumption(
                "1",
                "P_001",
                1,
                None,
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error_type"], "IdempotencyKeyRequired")
        handler.assert_not_called()

    def test_header_is_trimmed_and_passed_to_controller(self):
        expected = success_result(None, "成功")

        with patch.object(
            api_layer,
            "three_add_product_consumption",
            return_value=expected,
        ) as handler:
            response = api_layer.api_add_product_consumption(
                "1",
                "P_001",
                2,
                "  key-123  ",
            )

        self.assertEqual(response.status_code, 200)
        handler.assert_called_once_with(
            "1",
            "P_001",
            2,
            idempotency_key="key-123",
        )


class ControllerIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = patch.object(
            controller_layer,
            "one_write_business_log",
            return_value=None,
        )
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_missing_key_is_rejected_after_table_check(self):
        with patch.object(controller_layer, "check_table_exists", return_value=True):
            with patch.object(
                controller_layer,
                "one_load_product_by_id_from_mysql",
                side_effect=AssertionError("缺少 Key 时不应读取商品"),
            ):
                result = controller_layer.three_add_product_consumption(
                    "1",
                    "P_001",
                    1,
                )

        self.assertEqual(result["error_type"], "IdempotencyKeyRequired")
        self.assertEqual(status_code_for_result(result), 422)

    def test_completed_key_returns_first_result_without_business_reads(self):
        first_result = success_result(
            {"消费记录编号": "S_1_tabc_abc123"},
            "首次结果",
        )

        with patch.object(controller_layer, "check_table_exists", return_value=True):
            with patch.object(
                controller_layer,
                "one_get_completed_idempotency_result_from_mysql",
                return_value=success_result(first_result),
            ):
                with patch.object(
                    controller_layer,
                    "one_load_product_by_id_from_mysql",
                    side_effect=AssertionError("命中幂等记录后不应读取商品"),
                ):
                    result = controller_layer.three_add_product_consumption(
                        "1",
                        "P_001",
                        1,
                        idempotency_key="same-key",
                    )

        self.assertIs(result, first_result)

    def test_business_failure_after_a_concurrent_success_replays_first_result(self):
        first_result = success_result(
            {"消费记录编号": "S_1_tabc_abc123"},
            "首次结果",
        )
        lookup_results = [
            success_result(None),
            success_result(first_result),
        ]

        with patch.object(controller_layer, "check_table_exists", return_value=True):
            with patch.object(
                controller_layer,
                "one_get_completed_idempotency_result_from_mysql",
                side_effect=lookup_results,
            ):
                with patch.object(
                    controller_layer,
                    "one_load_product_by_id_from_mysql",
                    return_value={
                        "status": "error",
                        "error_type": "ProductStockNotEnough",
                        "data": None,
                        "message": "库存不足",
                    },
                ):
                    result = controller_layer.three_add_product_consumption(
                        "1",
                        "P_001",
                        1,
                        idempotency_key="same-key",
                    )

        self.assertIs(result, first_result)


class IdempotencyTransactionTests(unittest.TestCase):
    def test_existing_completed_record_is_returned_without_operation(self):
        first_result = success_result(
            {"消费记录编号": "S_1_tabc_abc123", "金额": "6.00"},
            "首次结果",
        )
        cursor = FakeCursor((
            OPERATION,
            io_layer.IDEMPOTENCY_STATUS_COMPLETED,
            json.dumps(first_result, ensure_ascii=False),
        ))
        connection = FakeConnection(cursor)
        called = []

        def operation(_cursor):
            called.append(True)
            return success_result(None, "不应执行")

        with patch.object(io_layer, "one_get_mysql_connection", return_value=connection):
            result = io_layer.one_execute_in_transaction_with_idempotency(
                operation,
                "same-key",
                OPERATION,
            )

        self.assertEqual(result, first_result)
        self.assertEqual(called, [])
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_success_stores_result_and_commits(self):
        result = success_result(
            {
                "消费记录编号": "S_1_tabc_abc123",
                "单价": Decimal("3.00"),
                "金额": Decimal("6.00"),
            },
            "首次结果",
        )
        cursor = FakeCursor(None)
        connection = FakeConnection(cursor)

        with patch.object(io_layer, "one_get_mysql_connection", return_value=connection):
            returned = io_layer.one_execute_in_transaction_with_idempotency(
                lambda _cursor: result,
                "same-key",
                OPERATION,
            )

        self.assertIs(returned, result)
        self.assertTrue(connection.committed)
        update_params = [
            params
            for sql, params in cursor.executed
            if "UPDATE idempotency_records" in sql
        ][0]
        stored = json.loads(update_params[1])
        self.assertEqual(stored["data"]["单价"], "3.00")
        self.assertEqual(stored["data"]["金额"], "6.00")
        self.assertEqual(stored["message"], "首次结果")

    def test_duplicate_key_rolls_back_and_returns_first_result(self):
        first_result = success_result(
            {"消费记录编号": "S_1_tabc_abc123"},
            "首次结果",
        )
        duplicate_error = pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'same-key' for key 'idempotency_records.PRIMARY'",
        )
        cursor = FakeCursor(
            None,
            execute_error=duplicate_error,
            execute_error_sql_contains="INSERT INTO idempotency_records",
        )
        connection = FakeConnection(cursor)

        with patch.object(io_layer, "one_get_mysql_connection", return_value=connection):
            with patch.object(
                io_layer,
                "one_get_idempotency_result_after_conflict",
                return_value=first_result,
            ) as replay:
                result = io_layer.one_execute_in_transaction_with_idempotency(
                    lambda _cursor: success_result(None, "不应执行"),
                    "same-key",
                    OPERATION,
                )

        self.assertIs(result, first_result)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        replay.assert_called_once_with("same-key", OPERATION)

    def test_operation_failure_rolls_back_placeholder_and_writes(self):
        cursor = FakeCursor(None)
        connection = FakeConnection(cursor)
        failure = {
            "status": "error",
            "error_type": "SimulatedFailure",
            "data": None,
            "message": "模拟失败",
        }

        with patch.object(io_layer, "one_get_mysql_connection", return_value=connection):
            result = io_layer.one_execute_in_transaction_with_idempotency(
                lambda _cursor: failure,
                "same-key",
                OPERATION,
            )

        self.assertIs(result, failure)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertFalse(any(
            "UPDATE idempotency_records" in sql
            for sql, _params in cursor.executed
        ))


class IdempotencyRecordTests(unittest.TestCase):
    def test_reused_key_for_another_operation_is_rejected(self):
        result = io_layer.one_result_from_idempotency_record({
            "operation": "another_operation",
            "status": io_layer.IDEMPOTENCY_STATUS_COMPLETED,
            "result": success_result(None),
        }, OPERATION)

        self.assertEqual(result["error_type"], "IdempotencyKeyReused")
        self.assertEqual(status_code_for_result(result), 409)

    def test_processing_record_returns_conflict(self):
        result = io_layer.one_result_from_idempotency_record({
            "operation": OPERATION,
            "status": io_layer.IDEMPOTENCY_STATUS_PROCESSING,
            "result": None,
        }, OPERATION)

        self.assertEqual(result["error_type"], "IdempotencyInProgress")
        self.assertEqual(status_code_for_result(result), 409)

    def test_duplicate_detection_only_matches_mysql_1062(self):
        duplicate = pymysql.err.IntegrityError(1062, "Duplicate entry")
        foreign_key = pymysql.err.IntegrityError(1452, "Foreign key")

        self.assertTrue(io_layer.one_is_duplicate_entry_error(duplicate))
        self.assertFalse(io_layer.one_is_duplicate_entry_error(foreign_key))


if __name__ == "__main__":
    unittest.main()
