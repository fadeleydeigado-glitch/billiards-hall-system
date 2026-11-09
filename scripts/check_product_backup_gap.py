#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨库检查并补录商品消费核心备份。

默认行为是只读检查：
    python scripts/check_product_backup_gap.py

确认缺失清单无误后，可以显式补录：
    python scripts/check_product_backup_gap.py --fix

数据源：
- 主库 billiards_hall：ongoing_product_consumptions、order_product_consumptions
- 备份库 billiards_backup：core_backup_products

补录只写备份库，不碰业务表；按 consumption_id 幂等。

创建时间说明：
- ongoing 记录从 ongoing_consumptions.创建时间 取得；
- order 记录优先从 S_{桌台}_{时间戳} 解析；
- 解析失败时回退到 orders.创建时间，并打印警告。
"""

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


MAIN_DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "billiards_hall"),
    "charset": "utf8mb4",
}

BACKUP_DB_CONFIG = {
    "host": os.environ.get("MYSQL_BACKUP_HOST", os.environ.get("MYSQL_HOST", "localhost")),
    "port": int(os.environ.get("MYSQL_BACKUP_PORT", os.environ.get("MYSQL_PORT", "3306"))),
    "user": os.environ.get("MYSQL_BACKUP_USER", os.environ.get("MYSQL_USER", "root")),
    "password": os.environ.get("MYSQL_BACKUP_PASSWORD", os.environ.get("MYSQL_PASSWORD", "")),
    "database": os.environ.get("MYSQL_BACKUP_DATABASE", "billiards_backup"),
    "charset": "utf8mb4",
}


SOURCE_QUERIES = (
    (
        "ongoing_product_consumptions",
        """
        SELECT
            f.consumption_id,
            f.`桌台编号`,
            f.`商品编号`,
            f.`商品名称`,
            f.`单价`,
            f.`数量`,
            f.`金额`,
            m.`创建时间`
        FROM `ongoing_product_consumptions` AS f
        INNER JOIN `ongoing_consumptions` AS m
            ON m.id = f.consumption_id
        ORDER BY m.`创建时间`, f.consumption_id
        """,
    ),
    (
        "order_product_consumptions",
        """
        SELECT
            f.consumption_id,
            o.`桌台编号`,
            f.`商品编号`,
            f.`商品名称`,
            f.`单价`,
            f.`数量`,
            f.`金额`,
            o.`创建时间`
        FROM `order_product_consumptions` AS f
        INNER JOIN `orders` AS o
            ON o.order_id = f.order_id
        ORDER BY o.`创建时间`, f.consumption_id
        """,
    ),
)


INSERT_SQL = """
INSERT INTO `core_backup_products`
    (consumption_id, `桌台编号`, `商品编号`, `商品名称`, `单价`, `数量`, `金额`, `创建时间`)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE consumption_id = consumption_id
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="跨库查找业务表中已存在、但 billiards_backup.core_backup_products 中缺失的商品消费备份。",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="补录缺失记录到备份库；不加此参数时只读打印。",
    )
    return parser.parse_args(argv)


def connect_mysql(config):
    try:
        import pymysql
    except ImportError:
        try:
            import mysql.connector as mysql_driver
        except ImportError as error:
            raise RuntimeError(
                "未找到 MySQL 驱动，请先安装 PyMySQL 或 mysql-connector-python。"
            ) from error
        return mysql_driver.connect(**config), "mysql-connector-python"

    return pymysql.connect(**config), "PyMySQL"


def parse_created_time(consumption_id):
    """从 S_{桌台}_{时间戳} 中解析创建时间；解析失败返回 None。"""
    match = re.match(r"^S_.+_(\d+)$", str(consumption_id))
    if match is None:
        return None
    return int(match.group(1))


def resolve_created_time(source, consumption_id, order_created_time):
    """返回 (创建时间, 是否使用了订单创建时间兜底)。"""
    if source == "order_product_consumptions":
        parsed = parse_created_time(consumption_id)
        if parsed is not None:
            return parsed, False
        return order_created_time, True
    return order_created_time, False


def find_source_records(cursor):
    records = []
    for source, sql in SOURCE_QUERIES:
        cursor.execute(sql)
        for row in cursor.fetchall():
            records.append((source, *row))
    return records


def find_existing_backup_ids(cursor):
    cursor.execute("SELECT consumption_id FROM `core_backup_products`")
    return {row[0] for row in cursor.fetchall()}


