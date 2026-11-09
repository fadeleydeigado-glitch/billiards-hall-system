# -*- coding: utf-8 -*-
"""
金额计算回归测试。

全部使用 Python 标准库 unittest，不连接数据库：
1. 台费除法、乘法、向下取整必须与精确分数结果一致；
2. 商品小计和订单合计必须是 Decimal；
3. Decimal 在 API 出口能安全序列化为 JSON。
"""

import json
import unittest
from decimal import Decimal
from fractions import Fraction

from backend.business_layer import (
    two_calculate_table_fee,
    two_create_product_consumption,
    two_create_unpaid_bill,
    two_get_frontend_checkout_options,
    two_get_frontend_unpaid_orders_info,
)
from backend.json_utils import decimal_to_json


def build_fee_flow(seconds, hour_price):
    return {
        "桌子号码": "1",
        "台位消费": {
            "消费记录编号": "T_1_1700000000",
            "类型": "台位费",
            "开台时间": 1700000000,
            "关台时间": 1700000000 + seconds,
            "每小时价格": hour_price,
            "金额": None
        }
    }


class TableFeeDecimalTests(unittest.TestCase):
    def test_float_boundary_is_floored_exactly(self):
        # 精确结果是 1 元；旧浮点算法 (5000 / 3600) * 0.72
        # 可能得到 0.9999999999999999，再 int() 就错成 0。
        result = two_calculate_table_fee(
            build_fee_flow(5000, Decimal("0.72"))
        )

        self.assertEqual(result["status"], "success")
        amount = result["data"]["台位消费"]["金额"]
        self.assertIsInstance(amount, Decimal)
        self.assertEqual(amount, Decimal("1"))

    def test_table_fee_matches_exact_fraction(self):
        cases = [
            (0, "30.00"),
            (1, "59.99"),
            (3599, "60.00"),
            (3600, "30.15"),
            (2500, "1.44"),
            (5000, "0.72"),
            (86401, "45.50"),
        ]

        for seconds, price_text in cases:
            with self.subTest(seconds=seconds, price=price_text):
                price = Decimal(price_text)
                price_cents = int(price * 100)
                exact = Fraction(seconds * price_cents, 360000)
                expected = exact.numerator // exact.denominator

                result = two_calculate_table_fee(
                    build_fee_flow(seconds, price)
                )

                self.assertEqual(result["status"], "success")
                self.assertEqual(
                    result["data"]["台位消费"]["金额"],
                    Decimal(expected)
                )

    def test_half_hour_is_floored_to_whole_yuan(self):
        result = two_calculate_table_fee(
            build_fee_flow(1800, Decimal("30.99"))
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["data"]["台位消费"]["金额"],
            Decimal("15")
        )

    def test_table_must_be_closed(self):
        flow_box = build_fee_flow(3600, Decimal("30.00"))
        flow_box["台位消费"]["关台时间"] = None

        result = two_calculate_table_fee(flow_box)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "TableNotClosed")


class ProductAmountDecimalTests(unittest.TestCase):
    def test_product_amount_is_exact_decimal(self):
        flow_box = {
            "商品编号": "P_001",
            "商品信息": {
                "商品名称": "可乐",
                "单价": Decimal("19.99"),
                "库存": 10
            },
            "购买数量": 3
        }

        result = two_create_product_consumption(
            "1",
            flow_box,
            1700000000
        )

        self.assertEqual(result["status"], "success")
        consumption = result["data"]
        self.assertIsInstance(consumption["单价"], Decimal)
        self.assertIsInstance(consumption["金额"], Decimal)
        self.assertEqual(consumption["单价"], Decimal("19.99"))
        self.assertEqual(consumption["金额"], Decimal("59.97"))
        self.assertEqual(str(consumption["金额"]), "59.97")

    def test_cent_level_product_amount_keeps_two_decimals(self):
        flow_box = {
            "商品编号": "P_002",
            "商品信息": {
                "商品名称": "薯片",
                "单价": Decimal("0.10"),
                "库存": 10
            },
            "购买数量": 3
        }

        result = two_create_product_consumption(
            "1",
            flow_box,
            1700000000
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["金额"], Decimal("0.30"))
        self.assertEqual(str(result["data"]["金额"]), "0.30")


