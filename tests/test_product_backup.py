# -*- coding: utf-8 -*-
"""
商品消费核心备份的数据组装测试。

全部使用 Python 标准库 unittest，不连接数据库：
1. 台位费备份仍然正常；
2. 商品备份能正确组装 S_ 记录；
3. 缺少字段、金额不一致、数量非法等异常会被拦截；
4. 商品消费记录本身带有创建时间，供备份使用。
"""

import json
import os
import sys
import unittest
from decimal import Decimal


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


from backend.business_layer import (
    two_create_product_consumption,
    two_prepare_core_backup,
)
from backend.outbox import (
    build_product_event_payload,
    build_table_fee_event_payload,
)


def build_product_record(**overrides):
    record = {
        "消费记录编号": "S_1_1700000000",
        "类型": "商品",
        "商品编号": "P_001",
        "商品名称": "可乐",
        "单价": Decimal("3.00"),
        "数量": 2,
        "金额": Decimal("6.00"),
        "创建时间": 1700000000,
    }
    record.update(overrides)
    return record


class CoreBackupPreparationTests(unittest.TestCase):
    def test_table_fee_backup_still_supported(self):
        result = two_prepare_core_backup("1", {
            "消费记录编号": "T_1_1700000000",
            "开台时间": 1700000000,
            "关台时间": 1700003600,
        })

        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(data["备份类型"], "台位费")
        self.assertEqual(data["消费记录编号"], "T_1_1700000000")
        self.assertEqual(data["桌子号码"], "1")
        self.assertEqual(data["开台时间"], 1700000000)
        self.assertEqual(data["关台时间"], 1700003600)

    def test_product_backup_success(self):
        result = two_prepare_core_backup("1", build_product_record())

        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(data["备份类型"], "商品")
        self.assertEqual(data["消费记录编号"], "S_1_1700000000")
        self.assertEqual(data["桌子号码"], "1")
        self.assertEqual(data["商品编号"], "P_001")
        self.assertEqual(data["商品名称"], "可乐")
        self.assertEqual(data["单价"], Decimal("3.00"))
        self.assertEqual(data["数量"], 2)
        self.assertEqual(data["金额"], Decimal("6.00"))
        self.assertEqual(data["创建时间"], 1700000000)

    def test_product_backup_missing_field(self):
        record = build_product_record()
        del record["商品名称"]

        result = two_prepare_core_backup("1", record)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ProductBackupFieldMissing")

    def test_product_backup_amount_mismatch(self):
        result = two_prepare_core_backup(
            "1",
            build_product_record(**{"金额": Decimal("7.00")})
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ProductBackupAmountMismatch")

    def test_product_backup_invalid_quantity(self):
        result = two_prepare_core_backup(
            "1",
            build_product_record(**{"数量": 0})
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ProductBackupQuantityFormatError")

    def test_product_backup_invalid_unit_price(self):
        result = two_prepare_core_backup(
            "1",
            build_product_record(**{"单价": Decimal("-1.00")})
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ProductBackupUnitPriceFormatError")

    def test_unknown_consumption_prefix(self):
        result = two_prepare_core_backup(
            "1",
            build_product_record(**{"消费记录编号": "X_1_1700000000"})
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "UnknownConsumptionType")


class ProductConsumptionCreatedTimeTests(unittest.TestCase):
    def test_product_consumption_contains_created_time(self):
        flow_box = {
            "商品编号": "P_001",
            "商品信息": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 10,
            },
            "购买数量": 2,
        }

        result = two_create_product_consumption("1", flow_box, 1700000000)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["创建时间"], 1700000000)


class OutboxPayloadTests(unittest.TestCase):
    def test_table_fee_payload_is_json_safe(self):
        payload = build_table_fee_event_payload({
            "消费记录编号": "T_1_1700000000",
            "桌子号码": "1",
            "开台时间": 1700000000,
            "关台时间": 1700003600,
        })

        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["consumption_id"], "T_1_1700000000")
        self.assertEqual(payload["table_id"], "1")
        self.assertEqual(payload["start_time"], 1700000000)
        self.assertEqual(payload["close_time"], 1700003600)

    def test_product_payload_converts_money_to_string(self):
        payload = build_product_event_payload({
            "消费记录编号": "S_1_1700000000",
            "桌子号码": "1",
            "商品编号": "P_001",
            "商品名称": "可乐",
            "单价": Decimal("3.00"),
            "数量": 2,
            "金额": Decimal("6.00"),
            "创建时间": 1700000000,
        })

        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["unit_price"], "3.00")
        self.assertEqual(payload["amount"], "6.00")
        self.assertEqual(payload["quantity"], 2)
        self.assertEqual(payload["created_at"], 1700000000)


if __name__ == "__main__":
    unittest.main()
