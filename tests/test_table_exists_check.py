# -*- coding: utf-8 -*-
"""桌号存在性检查及五个入口的第一道校验测试；不连接数据库。"""

import unittest
from unittest.mock import patch

from backend import controller_layer, local_cache
from backend.errors import status_code_for_result


def success_result(tables: dict) -> dict:
    return {
        "status": "success",
        "error_type": None,
        "data": tables,
        "message": "桌台配置表读取成功",
    }


class CheckTableExistsTests(unittest.TestCase):
    def setUp(self):
        local_cache.clear_all()

    def test_existing_table_returns_true(self):
        with patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value=success_result({"1": {"每小时价格": 30}}),
        ):
            self.assertTrue(controller_layer.check_table_exists("1"))

    def test_numeric_table_number_is_checked_as_string(self):
        with patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value=success_result({"1": {"每小时价格": 30}}),
        ):
            self.assertTrue(controller_layer.check_table_exists(1))

    def test_missing_table_returns_false(self):
        with patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value=success_result({"1": {"每小时价格": 30}}),
        ):
            self.assertFalse(controller_layer.check_table_exists("999"))

    def test_database_failure_raises_runtime_error(self):
        with patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value={
                "status": "error",
                "error_type": "OperationalError",
                "data": None,
                "message": "读取桌台配置表失败：连接失败",
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                controller_layer.check_table_exists("1")

        self.assertIn("读取桌台配置表失败", str(raised.exception))


class TableGuardTests(unittest.TestCase):
    def setUp(self):
        local_cache.clear_all()
        self.tables_patch = patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value=success_result({"1": {"每小时价格": 30}}),
        )
        self.log_patch = patch.object(
            controller_layer,
            "one_write_business_log",
            return_value=None,
        )
        self.tables_patch.start()
        self.log_patch.start()
        self.addCleanup(self.tables_patch.stop)
        self.addCleanup(self.log_patch.stop)

    def assert_table_not_found(self, result: dict) -> None:
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "TableNotFound")
        self.assertIsNone(result["data"])
        self.assertIn("999", result["message"])
        self.assertEqual(status_code_for_result(result), 404)

    def test_checkout_options_rejects_missing_table_first(self):
        with patch.object(
            controller_layer,
            "one_load_ongoing_by_table_from_mysql",
            side_effect=AssertionError("桌号不存在时不应继续读待结算消费"),
        ):
            result = controller_layer.three_get_frontend_checkout_options("999")

        self.assert_table_not_found(result)

    def test_open_table_rejects_missing_table_first(self):
        with patch.object(
            controller_layer,
            "one_load_all_ongoing_from_mysql",
            side_effect=AssertionError("桌号不存在时不应继续读全部待结算消费"),
        ):
            result = controller_layer.three_open_table("999")

        self.assert_table_not_found(result)

    def test_close_table_rejects_missing_table_first(self):
        with patch.object(
            controller_layer,
            "one_load_ongoing_by_table_from_mysql",
            side_effect=AssertionError("桌号不存在时不应继续读单桌待结算消费"),
        ):
            result = controller_layer.three_close_table("999")

        self.assert_table_not_found(result)

    def test_create_unpaid_order_rejects_missing_table_first(self):
        with patch.object(
            controller_layer,
            "one_load_ongoing_by_table_from_mysql",
            side_effect=AssertionError("桌号不存在时不应继续读单桌待结算消费"),
        ):
            result = controller_layer.three_create_unpaid_order({
                "桌子号码": "999",
                "消费记录编号列表": ["T_999_0000000000"],
            })

        self.assert_table_not_found(result)

    def test_add_product_rejects_missing_table_first(self):
        with patch.object(
            controller_layer,
            "one_load_product_by_id_from_mysql",
            side_effect=AssertionError("桌号不存在时不应继续读商品"),
        ):
            result = controller_layer.three_add_product_consumption(
                "999", "P_001", 1
            )

        self.assert_table_not_found(result)


if __name__ == "__main__":
    unittest.main()
