# -*- coding: utf-8 -*-
"""HTTP 状态码映射和校验错误响应测试；不连接数据库。"""

import unittest

from backend.errors import (
    build_validation_error_payload,
    status_code_for_result,
)


class ErrorStatusTests(unittest.TestCase):
    def test_success_is_200(self):
        self.assertEqual(
            status_code_for_result({
                "status": "success",
                "error_type": None,
            }),
            200,
        )

    def test_not_found_errors_are_404(self):
        for error_type in (
            "TableNotFound",
            "ProductNotFound",
            "OrderNotFound",
            "ConsumptionNotFound",
        ):
            self.assertEqual(
                status_code_for_result({
                    "status": "error",
                    "error_type": error_type,
                }),
                404,
            )

    def test_state_conflicts_are_409(self):
        for error_type in (
            "TableOccupied",
            "OrderNotUnpaid",
            "ProductStockNotEnough",
            "TableFeeAlreadyClosed",
        ):
            self.assertEqual(
                status_code_for_result({
                    "status": "error",
                    "error_type": error_type,
                }),
                409,
            )

    def test_controller_fallback_validation_errors_are_422(self):
        for error_type in (
            "TableIdFormatError",
            "SelectedConsumptionIdListMissing",
            "PaymentOrderIdFormatError",
            "ProductCountInvalid",
        ):
            self.assertEqual(
                status_code_for_result({
                    "status": "error",
                    "error_type": error_type,
                }),
                422,
            )

    def test_database_errors_default_to_500(self):
        for error_type in (
            "OperationalError",
            "InterfaceError",
            "ProgrammingError",
            "IntegrityError",
        ):
            self.assertEqual(
                status_code_for_result({
                    "status": "error",
                    "error_type": error_type,
                }),
                500,
            )

    def test_unknown_and_server_errors_are_500(self):
        for error_type in (
            "UnknownConsumptionType",
            "OrderIdFormatError",
            "SomeNewServerError",
        ):
            self.assertEqual(
                status_code_for_result({
                    "status": "error",
                    "error_type": error_type,
                }),
                500,
            )

        self.assertEqual(status_code_for_result(None), 500)


class ValidationErrorPayloadTests(unittest.TestCase):
    def test_payload_has_four_keys_and_sanitized_details(self):
        payload = build_validation_error_payload([
            {
                "loc": ("body", "消费记录编号列表", 1),
                "msg": "Input should be a valid string",
                "type": "string_type",
                "ctx": {"error": ValueError("不要泄漏这个对象")},
                "input": 123,
            },
        ])

        self.assertEqual(
            set(payload),
            {"status", "error_type", "data", "message"},
        )
        self.assertEqual(payload["status"], "error")
        self.assertEqual(
            payload["error_type"],
            "RequestValidationError",
        )
        self.assertEqual(
            payload["data"]["errors"],
            [
                {
                    "loc": ["body", "消费记录编号列表", 1],
                    "msg": "Input should be a valid string",
                    "type": "string_type",
                }
            ],
        )
        self.assertIn("消费记录编号列表", payload["message"])
        self.assertIn("第 2 项", payload["message"])
        self.assertIn("必须是字符串", payload["message"])

    def test_bad_json_uses_a_more_specific_message(self):
        payload = build_validation_error_payload(
            [
                {
                    "loc": ("body", 0),
                    "msg": "Expecting value",
                    "type": "json_invalid",
                }
            ],
            is_bad_json=True,
        )

        self.assertEqual(payload["message"], "请求体不是合法 JSON")
        self.assertEqual(payload["data"]["errors"][0]["type"], "json_invalid")


if __name__ == "__main__":
    unittest.main()
