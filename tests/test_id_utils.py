# -*- coding: utf-8 -*-
"""业务编号生成与解析测试。"""

import unittest

from backend.id_utils import (
    RANDOM_PART_LENGTH,
    build_business_id,
    parse_business_id,
    parse_business_id_time,
    resolve_id_timestamp_ms,
)


class BusinessIdTests(unittest.TestCase):
    def test_new_id_parses_back_to_millisecond_time(self):
        value = build_business_id(
            "S",
            "1",
            1791942761123,
            random_part="abc123",
        )

        parsed = parse_business_id(value)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["prefix"], "S")
        self.assertEqual(parsed["table_id"], "1")
        self.assertEqual(parsed["timestamp_ms"], 1791942761123)
        self.assertEqual(parsed["random_part"], "abc123")
        self.assertEqual(len(parsed["random_part"]), RANDOM_PART_LENGTH)
        self.assertEqual(parsed["format"], "millisecond_base36")
        self.assertEqual(parse_business_id_time(value), 1791942761)

    def test_order_id_has_no_prefix_and_still_parses(self):
        value = build_business_id(
            "",
            "2",
            1791942761999,
            random_part="xyz789",
        )

        parsed = parse_business_id(value)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["prefix"], "")
        self.assertEqual(parsed["table_id"], "2")
        self.assertEqual(parsed["timestamp_ms"], 1791942761999)
        self.assertEqual(parse_business_id_time(value), 1791942761)

    def test_seconds_fallback_and_explicit_milliseconds(self):
        self.assertEqual(
            resolve_id_timestamp_ms(1700000000),
            1700000000000,
        )
        self.assertEqual(
            resolve_id_timestamp_ms(1700000000, 1700000000123),
            1700000000123,
        )

    def test_legacy_seconds_id_is_still_supported(self):
        for value in ("T_1_1700000000", "S_1_1700000000", "1_1700000000"):
            parsed = parse_business_id(value)

            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["format"], "legacy_seconds")
            self.assertEqual(parsed["timestamp_ms"], 1700000000000)
            self.assertEqual(parse_business_id_time(value), 1700000000)

    def test_same_millisecond_generates_unique_ids(self):
        values = {
            build_business_id("S", "1", 1791942761123)
            for _ in range(256)
        }

        self.assertEqual(len(values), 256)
        self.assertTrue(all(len(value) <= 30 for value in values))

    def test_max_table_id_keeps_id_within_database_column(self):
        table_id = "1234567890"
        timestamp_ms = 1791942761123

        for prefix in ("T", "S", ""):
            value = build_business_id(prefix, table_id, timestamp_ms)
            self.assertLessEqual(len(value), 30, value)

    def test_invalid_random_part_is_rejected(self):
        with self.assertRaises(ValueError):
            build_business_id("S", "1", 1791942761123, random_part="short")

        with self.assertRaises(ValueError):
            build_business_id("S", "1", 1791942761123, random_part="!!!!!!")

    def test_overlong_business_id_is_rejected(self):
        with self.assertRaises(ValueError):
            build_business_id("S", "12345678901", 1791942761123)


if __name__ == "__main__":
    unittest.main()