def find_missing_records(source_records, backup_ids):
    return [
        record for record in source_records
        if record[1] not in backup_ids
    ]


def print_missing_records(missing, source_count, backup_count):
    print("=" * 72)
    print("billiards_backup.core_backup_products 缺失检查")
    print("=" * 72)
    print("源记录总数: %d" % source_count)
    print("备份库已有: %d" % backup_count)
    print("缺失记录总数: %d" % len(missing))

    counts = {source: 0 for source, _ in SOURCE_QUERIES}
    for source, *_ in missing:
        counts[source] += 1
    for source, _ in SOURCE_QUERIES:
        print("  %-32s : %d" % (source, counts[source]))

    if not missing:
        print("\n未发现缺失记录。")
        return

    print("\n缺失清单:")
    current_source = None
    for (
        source,
        consumption_id,
        table_id,
        product_id,
        product_name,
        unit_price,
        quantity,
        amount,
        fallback_created_time,
    ) in missing:
        if source != current_source:
            current_source = source
            print("\n[%s]" % source)
        created_time, used_fallback = resolve_created_time(
            source, consumption_id, fallback_created_time
        )
        fallback_note = "（回退到订单创建时间）" if used_fallback else ""
        print(
            "  consumption_id=%s  桌台编号=%s  商品=%s/%s  单价=%s  数量=%s  "
            "金额=%s  创建时间=%s%s"
            % (
                consumption_id,
                table_id,
                product_id,
                product_name,
                unit_price,
                quantity,
                amount,
                created_time,
                fallback_note,
            )
        )


def insert_missing_records(cursor, missing):
    inserted = 0
    skipped = 0
    seen = set()

    for (
        source,
        consumption_id,
        table_id,
        product_id,
        product_name,
        unit_price,
        quantity,
        amount,
        fallback_created_time,
    ) in missing:
        if consumption_id in seen:
            skipped += 1
            continue
        seen.add(consumption_id)

        created_time, used_fallback = resolve_created_time(
            source, consumption_id, fallback_created_time
        )
        if used_fallback:
            print(
                "[警告] 消费编号无法解析创建时间，使用订单创建时间：%s -> %s"
                % (consumption_id, created_time)
            )

        cursor.execute(
            INSERT_SQL,
            (
                consumption_id,
                table_id,
                product_id,
                product_name,
                unit_price,
                quantity,
                amount,
                created_time,
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    return inserted, skipped


def main(argv=None):
    args = parse_args(argv)
    main_conn = None
    backup_conn = None

    try:
        main_conn, main_driver = connect_mysql(MAIN_DB_CONFIG)
        backup_conn, backup_driver = connect_mysql(BACKUP_DB_CONFIG)
        print(
            "主库: %s (%s)   备份库: %s (%s)"
            % (
                MAIN_DB_CONFIG["database"],
                main_driver,
                BACKUP_DB_CONFIG["database"],
                backup_driver,
            )
        )

        with main_conn.cursor() as main_cursor:
            source_records = find_source_records(main_cursor)

        with backup_conn.cursor() as backup_cursor:
            backup_ids = find_existing_backup_ids(backup_cursor)

        missing = find_missing_records(source_records, backup_ids)
        print_missing_records(missing, len(source_records), len(backup_ids))

        if args.fix and missing:
            with backup_conn.cursor() as backup_cursor:
                inserted, skipped = insert_missing_records(backup_cursor, missing)
            backup_conn.commit()
            print(
                "\n补录完成: 新增 %d 条，已存在/重复跳过 %d 条。"
                % (inserted, skipped)
            )

            with backup_conn.cursor() as backup_cursor:
                remaining_ids = find_existing_backup_ids(backup_cursor)
            remaining = find_missing_records(source_records, remaining_ids)
            print("复查剩余缺失: %d 条。" % len(remaining))
        elif args.fix:
            print("\n无需补录。")
        elif missing:
            print("\n当前为只读模式；确认后可使用 --fix 补录到备份库。")

    except Exception as error:
        print("[错误] 检查或补录失败: %s" % error, file=sys.stderr)
        if backup_conn is not None:
            try:
                backup_conn.rollback()
            except Exception:
                pass
        return 1
    finally:
        if main_conn is not None:
            main_conn.close()
        if backup_conn is not None:
            backup_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
