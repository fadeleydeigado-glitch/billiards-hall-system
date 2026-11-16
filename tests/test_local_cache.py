# -*- coding: utf-8 -*-
"""本地 TTL 缓存及控制器接入测试；不连接真实数据库。"""

import threading
import unittest
from decimal import Decimal
from unittest.mock import patch

from backend import controller_layer, local_cache
from backend.local_cache import LocalCache


def success_result(data, message="成功"):
    return {
        "status": "success",
        "error_type": None,
        "data": data,
        "message": message,
    }


class FakeClock:
    def __init__(self, current=0.0):
        self.current = current

    def __call__(self):
        return self.current


class BrokenClock:
    def __call__(self):
        raise RuntimeError("clock failed")


class BrokenVersionDict(dict):
    def __setitem__(self, key, value):
        raise RuntimeError("version write failed")


class LocalCacheUnitTests(unittest.TestCase):
    def test_hit_returns_deep_copied_value_and_version(self):
        cache = LocalCache(clock=FakeClock())
        value = {"P_001": {"库存": 3}}

        self.assertTrue(cache.set_if_version("products:list", value, 0))
        value["P_001"]["库存"] = 999

        cached, version = cache.get_with_version("products:list")
        self.assertEqual(version, 0)
        self.assertEqual(cached, {"P_001": {"库存": 3}})

        cached["P_001"]["库存"] = 1
        cached_again, _ = cache.get_with_version("products:list")
        self.assertEqual(cached_again["P_001"]["库存"], 3)

    def test_ttl_expires_lazily_after_five_minutes(self):
        clock = FakeClock(current=1000)
        cache = LocalCache(clock=clock)
        self.assertTrue(
            cache.set_if_version("tables:config", {"1": {}}, 0)
        )

        clock.current = 1299
        cached, _ = cache.get_with_version("tables:config")
        self.assertEqual(cached, {"1": {}})

        clock.current = 1300
        expired, version = cache.get_with_version("tables:config")
        self.assertIsNone(expired)
        self.assertEqual(version, 0)

        # 过期后再次回填可以使用原版本号。
        self.assertTrue(
            cache.set_if_version("tables:config", {"1": {"价格": 30}}, 0)
        )
        refreshed, _ = cache.get_with_version("tables:config")
        self.assertEqual(refreshed, {"1": {"价格": 30}})

    def test_version_guard_discards_stale_refill(self):
        cache = LocalCache(clock=FakeClock())
        cached, version = cache.get_with_version("products:list")
        self.assertIsNone(cached)
        self.assertEqual(version, 0)

        # 模拟查库期间有写事务提交并让缓存失效。
        self.assertTrue(cache.invalidate("products:list"))
        self.assertFalse(
            cache.set_if_version(
                "products:list",
                {"P_001": {"库存": 9}},
                version,
            )
        )

        after_write, new_version = cache.get_with_version("products:list")
        self.assertIsNone(after_write)
        self.assertEqual(new_version, 1)

        # 新查询带着新版本回填，允许写入。
        self.assertTrue(
            cache.set_if_version(
                "products:list",
                {"P_001": {"库存": 1}},
                new_version,
            )
        )
        fresh, _ = cache.get_with_version("products:list")
        self.assertEqual(fresh["P_001"]["库存"], 1)

    def test_concurrent_stale_refill_is_rejected(self):
        cache = LocalCache(clock=FakeClock())
        reader_has_version = threading.Event()
        writer_finished = threading.Event()
        reader_result = {}

        def slow_reader():
            _, version = cache.get_with_version("products:list")
            reader_result["version"] = version
            reader_has_version.set()
            writer_finished.wait(timeout=2)
            reader_result["set_result"] = cache.set_if_version(
                "products:list",
                {"P_001": {"库存": 9}},
                version,
            )

        reader = threading.Thread(target=slow_reader)
        reader.start()
        self.assertTrue(reader_has_version.wait(timeout=2))
        self.assertTrue(cache.invalidate("products:list"))
        writer_finished.set()
        reader.join(timeout=2)

        self.assertFalse(reader.is_alive())
        self.assertFalse(reader_result["set_result"])
        cached, version = cache.get_with_version("products:list")
        self.assertIsNone(cached)
        self.assertEqual(version, 1)

    def test_cache_operation_exceptions_never_escape(self):
        cache = LocalCache(clock=BrokenClock())
        cache._versions = BrokenVersionDict()

        with self.assertLogs("backend.local_cache", level="WARNING"):
            cached, version = cache.get_with_version("products:list")
            self.assertIsNone(cached)
            self.assertEqual(version, 0)
            self.assertFalse(
                cache.set_if_version("products:list", {"P_001": {}}, 0)
            )
            self.assertFalse(cache.invalidate("products:list"))

    def test_invalidation_increments_even_when_key_was_never_cached(self):
        cache = LocalCache(clock=FakeClock())
        self.assertTrue(cache.invalidate("products:list"))
        _, version = cache.get_with_version("products:list")
        self.assertEqual(version, 1)


class ControllerCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        local_cache.clear_all()

    def test_product_read_hits_cache_and_skips_second_database_load(self):
        products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 5,
            }
        }

        with patch.object(
            controller_layer,
            "one_load_products_from_mysql",
            return_value=success_result(products, "商品总表读取成功"),
        ) as loader:
            first = controller_layer.three_get_frontend_products_data()
            second = controller_layer.three_get_frontend_products_data()

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(first["data"], second["data"])
        loader.assert_called_once()

    def test_table_config_read_hits_cache(self):
        tables = {"1": {"每小时价格": Decimal("30.00")}}

        with patch.object(
            controller_layer,
            "one_load_billiards_tables_from_mysql",
            return_value=success_result(tables, "桌台配置表读取成功"),
        ) as loader:
            self.assertTrue(controller_layer.check_table_exists("1"))
            self.assertTrue(controller_layer.check_table_exists("1"))

        loader.assert_called_once()

    def test_ttl_expiry_makes_controller_reload_database(self):
        clock = FakeClock()
        cache = LocalCache(clock=clock)
        products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 5,
            }
        }

        with patch.object(local_cache, "_default_cache", cache):
            with patch.object(
                controller_layer,
                "one_load_products_from_mysql",
                return_value=success_result(products, "商品总表读取成功"),
            ) as loader:
                controller_layer.three_get_frontend_products_data()
                clock.current = 299
                controller_layer.three_get_frontend_products_data()
                loader.assert_called_once()

                clock.current = 300
                controller_layer.three_get_frontend_products_data()
                self.assertEqual(loader.call_count, 2)

    def test_cache_invalidation_forces_next_read_to_reload(self):
        first_products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 5,
            }
        }
        second_products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 4,
            }
        }

        with patch.object(
            controller_layer,
            "one_load_products_from_mysql",
            side_effect=[
                success_result(first_products, "商品总表读取成功"),
                success_result(second_products, "商品总表读取成功"),
            ],
        ) as loader:
            first = controller_layer.three_get_frontend_products_data()
            controller_layer.invalidate_products_cache_after_commit()
            second = controller_layer.three_get_frontend_products_data()

        self.assertEqual(first["data"]["P_001"]["stock"], 5)
        self.assertEqual(second["data"]["P_001"]["stock"], 4)
        self.assertEqual(loader.call_count, 2)

    def test_cache_failure_falls_back_to_database(self):
        products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 5,
            }
        }

        with patch.object(
            controller_layer.local_cache,
            "get_with_version",
            side_effect=RuntimeError("cache read failed"),
        ):
            with patch.object(
                controller_layer.local_cache,
                "set_if_version",
                side_effect=RuntimeError("cache write failed"),
            ):
                with patch.object(
                    controller_layer,
                    "one_load_products_from_mysql",
                    return_value=success_result(
                        products,
                        "商品总表读取成功",
                    ),
                ) as loader:
                    result = controller_layer._load_products_list_cached()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], products)
        loader.assert_called_once()

    def test_add_product_invalidates_after_transaction_commit(self):
        products = {
            "P_001": {
                "商品名称": "可乐",
                "单价": Decimal("3.00"),
                "库存": 5,
            }
        }
        product_info = products["P_001"]
        consumption = {
            "消费记录编号": "S_1_test",
            "桌子号码": "1",
            "商品编号": "P_001",
            "商品名称": "可乐",
            "单价": Decimal("3.00"),
            "数量": 1,
            "金额": Decimal("3.00"),
        }
        commit_state = {"committed": False}
        invalidate_keys = []

        def fake_transaction(operation, _key, _operation_name):
            result = operation(object())
            if result["status"] == "success":
                commit_state["committed"] = True
            return result

        real_invalidate = local_cache._default_cache.invalidate

        def spy_invalidate(key):
            if key == local_cache.PRODUCTS_LIST_KEY:
                self.assertTrue(commit_state["committed"])
                invalidate_keys.append(key)
            return real_invalidate(key)

        self.assertTrue(local_cache.set_if_version(
            local_cache.PRODUCTS_LIST_KEY,
            products,
            0,
        ))

        patches = [
            patch.object(controller_layer, "check_table_exists", return_value=True),
            patch.object(
                controller_layer,
                "one_get_completed_idempotency_result_from_mysql",
                return_value=success_result(None),
            ),
            patch.object(
                controller_layer,
                "one_load_product_by_id_from_mysql",
                return_value=success_result(product_info),
            ),
            patch.object(
                controller_layer,
                "two_get_product_info",
                side_effect=lambda info, _product_id: success_result(info),
            ),
            patch.object(
                controller_layer,
                "two_check_product_can_be_ordered",
                return_value=success_result({
                    "商品编号": "P_001",
                    "商品信息": product_info,
                    "购买数量": 1,
                }),
            ),
            patch.object(
                controller_layer,
                "one_load_ongoing_by_table_from_mysql",
                return_value=success_result({
                    "1": {"待结算消费": {}}
                }),
            ),
            patch.object(
                controller_layer,
                "two_check_all_table_consumption_boxes",
                side_effect=lambda data: success_result(data),
            ),
            patch.object(
                controller_layer,
                "two_get_table_consumption_box",
                side_effect=lambda data, _table_id: success_result(data["1"]),
            ),
            patch.object(
                controller_layer,
                "one_get_current_time_pair",
                return_value=(1700000000, 1700000000000),
            ),
            patch.object(
                controller_layer,
                "two_create_product_consumption",
                return_value=success_result(consumption),
            ),
            patch.object(
                controller_layer,
                "two_prepare_core_backup",
                return_value=success_result({"消费记录编号": "S_1_test"}),
            ),
            patch.object(
                controller_layer,
                "build_product_event_payload",
                return_value={},
            ),
            patch.object(
                controller_layer,
                "one_insert_ongoing_product_consumption_to_mysql",
                return_value=success_result(consumption),
            ),
            patch.object(
                controller_layer,
                "one_deduct_product_stock_in_mysql",
                return_value=success_result("P_001"),
            ),
            patch.object(
                controller_layer,
                "one_insert_outbox_event",
                return_value=success_result(1),
            ),
            patch.object(
                controller_layer,
                "one_execute_in_transaction_with_idempotency",
                side_effect=fake_transaction,
            ),
            patch.object(
                controller_layer,
                "one_write_business_log",
                return_value=None,
            ),
            patch.object(
                controller_layer.local_cache,
                "invalidate",
                side_effect=spy_invalidate,
            ),
        ]

        for cache_patch in patches:
            cache_patch.start()
            self.addCleanup(cache_patch.stop)

        result = controller_layer.three_add_product_consumption(
            "1",
            "P_001",
            1,
            idempotency_key="cache-test-key",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(invalidate_keys, [local_cache.PRODUCTS_LIST_KEY])
        cached_after_write, version_after_write = (
            local_cache.get_with_version(local_cache.PRODUCTS_LIST_KEY)
        )
        self.assertIsNone(cached_after_write)
        self.assertEqual(version_after_write, 1)

    def test_close_table_invalidates_after_transaction_commit(self):
        tables = {"1": {"每小时价格": Decimal("30.00")}}
        flow_box = {
            "桌子号码": "1",
            "台位消费": {
                "消费记录编号": "T_1_old",
                "类型": "台位费",
                "开台时间": 1700000000,
                "关台时间": 1700003600,
                "每小时价格": Decimal("30.00"),
                "金额": Decimal("30.00"),
            },
            "当前步骤": "关台完成",
        }
        commit_state = {"committed": False}
        invalidate_keys = []

        def fake_transaction(operation):
            result = operation(object())
            if result["status"] == "success":
                commit_state["committed"] = True
            return result

        real_invalidate = local_cache._default_cache.invalidate

        def spy_invalidate(key):
            if key == local_cache.TABLES_CONFIG_KEY:
                self.assertTrue(commit_state["committed"])
                invalidate_keys.append(key)
            return real_invalidate(key)

        self.assertTrue(local_cache.set_if_version(
            local_cache.TABLES_CONFIG_KEY,
            tables,
            0,
        ))

        patches = [
            patch.object(controller_layer, "check_table_exists", return_value=True),
            patch.object(
                controller_layer,
                "one_load_ongoing_by_table_from_mysql",
                return_value=success_result({
                    "1": {"待结算消费": {}}
                }),
            ),
            patch.object(
                controller_layer,
                "two_check_all_table_consumption_boxes",
                side_effect=lambda data: success_result(data),
            ),
            patch.object(
                controller_layer,
                "two_get_table_consumption_box",
                side_effect=lambda data, _table_id: success_result(data["1"]),
            ),
            patch.object(
                controller_layer,
                "two_get_open_table_consumption",
                return_value=success_result(flow_box),
            ),
            patch.object(
                controller_layer,
                "one_get_current_time",
                return_value=1700003600,
            ),
            patch.object(
                controller_layer,
                "two_add_close_time_to_table_consumption",
                return_value=success_result(flow_box),
            ),
            patch.object(
                controller_layer,
                "two_calculate_table_fee",
                return_value=success_result(flow_box),
            ),
            patch.object(
                controller_layer,
                "two_prepare_core_backup",
                return_value=success_result({"消费记录编号": "T_1_old"}),
            ),
            patch.object(
                controller_layer,
                "build_table_fee_event_payload",
                return_value={},
            ),
            patch.object(
                controller_layer,
                "one_update_ongoing_table_fee_in_mysql",
                return_value=success_result({}),
            ),
            patch.object(
                controller_layer,
                "one_insert_outbox_event",
                return_value=success_result(1),
            ),
            patch.object(
                controller_layer,
                "one_execute_in_transaction",
                side_effect=fake_transaction,
            ),
            patch.object(
                controller_layer,
                "one_write_business_log",
                return_value=None,
            ),
            patch.object(
                controller_layer.local_cache,
                "invalidate",
                side_effect=spy_invalidate,
            ),
        ]

        for cache_patch in patches:
            cache_patch.start()
            self.addCleanup(cache_patch.stop)

        result = controller_layer.three_close_table("1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(invalidate_keys, [local_cache.TABLES_CONFIG_KEY])
        cached_after_write, version_after_write = (
            local_cache.get_with_version(local_cache.TABLES_CONFIG_KEY)
        )
        self.assertIsNone(cached_after_write)
        self.assertEqual(version_after_write, 1)


if __name__ == "__main__":
    unittest.main()