class OrderTotalDecimalTests(unittest.TestCase):
    def test_order_total_uses_decimal_sum(self):
        consumptions = [
            {
                "消费记录编号": "T_1_1700000000",
                "类型": "台位费",
                "每小时价格": Decimal("30.00"),
                "金额": Decimal("30.00")
            },
            {
                "消费记录编号": "S_1_1700000001",
                "类型": "商品",
                "单价": Decimal("0.10"),
                "金额": Decimal("0.10")
            },
            {
                "消费记录编号": "S_1_1700000002",
                "类型": "商品",
                "单价": Decimal("0.10"),
                "金额": Decimal("0.20")
            },
        ]

        result = two_create_unpaid_bill(
            "1",
            consumptions,
            1700000000
        )

        self.assertEqual(result["status"], "success")
        order_data = result["data"]
        self.assertIsInstance(order_data["总金额"], Decimal)
        self.assertEqual(order_data["总金额"], Decimal("30.30"))
        self.assertTrue(
            all(
                isinstance(item["金额"], Decimal)
                for item in order_data["已选消费"]
            )
        )

    def test_missing_amount_is_rejected(self):
        result = two_create_unpaid_bill(
            "1",
            [{"消费记录编号": "T_1_1700000000", "类型": "台位费"}],
            1700000000
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["error_type"],
            "SelectedConsumptionAmountMissing"
        )


class FrontendDecimalTests(unittest.TestCase):
    def test_zero_decimal_amount_is_still_selectable(self):
        table_consumption_box = {
            "待结算消费": {
                "T_1_1700000000": {
                    "消费记录编号": "T_1_1700000000",
                    "类型": "台位费",
                    "开台时间": 1700000000,
                    "关台时间": 1700003600,
                    "每小时价格": Decimal("30.00"),
                    "金额": Decimal("0.00"),
                }
            }
        }

        result = two_get_frontend_checkout_options(
            table_consumption_box,
            "1"
        )

        self.assertEqual(result["status"], "success")
        option = result["data"]["消费明细"][0]
        self.assertIsInstance(option["amount"], Decimal)
        self.assertEqual(option["amount"], Decimal("0.00"))
        self.assertTrue(option["selectable"])

    def test_unpaid_order_accepts_decimal_money_fields(self):
        all_unpaid_orders = {
            "1_1700000000": {
                "支付状态": "未支付",
                "总金额": Decimal("36.30"),
                "消费明细": [
                    {
                        "消费记录编号": "T_1_1700000000",
                        "类型": "台位费",
                        "开台时间": 1700000000,
                        "关台时间": 1700003600,
                        "每小时价格": Decimal("30.00"),
                        "金额": Decimal("30.00"),
                    },
                    {
                        "消费记录编号": "S_1_1700000001",
                        "类型": "商品",
                        "商品名称": "可乐",
                        "单价": Decimal("3.15"),
                        "数量": 2,
                        "金额": Decimal("6.30"),
                    },
                ],
            }
        }

        result = two_get_frontend_unpaid_orders_info(all_unpaid_orders)

        self.assertEqual(result["status"], "success")
        order = result["data"]["未支付订单"][0]
        self.assertIsInstance(order["total_amount"], Decimal)
        self.assertEqual(order["total_amount"], Decimal("36.30"))
        self.assertTrue(
            all(
                isinstance(detail["amount"], Decimal)
                for detail in order["consumption_details"]
            )
        )


class DecimalJsonTests(unittest.TestCase):
    def test_stdlib_json_cannot_serialize_decimal_directly(self):
        with self.assertRaises(TypeError):
            json.dumps({"金额": Decimal("59.97")})

    def test_api_serializer_converts_decimal_to_string(self):
        payload = {
            "status": "success",
            "data": {
                "金额": Decimal("59.97"),
                "明细": [
                    {"金额": Decimal("30.00")},
                    {"金额": None},
                ],
            },
        }

        safe_payload = decimal_to_json(payload)

        self.assertEqual(safe_payload["data"]["金额"], "59.97")
        self.assertEqual(safe_payload["data"]["明细"][0]["金额"], "30.00")
        self.assertIsNone(safe_payload["data"]["明细"][1]["金额"])
        self.assertEqual(
            json.loads(json.dumps(safe_payload, ensure_ascii=False)),
            safe_payload
        )


if __name__ == "__main__":
    unittest.main()
