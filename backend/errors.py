# -*- coding: utf-8 -*-
"""HTTP 状态码映射与统一错误响应构造。"""


VALIDATION_REASON_MESSAGES = {
    "missing": "缺少必填字段",
    "string_type": "必须是字符串",
    "string_too_short": "不能为空",
    "string_too_long": "长度超出限制",
    "list_type": "必须是列表",
    "too_short": "长度不足",
    "int_parsing": "必须是整数",
    "int_type": "必须是整数",
    "greater_than": "必须大于 0",
    "extra_forbidden": "包含不支持的字段",
    "model_attributes_type": "请求体必须是 JSON 对象",
}


# 只把“用户可以通过改请求或改操作解决”的错误映射成 4xx。
# 数据库异常、服务端数据格式错误、编号生成错误等默认 500。
ERROR_STATUS = {
    # 404：请求的资源不存在。
    "TableNotFound": 404,
    "ProductNotFound": 404,
    "OrderNotFound": 404,
    "ConsumptionNotFound": 404,
    "TableFeeNotFound": 404,
    "SelectedConsumptionNotFound": 404,
    "OpenTableConsumptionNotFound": 404,

    # 409：资源存在，但当前状态不允许这个操作。
    "TableOccupied": 409,
    "TableHasUnsettledConsumptions": 409,
    "TableNotClosed": 409,
    "TableFeeAlreadyClosed": 409,
    "OrderNotUnpaid": 409,
    "ProductStockNotEnough": 409,
    "ConsumptionAlreadyInUnpaidOrder": 409,
    "SelectedConsumptionAmountNotReady": 409,
    "IdempotencyKeyReused": 409,
    "IdempotencyInProgress": 409,

    # 422：请求形状错误；Pydantic 已挡住大部分，controller 仍保留兜底检查。
    "TableIdFormatError": 422,
    "TableIdEmptyError": 422,
    "SelectedDataFormatError": 422,
    "SelectedTableIdMissing": 422,
    "SelectedTableIdFormatError": 422,
    "SelectedConsumptionIdListMissing": 422,
    "SelectedConsumptionIdListFormatError": 422,
    "SelectedConsumptionIdFormatError": 422,
    "SelectedConsumptionIdDuplicate": 422,
    "PaymentDataFormatError": 422,
    "PaymentOrderIdMissing": 422,
    "PaymentOrderIdFormatError": 422,
    "ProductIdFormatError": 422,
    "ProductCountInvalid": 422,
    "ProductCountFormatError": 422,
    "IdempotencyKeyRequired": 422,
    "IdempotencyKeyFormatError": 422,

    # 500 条目显式列出，方便排查服务端问题；未列出的 error_type 也默认 500。
    "TableFeeIdFormatError": 500,
    "OrderIdFormatError": 500,
    "ProductConsumptionIdFormatError": 500,
    "UnpaidOrderTableIdFormatError": 500,
    "UnpaidOrderCreateTimeFormatError": 500,
    "SelectedConsumptionAmountMissing": 500,
    "SelectedConsumptionAmountFormatError": 500,
    "TableFeeEndTimeMissing": 500,
    "UnknownConsumptionType": 500,
    "IdempotencyRecordCorrupted": 500,
    "IdempotencyWriteFailed": 500,
}


def status_code_for_result(result: dict) -> int:
    """根据业务层返回的四键结果决定 HTTP 状态码。"""
    if not isinstance(result, dict):
        return 500
    if result.get("status") == "success":
        return 200
    return ERROR_STATUS.get(result.get("error_type"), 500)


def build_validation_error_payload(errors, is_bad_json: bool = False) -> dict:
    """把 FastAPI/Pydantic 的校验错误整理成四键结构。

    只保留 loc / msg / type，不把 ctx 里的异常对象或用户原始输入
    直接返回给客户端，避免序列化失败和信息泄漏。
    """
    details = []

    for error in errors or []:
        if not isinstance(error, dict):
            continue

        location = error.get("loc", ())
        if not isinstance(location, (list, tuple)):
            location = [location]

        details.append({
            "loc": list(location),
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        })

    message = (
        "请求体不是合法 JSON"
        if is_bad_json
        else _validation_message(details)
    )

    return {
        "status": "error",
        "error_type": "RequestValidationError",
        "data": {"errors": details},
        "message": message,
    }


def _validation_message(details: list[dict]) -> str:
    """把第一条字段错误整理成前端可直接展示的中文提示。"""
    if not details:
        return "请求参数格式错误"

    first = details[0]
    location = _format_validation_location(first.get("loc", []))
    reason = VALIDATION_REASON_MESSAGES.get(
        first.get("type"),
        first.get("msg") or "格式不正确",
    )

    if location:
        return f"请求参数格式错误：{location} {reason}"
    return f"请求参数格式错误：{reason}"


def _format_validation_location(location: list) -> str:
    """把 ["body", "消费记录编号列表", 1] 整理成可读字段路径。"""
    parts = [
        str(part)
        for part in location
        if str(part) not in ("body", "query", "path")
    ]
    if not parts:
        return ""

    formatted = []
    for part in parts:
        if part.isdigit():
            formatted.append(f"第 {int(part) + 1} 项")
        else:
            formatted.append(part)
    return " / ".join(formatted)
