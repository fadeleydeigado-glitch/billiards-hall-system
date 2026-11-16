# -*- coding: utf-8 -*-
"""热点抢购内存发牌、任务队列、重试与补偿测试；不连接数据库。"""

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend import api_layer
from backend.flash_sale_queue import FlashSaleEngine
from backend.schemas import FlashProductConsumeRequest


def success_result(data, message="成功"):
    return {
        "status": "success",
        "error_type": None,
        "data": data,
        "message": message,
    }


def wait_for_status(engine, ticket_id, expected, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ticket = engine.get_ticket(ticket_id)
        if (
            ticket["status"] == "success"
            and ticket["data"]["status"] == expected
        ):
            return ticket["data"]
        time.sleep(0.01)
    raise AssertionError(f"ticket {ticket_id} did not reach {expected}")


class QueueEngineTestBase(unittest.TestCase):
    def make_engine(self, handler, **kwargs):
        engine = FlashSaleEngine(
            task_handler=handler,
            retry_backoff_seconds=0,
            **kwargs,
        )
        self.addCleanup(lambda: engine.stop(drain=True, timeout=3))
        return engine


class ProductTokenConcurrencyTests(QueueEngineTestBase):
    def test_five_tokens_are_issued_once_to_twenty_threads(self):
        handled = []
        handled_lock = threading.Lock()

        def handler(task):
            with handled_lock:
                handled.append(task.ticket_id)
            return {
                "status": "success",
                "result": {"ticket_id": task.ticket_id},
            }

        engine = self.make_engine(handler)
        preheat = engine.preheat_product("P_HOT", 5)
        self.assertEqual(preheat["status"], "success")
        self.assertEqual(preheat["data"]["available"], 5)

        barrier = threading.Barrier(20)

        def acquire():
            barrier.wait()
            return engine.acquire_product_ticket("1", "P_HOT")

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _: acquire(), range(20)))

        successes = [item for item in results if item["status"] == "success"]
        sold_out = [
            item for item in results
            if item["error_type"] == "FlashSaleSoldOut"
        ]

        self.assertEqual(len(successes), 5)
        self.assertEqual(len(sold_out), 15)
        self.assertEqual(len({
            item["data"]["ticket_id"] for item in successes
        }), 5)
        self.assertTrue(engine.wait_until_idle(timeout=3))
        self.assertEqual(len(handled), 5)

        for item in successes:
            ticket = wait_for_status(
                engine,
                item["data"]["ticket_id"],
                "succeeded",
            )
            self.assertEqual(
                ticket["result"]["ticket_id"],
                item["data"]["ticket_id"],
            )

        pool = engine.get_pool_status()["data"]
        self.assertEqual(pool["products"]["P_HOT"], 0)
        self.assertEqual(pool["ticket_statuses"]["succeeded"], 5)
        self.assertEqual(pool["ticket_statuses"]["failed"], 0)


class TableTokenConcurrencyTests(QueueEngineTestBase):
    def test_one_table_token_is_issued_once_to_twenty_threads(self):
        def handler(task):
            return {"status": "success", "result": {"task": task.ticket_id}}

        engine = self.make_engine(handler)
        self.assertEqual(
            engine.preheat_tables(["T_HOT"])["status"],
            "success",
        )

        barrier = threading.Barrier(20)

        def acquire():
            barrier.wait()
            return engine.acquire_table_ticket("T_HOT")

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _: acquire(), range(20)))

        successes = [item for item in results if item["status"] == "success"]
        occupied = [
            item for item in results
            if item["error_type"] == "TableOccupied"
        ]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(occupied), 19)
        self.assertTrue(engine.wait_until_idle(timeout=3))
        wait_for_status(
            engine,
            successes[0]["data"]["ticket_id"],
            "succeeded",
        )


