from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .controller_layer import (
    three_get_frontend_refresh_data,
    three_get_frontend_products_data,
    three_get_frontend_unpaid_orders_data,
    three_get_frontend_checkout_options,
    three_open_table,
    three_close_table,
    three_create_unpaid_order,
    three_confirm_payment,
    three_add_product_consumption,
)
from .errors import build_validation_error_payload, status_code_for_result
from .json_utils import decimal_to_json
from .schemas import (
    ConfirmPaymentRequest,
    CreateUnpaidOrderRequest,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    ProductId,
    TableId,
)


app = FastAPI()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request, exc):
    # FastAPI 默认的 422 响应不是四键结构；这里统一成项目约定。
    # JSON 本身语法错误按 400 返回，JSON 能解析但字段不合格按 422 返回。
    errors = exc.errors()
    is_bad_json = any(
        error.get("type") == "json_invalid"
        for error in errors
    )
    return JSONResponse(
        status_code=400 if is_bad_json else 422,
        content=build_validation_error_payload(
            errors,
            is_bad_json=is_bad_json,
        ),
    )


@app.get("/")
def frontend_page():
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store"}
    )


@app.get("/health")
def health():
    return {
        "status": "success",
        "error_type": None,
        "data": {"status": "ok"},
        "message": "服务正常",
    }


@app.get("/preview")
@app.get("/preview/")
def preview_frontend_page():
    # 保留旧地址，兼容已有书签；Vue 前端现在已经是正式首页。
    return RedirectResponse(url="/", status_code=307)


if FRONTEND_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="frontend-assets"
    )


def _json_response(result: dict) -> JSONResponse:
    # 金额在业务层统一是 Decimal；HTTP 边界显式转成字符串，
    # 避免 json.dumps 直接序列化 Decimal 时报错或偷偷变成 float。
    return JSONResponse(
        content=decimal_to_json(result),
        status_code=status_code_for_result(result),
    )


@app.get("/frontend/refresh")
def api_get_frontend_refresh_data():
    # 对应第三层函数 three_get_frontend_refresh_data。
    return _json_response(three_get_frontend_refresh_data())


@app.get("/frontend/products")
def api_get_frontend_products_data():
    # 对应第三层函数 three_get_frontend_products_data。
    return _json_response(three_get_frontend_products_data())


@app.get("/frontend/unpaid-orders")
def api_get_frontend_unpaid_orders_data():
    # 对应第三层函数 three_get_frontend_unpaid_orders_data。
    return _json_response(three_get_frontend_unpaid_orders_data())


@app.get("/tables/checkout-options")
def api_get_frontend_checkout_options(table_id: TableId):
    # 对应第三层函数 three_get_frontend_checkout_options。
    return _json_response(three_get_frontend_checkout_options(table_id))


@app.post("/tables/open")
def api_open_table(
    table_id: TableId,
    confirm_unsettled: bool = Query(False),
):
    # 对应第三层函数 three_open_table。
    return _json_response(three_open_table(
        table_id,
        confirm_unsettled=confirm_unsettled
    ))


@app.post("/tables/close")
def api_close_table(table_id: TableId):
    # 对应第三层函数 three_close_table。
    return _json_response(three_close_table(table_id))


@app.post("/orders/unpaid")
def api_create_unpaid_order(payload: CreateUnpaidOrderRequest):
    # 对应第三层函数 three_create_unpaid_order。
    return _json_response(
        three_create_unpaid_order(payload.model_dump(by_alias=True))
    )


@app.post("/payments/confirm")
def api_confirm_payment(payload: ConfirmPaymentRequest):
    # 对应第三层函数 three_confirm_payment。
    return _json_response(
        three_confirm_payment(payload.model_dump(by_alias=True))
    )


@app.post("/products/consumption")
def api_add_product_consumption(
    table_id: TableId,
    product_id: ProductId,
    product_count: Annotated[int, Query(gt=0)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key")
    ] = None,
):
    # 对应第三层函数 three_add_product_consumption。
    # 前端每次提交必须带 Idempotency-Key；缺失或空白时在这里直接拦截。
    if idempotency_key is None:
        return _json_response({
            "status": "error",
            "error_type": "IdempotencyKeyRequired",
            "data": None,
            "message": "缺少 Idempotency-Key 请求头"
        })

    normalized_key = idempotency_key.strip()

    if not normalized_key:
        return _json_response({
            "status": "error",
            "error_type": "IdempotencyKeyRequired",
            "data": None,
            "message": "Idempotency-Key 不能为空"
        })

    if len(normalized_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return _json_response({
            "status": "error",
            "error_type": "IdempotencyKeyFormatError",
            "data": None,
            "message": f"Idempotency-Key 长度不能超过 {MAX_IDEMPOTENCY_KEY_LENGTH}"
        })

    return _json_response(
        three_add_product_consumption(
            table_id,
            product_id,
            product_count,
            idempotency_key=normalized_key
        )
    )
