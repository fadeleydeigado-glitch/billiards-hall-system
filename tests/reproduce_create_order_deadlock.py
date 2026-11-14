# -*- coding: utf-8 -*-
"""
复现创建订单时的 ABBA 死锁。

场景：
    两个并发请求都结账同一组两条待结算商品消费：
        请求 1：先 A 后 B
        请求 2：先 B 后 A

脚本直接调用生产代码里的 three_create_unpaid_order()，只在当前进程内用
包装函数控制并发时序，不改 controller_layer.py / io_layer.py：
    1. 两边完成事务前检查后，在开事务前对齐；
    2. 两边各完成第一次 DELETE、持有第一行锁后，再同时继续；
    3. 第二次 DELETE 形成 A->B / B->A 的循环等待，由 MySQL 返回 1213。

安全限制：
    - 默认使用 billiards_hall_test；
    - MYSQL_DATABASE 名称不含 test 时拒绝运行；
    - 测试前后都会清理本次专用桌台和消费编号的数据。

运行：
    python tests/reproduce_create_order_deadlock.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# 必须在导入 backend.io_layer 之前确定测试库，避免误连正式库。
TEST_DATABASE = os.environ.get("MYSQL_DATABASE", "billiards_hall_test")
if "test" not in TEST_DATABASE.lower():
    print(
        f"拒绝运行：当前 MYSQL_DATABASE={TEST_DATABASE!r}，名称必须包含 test。",
        file=sys.stderr,
    )
    raise SystemExit(2)
os.environ["MYSQL_DATABASE"] = TEST_DATABASE


import pymysql

from backend import controller_layer, io_layer


DEADLOCK_ERRNO = 1213
BARRIER_TIMEOUT_SECONDS = 10
WORKER_TIMEOUT_SECONDS = 25

RUN_TAG = uuid.uuid4().hex[:8].upper()
TABLE_ID = f"DLK{uuid.uuid4().hex[:6].upper()}"
CONSUMPTION_A = f"S_DLK_A_{RUN_TAG}"
CONSUMPTION_B = f"S_DLK_B_{RUN_TAG}"
PRODUCT_ID = "P_DLK"
SELECTED_ORDERS = {
    "请求 1 [A, B]": [CONSUMPTION_A, CONSUMPTION_B],
    "请求 2 [B, A]": [CONSUMPTION_B, CONSUMPTION_A],
}


def open_connection():
    return pymysql.connect(**io_layer.DB_CONFIG)


def delete_fixture_rows(cursor, table_id: str, consumption_ids: list[str]):
    """按外键依赖顺序删掉指定测试桌台的所有可能残留数据。"""
    cursor.execute(
        "SELECT order_id FROM orders WHERE `桌台编号` = %s",
        (table_id,),
    )
    order_ids = [row[0] for row in cursor.fetchall()]

    if order_ids:
        placeholders = ", ".join(["%s"] * len(order_ids))
        for table in (
            "order_product_consumptions",
            "order_table_fees",
            "order_consumptions",
        ):
            cursor.execute(
                f"DELETE FROM {table} WHERE order_id IN ({placeholders})",
                tuple(order_ids),
            )
        cursor.execute(
            f"DELETE FROM orders WHERE order_id IN ({placeholders})",
            tuple(order_ids),
        )

    if consumption_ids:
        placeholders = ", ".join(["%s"] * len(consumption_ids))
        cursor.execute(
            f"DELETE FROM ongoing_product_consumptions "
            f"WHERE consumption_id IN ({placeholders})",
            tuple(consumption_ids),
        )
        cursor.execute(
            f"DELETE FROM ongoing_table_fees "
            f"WHERE consumption_id IN ({placeholders})",
            tuple(consumption_ids),
        )
        cursor.execute(
            f"DELETE FROM ongoing_consumptions "
            f"WHERE id IN ({placeholders})",
            tuple(consumption_ids),
        )

    cursor.execute(
        "DELETE FROM billiards_tables WHERE id = %s",
        (table_id,),
    )


def cleanup():
    connection = open_connection()
    try:
        with connection.cursor() as cursor:
            delete_fixture_rows(
                cursor,
                TABLE_ID,
                [CONSUMPTION_A, CONSUMPTION_B],
            )
        connection.commit()
    finally:
        connection.close()


def seed_fixture():
    now = int(time.time())
    connection = open_connection()
    try:
        with connection.cursor() as cursor:
            # 先清掉同一名字的意外残留；TABLE_ID 本身是本次随机生成的。
            delete_fixture_rows(
                cursor,
                TABLE_ID,
                [CONSUMPTION_A, CONSUMPTION_B],
            )

            cursor.execute(
                "INSERT INTO billiards_tables (id, `每小时价格`) VALUES (%s, %s)",
                (TABLE_ID, "30.00"),
            )

            for consumption_id, product_name in (
                (CONSUMPTION_A, "死锁测试商品 A"),
                (CONSUMPTION_B, "死锁测试商品 B"),
            ):
                cursor.execute(
                    "INSERT INTO ongoing_consumptions "
                    "(id, `桌台编号`, type_code, `创建时间`) "
                    "VALUES (%s, %s, %s, %s)",
                    (consumption_id, TABLE_ID, "S", now),
                )
                cursor.execute(
                    "INSERT INTO ongoing_product_consumptions "
                    "(consumption_id, `桌台编号`, `商品编号`, `商品名称`, "
                    "`单价`, `数量`, `金额`) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        consumption_id,
                        TABLE_ID,
                        PRODUCT_ID,
                        product_name,
                        "1.00",
                        1,
                        "1.00",
                    ),
                )
        connection.commit()
    finally:
        connection.close()


class ConcurrencyProbe:
    """
    只用于测试的内存探针。

    第一个屏障拦在真实事务开启之前，保证两个请求都通过了事务外预检查。
    第二个屏障拦在各自第一次 DELETE 成功之后，保证双方都持有一把行锁。
    """

    def __init__(self):
        self.before_transaction = threading.Barrier(2)
        self.after_first_delete = threading.Barrier(2)
        self.local = threading.local()
        self.events: list[str] = []
        self.events_lock = threading.Lock()
        self.original_execute_in_transaction = (
            controller_layer.one_execute_in_transaction
        )
        self.original_delete_ongoing_consumption = (
            controller_layer.one_delete_ongoing_consumption_from_mysql
        )

    def record(self, message: str):
        line = f"{threading.current_thread().name}: {message}"
        with self.events_lock:
            self.events.append(line)
        print(f"[时序] {line}")

    def wrapped_execute_in_transaction(self, operation_function):
        self.local.delete_count = 0
        self.record("预检查完成，准备开启事务")
        self.before_transaction.wait(timeout=BARRIER_TIMEOUT_SECONDS)
        self.record("另一个请求也已就绪，开始事务")
        return self.original_execute_in_transaction(operation_function)

    def wrapped_delete(self, cursor, consumption_id):
        self.local.delete_count += 1
        call_number = self.local.delete_count

        result = self.original_delete_ongoing_consumption(cursor, consumption_id)
        self.record(
            f"第 {call_number} 次 DELETE {consumption_id} -> "
            f"{result.get('status')}"
        )

        if call_number == 1 and result.get("status") == "success":
            self.record("已持有第一把行锁，等待另一个请求也拿到第一把锁")
            self.after_first_delete.wait(timeout=BARRIER_TIMEOUT_SECONDS)
            self.record("双方都已持有第一把锁，继续第二次 DELETE")

        return result


def run_request(label: str, consumption_ids: list[str]) -> dict:
    started_at = time.perf_counter()
    try:
        result = controller_layer.three_create_unpaid_order(
            {
                "桌子号码": TABLE_ID,
                "消费记录编号列表": consumption_ids,
            },
            source="并发死锁复现",
        )
    except Exception as error:
        result = {
            "status": "exception",
            "error_type": type(error).__name__,
            "data": None,
            "message": str(error),
        }

    return {
        "label": label,
        "consumption_ids": consumption_ids,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
        "result": result,
    }


def is_deadlock_result(result: dict) -> bool:
    message = str(result.get("message", ""))
    return str(DEADLOCK_ERRNO) in message or "Deadlock found" in message


def print_post_state():
    connection = open_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM orders WHERE `桌台编号` = %s",
                (TABLE_ID,),
            )
            order_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM ongoing_consumptions "
                "WHERE id IN (%s, %s)",
                (CONSUMPTION_A, CONSUMPTION_B),
            )
            ongoing_count = cursor.fetchone()[0]
        print()
        print("事务结束后的测试数据状态：")
        print(f"  orders 中本桌台订单数：{order_count}")
        print(f"  ongoing_consumptions 中 A/B 剩余数：{ongoing_count}")
    finally:
        connection.close()


def main() -> int:
    print("=" * 76)
    print("创建订单 ABBA 死锁复现")
    print("=" * 76)
    print(f"数据库：{io_layer.DB_CONFIG['database']}")
    print(f"测试桌台：{TABLE_ID}")
    print(f"消费 A：{CONSUMPTION_A}")
    print(f"消费 B：{CONSUMPTION_B}")

    deadlock_found = False
    probe = None

    try:
        seed_fixture()

        connection = open_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@transaction_isolation")
                isolation_level = cursor.fetchone()[0]
        finally:
            connection.close()
        print(f"MySQL 隔离级别：{isolation_level}")

        probe = ConcurrencyProbe()
        results = []

        with patch.object(
            controller_layer,
            "one_execute_in_transaction",
            probe.wrapped_execute_in_transaction,
        ), patch.object(
            controller_layer,
            "one_delete_ongoing_consumption_from_mysql",
            probe.wrapped_delete,
        ):
            with ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="order-request",
            ) as executor:
                futures = [
                    executor.submit(run_request, label, consumption_ids)
                    for label, consumption_ids in SELECTED_ORDERS.items()
                ]
                for future in as_completed(
                    futures,
                    timeout=WORKER_TIMEOUT_SECONDS,
                ):
                    results.append(future.result())

        results.sort(key=lambda item: item["label"])

        print()
        print("两个请求的返回：")
        for item in results:
            result = item["result"]
            print(f"\n{item['label']}，耗时 {item['elapsed_ms']} ms")
            print(f"  status     = {result.get('status')}")
            print(f"  error_type = {result.get('error_type')}")
            print(f"  message    = {result.get('message')}")

        deadlock_results = [
            item for item in results if is_deadlock_result(item["result"])
        ]
        deadlock_found = bool(deadlock_results)

        print()
        if deadlock_found:
            victim = deadlock_results[0]
            print(
                f"复现成功：{victim['label']} 被 MySQL {DEADLOCK_ERRNO} "
                "判定为死锁牺牲者，事务已整体回滚。"
            )
        else:
            print("本次没有抓到 1213 死锁。")
            print("请检查两个请求是否确实选中同一组 A/B，以及 MySQL 是否为 InnoDB。")

        print_post_state()

        if probe is not None:
            print()
            print(f"共记录 {len(probe.events)} 条并发时序。")

        return 0 if deadlock_found else 1

    finally:
        try:
            cleanup()
            print(f"\n已清理测试桌台 {TABLE_ID} 及本次消费 A/B 的全部测试数据。")
        except Exception as cleanup_error:
            print(
                f"\n警告：测试数据清理失败：{cleanup_error}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
