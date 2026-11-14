# -*- coding: utf-8 -*-
"""加商品幂等链路真实 MySQL 测试（仅测试库，跑完清理测试数据）。

运行：
    $env:MYSQL_DATABASE = "billiards_hall_test"
    $env:ALLOW_WRITE_TESTS = "1"
    python tests/test_idempotency_mysql.py
"""

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pymysql


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


if os.environ.get("ALLOW_WRITE_TESTS") != "1":
    print(
        "错误：测试会真实写库，请先设置 ALLOW_WRITE_TESTS=1",
        file=sys.stderr,
    )
    sys.exit(1)


from backend.controller_layer import three_add_product_consumption
from backend.io_layer import DB_CONFIG
from backend.json_utils import decimal_to_json


if "test" not in str(DB_CONFIG.get("database", "")).lower():
    print(
        "错误：拒绝在非测试库运行，MYSQL_DATABASE 必须包含 test",
        file=sys.stderr,
    )
    sys.exit(1)


TABLE_ID = "99"
PRODUCT_ID = "P_001"
QUANTITY = 1


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


def cleanup(key_ids, consumption_ids, original_stock):
    conn = connection()
    try:
        with conn.cursor() as cursor:
            for consumption_id in consumption_ids:
                cursor.execute(
                    "DELETE FROM ongoing_product_consumptions "
                    "WHERE consumption_id = %s",
                    (consumption_id,),
                )
                cursor.execute(
                    "DELETE FROM ongoing_consumptions WHERE id = %s",
                    (consumption_id,),
                )

            for key_id in key_ids:
                cursor.execute(
                    "DELETE FROM idempotency_records "
                    "WHERE idempotency_key = %s",
                    (key_id,),
                )

            if consumption_ids:
                placeholders = ", ".join(["%s"] * len(consumption_ids))
                cursor.execute(
                    "DELETE FROM outbox_events "
                    "WHERE payload->>\"$.consumption_id\" "
                    f"IN ({placeholders})",
                    tuple(consumption_ids),
                )

            cursor.execute(
                "UPDATE products SET `库存` = %s WHERE id = %s",
                (original_stock, PRODUCT_ID),
            )
        conn.commit()
    finally:
        conn.close()


def run_once(idempotency_key):
    return three_add_product_consumption(
        TABLE_ID,
        PRODUCT_ID,
        QUANTITY,
        idempotency_key=idempotency_key,
    )


def main():
    key_ids = []
    consumption_ids = []
    original_stock = scalar(
        "SELECT `库存` FROM products WHERE id = %s",
        (PRODUCT_ID,),
    )

    try:
        sequential_key = f"test-sequential-{uuid.uuid4()}"
        concurrent_key = f"test-concurrent-{uuid.uuid4()}"
        key_ids.extend([sequential_key, concurrent_key])

        first = run_once(sequential_key)
        assert first["status"] == "success", first
        first_id = first["data"]["消费记录编号"]
        consumption_ids.append(first_id)

        stock_after_first = scalar(
            "SELECT `库存` FROM products WHERE id = %s",
            (PRODUCT_ID,),
        )
        assert stock_after_first == original_stock - QUANTITY, (
            stock_after_first,
            original_stock,
        )

        replay = run_once(sequential_key)
        assert replay["status"] == "success", replay
        assert replay["data"]["消费记录编号"] == first_id
        assert decimal_to_json(first) == replay
        assert scalar(
            "SELECT `库存` FROM products WHERE id = %s",
            (PRODUCT_ID,),
        ) == stock_after_first
        assert scalar(
            "SELECT COUNT(*) FROM ongoing_product_consumptions "
            "WHERE consumption_id = %s",
            (first_id,),
        ) == 1

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(run_once, concurrent_key)
                for _ in range(2)
            ]
            concurrent_results = [future.result() for future in futures]

        consumption_ids.extend(
            result["data"]["消费记录编号"]
            for result in concurrent_results
            if result.get("status") == "success"
        )
        assert all(
            result["status"] == "success"
            for result in concurrent_results
        ), concurrent_results

        concurrent_ids = {
            result["data"]["消费记录编号"]
            for result in concurrent_results
        }
        assert len(concurrent_ids) == 1, concurrent_ids
        concurrent_id = concurrent_ids.pop()

        assert decimal_to_json(concurrent_results[0]) == concurrent_results[1]
        assert scalar(
            "SELECT COUNT(*) FROM ongoing_product_consumptions "
            "WHERE consumption_id = %s",
            (concurrent_id,),
        ) == 1
        assert scalar(
            "SELECT COUNT(*) FROM ongoing_consumptions WHERE id = %s",
            (concurrent_id,),
        ) == 1
        assert scalar(
            "SELECT `库存` FROM products WHERE id = %s",
            (PRODUCT_ID,),
        ) == original_stock - QUANTITY * 2
        assert scalar(
            "SELECT COUNT(*) FROM idempotency_records "
            "WHERE idempotency_key IN (%s, %s) AND status = %s",
            (sequential_key, concurrent_key, "completed"),
        ) == 2

        # 只剩 1 件库存时并发双击：后到请求可能在业务预检阶段看到库存 0，
        # 此时 controller 的二次查防重表必须把首次成功结果回放出来。
        last_stock_key = f"test-last-stock-{uuid.uuid4()}"
        key_ids.append(last_stock_key)
        conn = connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE products SET `库存` = 1 WHERE id = %s",
                    (PRODUCT_ID,),
                )
            conn.commit()
        finally:
            conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(run_once, last_stock_key)
                for _ in range(2)
            ]
            last_stock_results = [future.result() for future in futures]

        consumption_ids.extend(
            result["data"]["消费记录编号"]
            for result in last_stock_results
            if result.get("status") == "success"
        )
        assert all(
            result["status"] == "success"
            for result in last_stock_results
        ), last_stock_results
        assert len({
            result["data"]["消费记录编号"]
            for result in last_stock_results
        }) == 1
        last_stock_id = last_stock_results[0]["data"]["消费记录编号"]
        assert scalar(
            "SELECT `库存` FROM products WHERE id = %s",
            (PRODUCT_ID,),
        ) == 0

        print("PASS: 顺序重试、并发双击、只剩 1 件库存三种场景都只执行一次")
        print(f"  顺序消费编号: {first_id}")
        print(f"  并发消费编号: {concurrent_id}")
        print(f"  末件库存消费编号: {last_stock_id}")
    finally:
        cleanup(key_ids, consumption_ids, original_stock)


if __name__ == "__main__":
    main()
