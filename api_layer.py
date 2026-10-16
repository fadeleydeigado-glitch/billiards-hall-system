from fastapi import FastAPI
from fastapi.responses import FileResponse

from controller_layer import (
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


app = FastAPI()


@app.get("/")
def frontend_page():
    return FileResponse("frontend/index.html")


@app.get("/frontend/refresh")
def api_get_frontend_refresh_data():
    # 对应第三层函数 three_get_frontend_refresh_data。
    return three_get_frontend_refresh_data()


@app.get("/frontend/products")
def api_get_frontend_products_data():
    # 对应第三层函数 three_get_frontend_products_data。
    return three_get_frontend_products_data()


@app.get("/frontend/unpaid-orders")
def api_get_frontend_unpaid_orders_data():
    # 对应第三层函数 three_get_frontend_unpaid_orders_data。
    return three_get_frontend_unpaid_orders_data()


@app.get("/tables/checkout-options")
def api_get_frontend_checkout_options(table_id: str):
    # 对应第三层函数 three_get_frontend_checkout_options。
    return three_get_frontend_checkout_options(table_id)


@app.post("/tables/open")
def api_open_table(table_id: str):
    # 对应第三层函数 three_open_table。
    return three_open_table(table_id)


@app.post("/tables/close")
def api_close_table(table_id: str):
    # 对应第三层函数 three_close_table。
    return three_close_table(table_id)


@app.post("/orders/unpaid")
def api_create_unpaid_order(selected_data: dict):
    # 对应第三层函数 three_create_unpaid_order。
    return three_create_unpaid_order(selected_data)


@app.post("/payments/confirm")
def api_confirm_payment(payment_data: dict):
    # 对应第三层函数 three_confirm_payment。
    return three_confirm_payment(payment_data)


@app.post("/products/consumption")
def api_add_product_consumption(
    table_id: str,
    product_id: str,
    product_count: int,
):
    # 对应第三层函数 three_add_product_consumption。
    return three_add_product_consumption(table_id, product_id, product_count)
