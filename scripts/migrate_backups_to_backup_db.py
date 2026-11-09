#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把主库里的旧核心备份表复制到独立备份库 billiards_backup。

默认只做只读检查：
    python scripts/migrate_backups_to_backup_db.py

确认后执行复制：
    python scripts/migrate_backups_to_backup_db.py --apply

说明：
- 迁移是幂等的，重复执行不会产生重复行；
- 只复制，不删除主库旧表，保留迁移期回退依据；
- 本脚本只处理两张备份表，不碰业务表。
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


DB_CONFIG = {
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


TABLES = {
    "core_backup": (
        "consumption_id",
        "桌台编号",
        "开台时间",
        "关台时间",
    ),
    "core_backup_products": (
        "consumption_id",
        "桌台编号",
        "商品编号",
        "商品名称",
        "单价",
        "数量",
        "金额",
        "创建时间",
    ),
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="把主库旧备份表复制到 billiards_backup，默认只读检查。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行复制；不加此参数时只读检查。",
    )
    return parser.parse_args(argv)


def table_exists(connection, table_name):
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        return cursor.fetchone() is not None


def fetch_ids(connection, table_name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT consumption_id FROM `%s`" % table_name)
        return {row[0] for row in cursor.fetchall()}


def main(argv=None):
    args = parse_args(argv)
    print("主库:", DB_CONFIG["database"], " 备份库:", BACKUP_DB_CONFIG["database"])

    main_conn = None
    backup_conn = None
    try:
        import pymysql
        main_conn = pymysql.connect(**DB_CONFIG)
        backup_conn = pymysql.connect(**BACKUP_DB_CONFIG)

        for table_name, columns in TABLES.items():
            if not table_exists(main_conn, table_name):
                print("\n[%s] 主库旧表不存在，跳过。" % table_name)
                continue
            if not table_exists(backup_conn, table_name):
                print("\n[%s] 备份库表不存在，请先执行 backup_schema.sql。" % table_name)
                return 1

            main_ids = fetch_ids(main_conn, table_name)
            backup_ids = fetch_ids(backup_conn, table_name)
            missing_ids = main_ids - backup_ids

            print("\n[%s]" % table_name)
            print("  主库: %d 行" % len(main_ids))
            print("  备份库: %d 行" % len(backup_ids))
            print("  待迁移: %d 行" % len(missing_ids))

            if not missing_ids:
                continue

            if not args.apply:
                sample = sorted(missing_ids)[:5]
                print("  待迁移样例: %s" % sample)
                continue

            column_sql = ", ".join("`%s`" % column for column in columns)
            placeholder_sql = ", ".join(["%s"] * len(columns))
            insert_sql = (
                "INSERT INTO `%s` (%s) VALUES (%s) "
                "ON DUPLICATE KEY UPDATE consumption_id = consumption_id"
                % (table_name, column_sql, placeholder_sql)
            )

            with main_conn.cursor() as main_cursor:
                main_cursor.execute(
                    "SELECT %s FROM `%s`"
                    % (column_sql, table_name)
                )
                rows = main_cursor.fetchall()

            with backup_conn.cursor() as backup_cursor:
                backup_cursor.executemany(insert_sql, rows)
            backup_conn.commit()

            remaining_ids = fetch_ids(backup_conn, table_name)
            print("  迁移后备份库: %d 行" % len(remaining_ids))
            print("  仍缺失: %d 行" % len(main_ids - remaining_ids))

        if not args.apply:
            print("\n当前为只读模式；确认后可使用 --apply 执行复制。")

    except Exception as error:
        print("[错误] 迁移失败: %s" % error, file=sys.stderr)
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
