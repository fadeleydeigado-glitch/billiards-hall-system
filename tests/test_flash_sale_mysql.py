# -*- coding: utf-8 -*-
"""热点抢购异步落库真实 MySQL 验证（仅测试库，结束后清理数据）。"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymysql


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend import controller_layer, flash_sale_service  # noqa: E402
from backend.flash_sale_queue import flash_sale_engine  # noqa: E402
from backend.io_layer import DB_CONFIG  # noqa: E402


def connection():
    return pymysql.connect(**DB_CONFIG)


def scalar(sql, params=()):
    conn = connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def cleanup(table_ids, product_id, idempotency_keys, order_ids=None):
    order_ids = list(order_ids or [])
    conn = connection()
    try:
        with conn.cursor() as cursor:
            if order_ids:
                order_placeholders = ", ".join(["%s"] * len(order_ids))
                for table_name in (
                    "order_consumptions",
                    "order_table_fees",
                    "order_product_consumptions",
                ):
                    cursor.execute(
                        f"DELETE FROM {table_name} "
                        f"WHERE order_id IN ({order_placeholders})",
                        tuple(order_ids),
                    )
                cursor.execute(
                    f"DELETE FROM orders WHERE order_id IN ({order_placeholders})",
                    tuple(order_ids),
                )

            for table_id in table_ids:
                cursor.execute(
                    "DELETE FROM outbox_events "
                    "WHERE payload->>\"$.table_id\" = %s",
                    (table_id,),
                )
                cursor.execute(
                    "DELETE FROM ongoing_product_consumptions "
                    "WHERE 桌台编号 = %s",
                    (table_id,),
                )
                cursor.execute(
                    "DELETE FROM ongoing_table_fees "
                    "WHERE consumption_id IN ("
                    "SELECT id FROM ongoing_consumptions WHERE 桌台编号 = %s"
                    ")",
                    (table_id,),
                )
                cursor.execute(
                    "DELETE FROM ongoing_consumptions WHERE 桌台编号 = %s",
                    (table_id,),
                )

            if idempotency_keys:
                placeholders = ", ".join(["%s"] * len(idempotency_keys))
                cursor.execute(
                    "DELETE FROM idempotency_records "
                    f"WHERE idempotency_key IN ({placeholders})",
                    tuple(idempotency_keys),
                )

            if product_id:
                cursor.execute(
                    "DELETE FROM products WHERE id = %s",
                    (product_id,),
                )

            for table_id in table_ids:
                cursor.execute(
                    "DELETE FROM billiards_tables WHERE id = %s",
                    (table_id,),
                )
        conn.commit()
    finally:
        conn.close()


def wait_for_status(ticket_id, expected, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ticket = flash_sale_engine.get_ticket(ticket_id)
        if (
            ticket["status"] == "success"
            and ticket["data"]["status"] == expected
        ):
            return ticket["data"]
        time.sleep(0.02)
    raise AssertionError(
        f"ticket {ticket_id} did not reach {expected}: "
        f"{flash_sale_engine.get_ticket(ticket_id)}"
    )


def main():
    if os.environ.get("ALLOW_WRITE_TESTS") != "1":
        print("跳过：需要设置 ALLOW_WRITE_TESTS=1")
        return 0

    database = str(DB_CONFIG.get("database", ""))
    if "test" not in database.lower():
        print(f"拒绝运行：当前数据库 {database!r} 不是测试库")
        return 2

    suffix = uuid.uuid4().hex[:6].upper()
    product_id = f"FSP{suffix}"[:10]
    product_table_id = f"FPB{suffix}"[:10]
    open_table_id = f"FPT{suffix}"[:10]
    table_ids = [product_table_id, open_table_id]
    idempotency_keys = set()
    order_ids = []

    flash_sale_engine.stop(drain=True, timeout=5)
    flash_sale_engine.reset()
    cleanup(table_ids, product_id, [], order_ids)

    conn = connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO billiards_tables (id, `每小时价格`) VALUES (%s, %s)",
                (product_table_id, 30),
            )
            cursor.execute(
                "INSERT INTO billiards_tables (id, `每小时价格`) VALUES (%s, %s)",
                (open_table_id, 30),
            )
            cursor.execute(
                "INSERT INTO products "
                "(id, `商品名称`, `单价`, `库存`) VALUES (%s, %s, %s, %s)",
                (product_id, "抢购测试商品", 3, 20),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        flash_sale_service.ensure_flash_sale_worker()

        # ---------- 1. 5 张商品牌，20 并发 ----------
        preheat = flash_sale_service.preheat_product(product_id, 5)
        assert preheat["status"] == "success", preheat

        barrier = threading.Barrier(20)

        def acquire_product(_index):
            barrier.wait()
            return flash_sale_service.acquire_product_ticket(
                product_table_id,
                product_id,
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            product_results = list(pool.map(acquire_product, range(20)))

        product_successes = [
            item for item in product_results
            if item["status"] == "success"
        ]
        product_sold_out = [
            item for item in product_results
            if item["error_type"] == "FlashSaleSoldOut"
        ]
        assert len(product_successes) == 5, product_results
        assert len(product_sold_out) == 15, product_results
        assert flash_sale_engine.wait_until_idle(timeout=10)

        product_ticket_ids = [
            item["data"]["ticket_id"] for item in product_successes
        ]
        idempotency_keys.update(
            f"flash:{ticket_id}" for ticket_id in product_ticket_ids
        )
        for ticket_id in product_ticket_ids:
            ticket = wait_for_status(ticket_id, "succeeded")
            assert ticket["attempts"] == 1, ticket

        stock = scalar(
            "SELECT `库存` FROM products WHERE id = %s",
            (product_id,),
        )
        product_rows = scalar(
            "SELECT COUNT(*) FROM ongoing_product_consumptions "
            "WHERE 桌台编号 = %s AND 商品编号 = %s",
            (product_table_id, product_id),
        )
        product_quantity = scalar(
            "SELECT COALESCE(SUM(`数量`), 0) "
            "FROM ongoing_product_consumptions "
            "WHERE 桌台编号 = %s AND 商品编号 = %s",
            (product_table_id, product_id),
        )
        idempotency_rows = scalar(
            "SELECT COUNT(*) FROM idempotency_records "
            "WHERE idempotency_key IN ("
            + ", ".join(["%s"] * len(idempotency_keys))
            + ") AND status = 'completed'",
            tuple(sorted(idempotency_keys)),
        )
        outbox_rows = scalar(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE payload->>\"$.table_id\" = %s",
            (product_table_id,),
        )
        assert stock == 15, stock
        assert product_rows == 5, product_rows
        assert product_quantity == 5, product_quantity
        assert idempotency_rows == 5, idempotency_rows
        assert outbox_rows == 5, outbox_rows
        print("PASS: 5 张商品牌被 20 并发精准发出，异步落库库存减 5")

        # ---------- 2. 1 张桌台牌，20 并发 ----------
        preheat_tables = flash_sale_service.preheat_tables([open_table_id])
        assert preheat_tables["status"] == "success", preheat_tables

        table_barrier = threading.Barrier(20)

        def acquire_table(_index):
            table_barrier.wait()
            return flash_sale_service.acquire_table_ticket(open_table_id)

        with ThreadPoolExecutor(max_workers=20) as pool:
            table_results = list(pool.map(acquire_table, range(20)))

        table_successes = [
            item for item in table_results
            if item["status"] == "success"
        ]
        table_occupied = [
            item for item in table_results
            if item["error_type"] == "TableOccupied"
        ]
        assert len(table_successes) == 1, table_results
        assert len(table_occupied) == 19, table_results
        assert flash_sale_engine.wait_until_idle(timeout=10)

        table_ticket_id = table_successes[0]["data"]["ticket_id"]
        wait_for_status(table_ticket_id, "succeeded")
        open_count = scalar(
            "SELECT COUNT(*) FROM ongoing_table_fees "
            "WHERE 桌台编号 = %s AND `关台时间` IS NULL",
            (open_table_id,),
        )
        assert open_count == 1, open_count
        print("PASS: 1 张桌台牌被 20 并发精准发出，异步开台成功")

        # ---------- 3. 老路关台后自动补牌 ----------
        close_result = controller_layer.three_close_table(
            open_table_id,
            source="flash-test",
        )
        assert close_result["status"] == "success", close_result
        pool_status = flash_sale_engine.get_pool_status()["data"]["tables"]
        assert open_table_id in pool_status["available"], pool_status

        closed_fee_id = scalar(
            "SELECT consumption_id FROM ongoing_table_fees "
            "WHERE 桌台编号 = %s AND `关台时间` IS NOT NULL "
            "ORDER BY `开台时间` DESC LIMIT 1",
            (open_table_id,),
        )
        assert closed_fee_id, "关台后没有找到待结算台费"

        order_result = controller_layer.three_create_unpaid_order({
            "桌子号码": open_table_id,
            "消费记录编号列表": [closed_fee_id],
        })
        assert order_result["status"] == "success", order_result
        order_id = order_result["data"]["订单编号"]
        order_ids.append(order_id)

        payment_result = controller_layer.three_confirm_payment({
            "订单编号": order_id,
        })
        assert payment_result["status"] == "success", payment_result

        second_acquire = flash_sale_service.acquire_table_ticket(open_table_id)
        assert second_acquire["status"] == "success", second_acquire
        second_ticket_id = second_acquire["data"]["ticket_id"]
        assert flash_sale_engine.wait_until_idle(timeout=10)
        wait_for_status(second_ticket_id, "succeeded")

        second_open_count = scalar(
            "SELECT COUNT(*) FROM ongoing_table_fees "
            "WHERE 桌台编号 = %s AND `关台时间` IS NULL",
            (open_table_id,),
        )
        assert second_open_count == 1, second_open_count
        print("PASS: 老路关台提交后，桌台发牌资格自动恢复并可再次抢到")

        controller_layer.three_close_table(
            open_table_id,
            source="flash-test-cleanup",
        )
    finally:
        flash_sale_engine.stop(drain=True, timeout=10)
        flash_sale_engine.reset()
        cleanup(
            table_ids,
            product_id,
            sorted(idempotency_keys),
            order_ids,
        )

    print("全部热点抢购真实库测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
