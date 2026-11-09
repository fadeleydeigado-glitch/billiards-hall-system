from pathlib import Path

from fastapi import FastAPI
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
from .json_utils import decimal_to_json


app = FastAPI()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.get("/")
def frontend_page():
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store"}
    )


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
    return JSONResponse(content=decimal_to_json(result))


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
def api_get_frontend_checkout_options(table_id: str):
    # 对应第三层函数 three_get_frontend_checkout_options。
    return _json_response(three_get_frontend_checkout_options(table_id))


@app.post("/tables/open")
def api_open_table(table_id: str):
    # 对应第三层函数 three_open_table。
    return _json_response(three_open_table(table_id))


@app.post("/tables/close")
def api_close_table(table_id: str):
    # 对应第三层函数 three_close_table。
    return _json_response(three_close_table(table_id))


@app.post("/orders/unpaid")
def api_create_unpaid_order(selected_data: dict):
    # 对应第三层函数 three_create_unpaid_order。
    return _json_response(three_create_unpaid_order(selected_data))


@app.post("/payments/confirm")
def api_confirm_payment(payment_data: dict):
    # 对应第三层函数 three_confirm_payment。
    return _json_response(three_confirm_payment(payment_data))


@app.post("/products/consumption")
def api_add_product_consumption(
    table_id: str,
    product_id: str,
    product_count: int,
):
    # 对应第三层函数 three_add_product_consumption。
    return _json_response(
        three_add_product_consumption(table_id, product_id, product_count)
    )
