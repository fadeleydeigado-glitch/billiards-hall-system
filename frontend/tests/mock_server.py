"""
Small dependency-free mock backend for visual checks of the Vue frontend.

It is not used by the real application. Start it with:

    python frontend/tests/mock_server.py

Then open http://127.0.0.1:8765/.
"""

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


FRONTEND_DIR = Path(__file__).resolve().parents[1]


def now_seconds():
    return int(time.time())


def success(data, message):
    return {
        "status": "success",
        "error_type": None,
        "data": data,
        "message": message
    }


INITIAL_TIME = now_seconds()

INITIAL_TABLE_STATE = {
    "1": {
        "table_id": "1",
        "is_open": True,
        "start_time": INITIAL_TIME - 3725,
        "hour_price": 30
    },
    "2": {
        "table_id": "2",
        "is_open": False,
        "start_time": None,
        "hour_price": None
    },
    "3": {
        "table_id": "3",
        "is_open": True,
        "start_time": INITIAL_TIME - 483,
        "hour_price": 45
    },
    "4": {
        "table_id": "4",
        "is_open": False,
        "start_time": None,
        "hour_price": None
    }
}


def reset_table_state():
    TABLE_STATE.clear()
    TABLE_STATE.update({
        table_id: table.copy()
        for table_id, table in INITIAL_TABLE_STATE.items()
    })


TABLE_STATE = {}
reset_table_state()


def table_state():
    return {
        table_id: table.copy()
        for table_id, table in TABLE_STATE.items()
    }


PRODUCTS = {
    "P_001": {
        "product_id": "P_001",
        "product_name": "可乐",
        "price": 3,
        "stock": 18
    },
    "P_002": {
        "product_id": "P_002",
        "product_name": "薯片",
        "price": 5,
        "stock": 13
    }
}


UNPAID_ORDER = {
    "order_id": "2_1760000000",
    "table_id": "2",
    "created_at": now_seconds() - 900,
    "total_amount": 51,
    "consumption_details": [
        {
            "consumption_type": "台位费",
            "display_name": "台费",
            "amount": 45,
            "start_time": now_seconds() - 5440,
            "close_time": now_seconds() - 840,
            "hour_price": 45
        },
        {
            "consumption_type": "商品",
            "display_name": "可乐",
            "amount": 6,
            "unit_price": 3,
            "quantity": 2
        }
    ]
}


CHECKOUT_OPTIONS = [
    {
        "consumption_id": "T_DEMO_001",
        "consumption_type": "台位费",
        "display_name": "台费",
        "amount": 60,
        "selectable": True,
        "unavailable_message": None,
        "start_time": now_seconds() - 7400,
        "close_time": now_seconds() - 200
    },
    {
        "consumption_id": "S_DEMO_001",
        "consumption_type": "商品",
        "display_name": "可乐",
        "amount": 6,
        "selectable": True,
        "unavailable_message": None,
        "unit_price": 3,
        "quantity": 2
    }
]


class MockHandler(BaseHTTPRequestHandler):
    server_version = "BilliardsMock/1.0"

    def log_message(self, message, *args):
        print(message % args)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            content_type = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/preview", "/preview/"):
            self.send_file(FRONTEND_DIR / "index.html")
            return

        if parsed.path.startswith("/assets/"):
            relative_path = parsed.path.removeprefix("/assets/")
            self.send_file(FRONTEND_DIR / relative_path)
            return

        if parsed.path == "/frontend/refresh":
            self.send_json(success({
                "桌台基础信息": table_state(),
                "商品信息": PRODUCTS
            }, "模拟桌台与商品数据读取成功"))
            return

        if parsed.path == "/frontend/products":
            self.send_json(success(PRODUCTS, "模拟商品数据读取成功"))
            return

        if parsed.path == "/frontend/unpaid-orders":
            self.send_json(success({
                "未支付订单": [UNPAID_ORDER]
            }, "模拟未支付订单读取成功"))
            return

        if parsed.path == "/tables/checkout-options":
            self.send_json(success({
                "table_id": parse_qs(parsed.query).get("table_id", ["1"])[0],
                "消费明细": CHECKOUT_OPTIONS
            }, "模拟结账明细读取成功"))
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length) if content_length else b"{}"

        if parsed.path == "/__reset":
            reset_table_state()
            self.send_json(success(None, "模拟数据已重置"))
            return

        if parsed.path == "/orders/unpaid":
            json.loads(request_body.decode("utf-8"))
            self.send_json(success({
                "桌子号码": query.get("table_id", ["1"])[0],
                "订单编号": f"demo_{now_seconds()}",
                "总金额": 66,
                "创建时间": now_seconds(),
                "已选消费": []
            }, "模拟未支付订单已创建"))
            return

        if parsed.path == "/payments/confirm":
            json.loads(request_body.decode("utf-8"))
            self.send_json(success(None, "模拟收款成功"))
            return

        if parsed.path == "/tables/open":
            table_id = query.get("table_id", ["2"])[0]
            if table_id in TABLE_STATE:
                TABLE_STATE[table_id].update({
                    "is_open": True,
                    "start_time": now_seconds(),
                    "hour_price": 30
                })
            self.send_json(success(None, "模拟开台成功"))
            return

        if parsed.path == "/tables/close":
            table_id = query.get("table_id", ["1"])[0]
            if table_id in TABLE_STATE:
                TABLE_STATE[table_id].update({
                    "is_open": False,
                    "start_time": None,
                    "hour_price": None
                })
            self.send_json(success(None, "模拟关台成功"))
            return

        if parsed.path == "/products/consumption":
            self.send_json(success(None, "模拟操作成功"))
            return

        self.send_error(404)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), MockHandler)
    print("Mock frontend: http://127.0.0.1:8765/")
    server.serve_forever()


if __name__ == "__main__":
    main()
