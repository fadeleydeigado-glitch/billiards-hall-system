# -*- coding: utf-8 -*-
"""Pydantic 请求模型测试；不连接数据库。"""

import unittest

from pydantic import ValidationError

from backend.schemas import (
    ConfirmPaymentRequest,
    CreateUnpaidOrderRequest,
)


def error_types(error: ValidationError) -> set:
    return {item["type"] for item in error.errors()}


class CreateUnpaidOrderRequestTests(unittest.TestCase):
    def test_chinese_aliases_are_normalized_for_controller(self):
        request = CreateUnpaidOrderRequest.model_validate({
            "桌子号码": " 3 ",
            "消费记录编号列表": [
                " T_3_tmv7ggbkz_abc123 ",
                "S_3_tmv7ggbkz_def456",
            ],
        })

        self.assertEqual(
            request.model_dump(by_alias=True),
            {
                "桌子号码": "3",
                "消费记录编号列表": [
                    "T_3_tmv7ggbkz_abc123",
                    "S_3_tmv7ggbkz_def456",
                ],
            },
        )
        self.assertEqual(
            request.model_dump(),
            {
                "table_id": "3",
                "consumption_ids": [
                    "T_3_tmv7ggbkz_abc123",
                    "S_3_tmv7ggbkz_def456",
                ],
            },
        )

    def test_english_names_are_accepted_and_normalized_to_chinese(self):
        request = CreateUnpaidOrderRequest.model_validate({
            "table_id": "4",
            "consumption_ids": ["T_4_tmv7ggbkz_abc123"],
        })

        self.assertEqual(
            request.model_dump(by_alias=True),
            {
                "桌子号码": "4",
                "消费记录编号列表": ["T_4_tmv7ggbkz_abc123"],
            },
        )

    def test_json_body_uses_the_same_alias_rules(self):
        request = CreateUnpaidOrderRequest.model_validate_json(
            '{"桌子号码":"3","消费记录编号列表":'
            '["T_3_tmv7ggbkz_abc123"]}'
        )

        self.assertEqual(request.table_id, "3")
        self.assertEqual(
            request.consumption_ids,
            ["T_3_tmv7ggbkz_abc123"],
        )

    def test_empty_or_whitespace_table_id_is_rejected(self):
        for table_id in ("", "   "):
            with self.assertRaises(ValidationError) as raised:
                CreateUnpaidOrderRequest.model_validate({
                    "桌子号码": table_id,
                    "消费记录编号列表": ["T_3_tmv7ggbkz_abc123"],
                })

            self.assertIn("string_too_short", error_types(raised.exception))

    def test_non_string_table_id_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": 3,
                "消费记录编号列表": ["T_3_tmv7ggbkz_abc123"],
            })

        self.assertIn("string_type", error_types(raised.exception))

    def test_missing_required_field_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "3",
            })

        self.assertIn("missing", error_types(raised.exception))

    def test_empty_consumption_list_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "3",
                "消费记录编号列表": [],
            })

        self.assertIn("too_short", error_types(raised.exception))

    def test_non_string_consumption_id_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "3",
                "消费记录编号列表": ["T_3_tmv7ggbkz_abc123", 123],
            })

        errors = raised.exception.errors()
        self.assertEqual(errors[0]["type"], "string_type")
        self.assertEqual(errors[0]["loc"], ("消费记录编号列表", 1))

    def test_extra_field_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "3",
                "消费记录编号列表": ["T_3_tmv7ggbkz_abc123"],
                "备注": "多传字段",
            })

        self.assertIn("extra_forbidden", error_types(raised.exception))

    def test_column_length_limits_are_enforced(self):
        with self.assertRaises(ValidationError) as table_error:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "12345678901",
                "消费记录编号列表": ["T_3_tmv7ggbkz_abc123"],
            })
        self.assertIn("string_too_long", error_types(table_error.exception))

        with self.assertRaises(ValidationError) as id_error:
            CreateUnpaidOrderRequest.model_validate({
                "桌子号码": "3",
                "消费记录编号列表": ["T" * 31],
            })
        self.assertIn("string_too_long", error_types(id_error.exception))


class ConfirmPaymentRequestTests(unittest.TestCase):
    def test_chinese_order_id_is_normalized(self):
        request = ConfirmPaymentRequest.model_validate({
            "订单编号": " 1_tmv7ggbkz_v4bq51 ",
        })

        self.assertEqual(
            request.model_dump(by_alias=True),
            {"订单编号": "1_tmv7ggbkz_v4bq51"},
        )

    def test_english_order_id_is_accepted(self):
        request = ConfirmPaymentRequest.model_validate({
            "order_id": "1_tmv7ggbkz_v4bq51",
        })

        self.assertEqual(request.order_id, "1_tmv7ggbkz_v4bq51")

    def test_empty_order_id_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            ConfirmPaymentRequest.model_validate({"订单编号": "   "})

        self.assertIn("string_too_short", error_types(raised.exception))

    def test_missing_order_id_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            ConfirmPaymentRequest.model_validate({})

        self.assertIn("missing", error_types(raised.exception))

    def test_overlong_order_id_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            ConfirmPaymentRequest.model_validate({"订单编号": "1" * 31})

        self.assertIn("string_too_long", error_types(raised.exception))


if __name__ == "__main__":
    unittest.main()