class CompensationTests(QueueEngineTestBase):
    def test_business_failure_refunds_token_and_queue_continues(self):
        processed = []
        processed_lock = threading.Lock()

        def handler(task):
            with processed_lock:
                processed.append(task.ticket_id)
                is_first = len(processed) == 1

            if is_first:
                return {
                    "status": "error",
                    "retryable": False,
                    "refund_token": True,
                    "error": "模拟业务失败",
                }
            return {"status": "success", "result": {"ok": True}}

        engine = self.make_engine(handler)
        engine.preheat_product("P_REFUND", 2)

        first = engine.acquire_product_ticket("1", "P_REFUND")
        second = engine.acquire_product_ticket("1", "P_REFUND")
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")

        self.assertTrue(engine.wait_until_idle(timeout=3))
        tickets = [
            engine.get_ticket(first["data"]["ticket_id"])["data"],
            engine.get_ticket(second["data"]["ticket_id"])["data"],
        ]
        statuses = sorted(ticket["status"] for ticket in tickets)
        self.assertEqual(statuses, ["failed", "succeeded"])
        self.assertEqual(
            engine.get_pool_status()["data"]["products"]["P_REFUND"],
            1,
        )

        third = engine.acquire_product_ticket("1", "P_REFUND")
        self.assertEqual(third["status"], "success")
        self.assertTrue(engine.wait_until_idle(timeout=3))

    def test_retry_stops_after_two_retries_and_refunds(self):
        attempts = {}
        attempts_lock = threading.Lock()

        def handler(task):
            with attempts_lock:
                attempts[task.ticket_id] = attempts.get(task.ticket_id, 0) + 1
            return {
                "status": "error",
                "retryable": True,
                "error": "模拟瞬时数据库抖动",
            }

        engine = self.make_engine(handler)
        engine.preheat_product("P_RETRY", 1)
        acquired = engine.acquire_product_ticket("1", "P_RETRY")
        ticket_id = acquired["data"]["ticket_id"]

        self.assertTrue(engine.wait_until_idle(timeout=3))
        ticket = engine.get_ticket(ticket_id)["data"]
        self.assertEqual(ticket["status"], "failed")
        self.assertEqual(ticket["attempts"], 3)
        self.assertEqual(attempts[ticket_id], 3)
        self.assertEqual(
            engine.get_pool_status()["data"]["products"]["P_RETRY"],
            1,
        )

    def test_retry_can_recover_on_third_attempt(self):
        attempts = {}
        attempts_lock = threading.Lock()

        def handler(task):
            with attempts_lock:
                attempts[task.ticket_id] = attempts.get(task.ticket_id, 0) + 1
                current = attempts[task.ticket_id]
            if current < 3:
                return {
                    "status": "error",
                    "retryable": True,
                    "error": "临时抖动",
                }
            return {"status": "success", "result": {"attempt": current}}

        engine = self.make_engine(handler)
        engine.preheat_product("P_RECOVER", 1)
        acquired = engine.acquire_product_ticket("1", "P_RECOVER")
        ticket_id = acquired["data"]["ticket_id"]

        self.assertTrue(engine.wait_until_idle(timeout=3))
        ticket = engine.get_ticket(ticket_id)["data"]
        self.assertEqual(ticket["status"], "succeeded")
        self.assertEqual(ticket["attempts"], 3)
        self.assertEqual(ticket["result"]["attempt"], 3)


class OldPathSyncTests(QueueEngineTestBase):
    def test_old_open_removes_token_and_old_close_restores_it(self):
        def handler(task):
            return {"status": "success", "result": None}

        engine = self.make_engine(handler)
        engine.preheat_tables(["T_SYNC"])

        self.assertTrue(engine.on_table_opened("T_SYNC"))
        rejected = engine.acquire_table_ticket("T_SYNC")
        self.assertEqual(rejected["error_type"], "TableOccupied")

        self.assertTrue(engine.on_table_closed("T_SYNC"))
        acquired = engine.acquire_table_ticket("T_SYNC")
        self.assertEqual(acquired["status"], "success")
        self.assertTrue(engine.wait_until_idle(timeout=3))

    def test_old_product_sale_removes_idle_tokens(self):
        def handler(task):
            return {"status": "success", "result": None}

        engine = self.make_engine(handler)
        engine.preheat_product("P_SYNC", 5)
        self.assertEqual(engine.on_product_sold("P_SYNC", 2), 2)
        self.assertEqual(
            engine.get_pool_status()["data"]["products"]["P_SYNC"],
            3,
        )


class FlashSaleApiContractTests(unittest.TestCase):
    def test_consume_endpoint_returns_ticket_contract(self):
        expected = success_result({
            "ticket_id": "tkt_test",
            "status": "queued",
        })
        with patch.object(
            api_layer.flash_sale_service,
            "acquire_product_ticket",
            return_value=expected,
        ) as acquire:
            response = api_layer.api_flash_sale_consume_product(
                FlashProductConsumeRequest(
                    table_id="1",
                    product_id="P_001",
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["ticket_id"], "tkt_test")
        self.assertEqual(payload["data"]["status"], "queued")
        acquire.assert_called_once_with("1", "P_001")

    def test_unknown_ticket_is_404(self):
        response = api_layer.api_flash_sale_get_ticket("tkt_missing")
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error_type"], "FlashTicketNotFound")


if __name__ == "__main__":
    unittest.main()
