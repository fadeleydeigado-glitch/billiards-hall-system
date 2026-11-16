# -*- coding: utf-8 -*-
"""开台前未结账消费提醒的控制器测试；不连接数据库。"""

import unittest
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

from backend import controller_layer, local_cache
from backend.errors import status_code_for_result


TABLE_ID = "1"


class OpenTableUnsettledTests(unittest.TestCase):
    def setUp(self):
        local_cache.clear_all()
        self.tables = {
            TABLE_ID: {
                "每小时价格": Decimal("30.00"),
            }
        }
        self.table_consumption_boxes = {
            TABLE_ID: {
                "待结算消费": {
                    "T_1_1700000000": {
                        "消费记录编号": "T_1_1700000000",
                        "类型": "台位费",
                        "开台时间": 1700000000,
                        "关台时间": 1700003600,
                        "每小时价格": Decimal("30.00"),
                        "金额": Decimal("30.00"),
                    },
                    "S_1_1700000001": {
                        "消费记录编号": "S_1_1700000001",
                        "类型": "商品",
                        "商品名称": "可乐",
                        "单价": Decimal("3.00"),
                        "数量": 2,
                        "金额": Decimal("6.00"),
                    },
                }
            }
        }
        self.flow_box = {
            "桌子号码": TABLE_ID,
            "台位消费": {
                "消费记录编号": "T_1_new",
                "类型": "台位费",
                "开台时间": 1700007200,
                "关台时间": None,
                "每小时价格": Decimal("30.00"),
                "金额": None,
            },
            "当前步骤": "已创建台位消费记录",
        }
        self.insert_result = {
            "status": "success",
            "error_type": None,
            "data": self.flow_box["台位消费"],
            "message": "1 号桌台位费写入成功",
        }

    def _run_open(self, confirm_unsettled=False, empty_tables=None):
        if empty_tables is None:
            empty_tables = [TABLE_ID]

        def success(data):
            return {
                "status": "success",
                "error_type": None,
                "data": data,
                "message": "ok",
            }

        events = []

        def fake_execute(operation):
            result = operation(object())
            if result["status"] == "success":
                events.append("commit")
            return result

        with ExitStack() as stack:
            def patch_controller(name, **kwargs):
                return stack.enter_context(
                    patch.object(controller_layer, name, **kwargs)
                )

            patch_controller("check_table_exists", return_value=True)
            patch_controller(
                "one_load_billiards_tables_from_mysql",
                return_value=success(self.tables),
            )
            patch_controller(
                "two_check_billiards_tables",
                side_effect=lambda data: success(data),
            )
            patch_controller(
                "two_get_table_info",
                side_effect=lambda data, table_id: success(data[table_id]),
            )
            patch_controller(
                "one_load_all_ongoing_from_mysql",
                return_value=success(self.table_consumption_boxes),
            )
            patch_controller(
                "two_check_all_table_consumption_boxes",
                side_effect=lambda data: success(data),
            )
            patch_controller(
                "two_get_table_consumption_box",
                side_effect=lambda data, table_id: success(data[table_id]),
            )
            patch_controller(
                "one_get_current_time_pair",
                return_value=(1700007200, 1700007200000),
            )
            patch_controller(
                "two_create_flow_box",
                return_value=success(self.flow_box),
            )
            patch_controller(
                "one_execute_in_transaction",
                side_effect=fake_execute,
            )
            patch_controller(
                "one_lock_billiards_table",
                return_value=success(self.tables[TABLE_ID]),
            )
            patch_controller(
                "one_load_ongoing_by_table_with_cursor",
                return_value=success(self.table_consumption_boxes),
            )
            patch_controller(
                "two_get_empty_billiards_tables",
                return_value=success(empty_tables),
            )
            insert_mock = patch_controller(
                "one_insert_ongoing_table_fee_to_mysql",
                return_value=self.insert_result,
            )
            invalidate_mock = patch_controller(
                "invalidate_tables_cache_after_commit",
                side_effect=lambda: events.append("invalidate"),
            )
            patch_controller("one_write_business_log", return_value=None)

            result = controller_layer.three_open_table(
                TABLE_ID,
                confirm_unsettled=confirm_unsettled,
            )

        return result, insert_mock, invalidate_mock, events

    def test_open_table_warns_before_mixing_unsettled_consumptions(self):
        result, insert_mock, invalidate_mock, _events = self._run_open()

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["error_type"],
            "TableHasUnsettledConsumptions",
        )
        self.assertEqual(status_code_for_result(result), 409)
        self.assertEqual(result["data"]["汇总"]["消费笔数"], 2)
        self.assertEqual(
            result["data"]["汇总"]["合计金额"],
            Decimal("36.00"),
        )
        self.assertTrue(result["data"]["可确认开台"])
        insert_mock.assert_not_called()
        invalidate_mock.assert_not_called()

    def test_open_table_can_continue_after_confirmation(self):
        result, insert_mock, invalidate_mock, events = self._run_open(
            confirm_unsettled=True
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["消费记录编号"], "T_1_new")
        insert_mock.assert_called_once()
        invalidate_mock.assert_called_once()
        self.assertEqual(events, ["commit", "invalidate"])

    def test_occupied_table_error_takes_precedence(self):
        result, insert_mock, invalidate_mock, _events = self._run_open(
            empty_tables=[]
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "TableOccupied")
        self.assertEqual(status_code_for_result(result), 409)
        insert_mock.assert_not_called()
        invalidate_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
