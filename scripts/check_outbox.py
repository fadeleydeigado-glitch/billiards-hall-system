#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查看主库 outbox_events 的积压与死信，并可手动重新入队。

只读查看：
    python scripts/check_outbox.py

重排全部 dead 事件：
    python scripts/check_outbox.py --retry-dead

重排指定 dead 事件：
    python scripts/check_outbox.py --retry-event 123
"""

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.io_layer import DB_CONFIG
from backend.outbox import (
    one_get_outbox_stats,
    one_list_dead_outbox_events,
    one_requeue_dead_outbox_events,
)


def format_time(timestamp):
    if not timestamp:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="查看 outbox_events 状态，或重排 dead 事件。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--retry-dead",
        action="store_true",
        help="把所有 dead 事件重新放回 pending。",
    )
    group.add_argument(
        "--retry-event",
        type=int,
        default=None,
        help="把指定 event_id 的 dead 事件重新放回 pending。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="最多列出多少条 dead 事件，默认 20。",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print("数据库: %s" % DB_CONFIG["database"])

    if args.retry_dead:
        result = one_requeue_dead_outbox_events()
        if result["status"] != "success":
            print("[错误] 重排失败: %s" % result["message"], file=sys.stderr)
            return 1
        print("已重排 dead 事件: %d 条。" % result["data"])
        return 0

    if args.retry_event is not None:
        result = one_requeue_dead_outbox_events(args.retry_event)
        if result["status"] != "success":
            print("[错误] 重排失败: %s" % result["message"], file=sys.stderr)
            return 1
        print("已重排 event_id=%s: %d 条。" % (args.retry_event, result["data"]))
        return 0

    stats_result = one_get_outbox_stats()
    if stats_result["status"] != "success":
        print("[错误] 读取统计失败: %s" % stats_result["message"], file=sys.stderr)
        return 1

    counts = stats_result["data"]["counts"]
    oldest = stats_result["data"]["oldest_pending_at"]
    print("=" * 60)
    print("outbox_events 状态统计")
    print("=" * 60)
    print("  pending    : %d" % counts["pending"])
    print("  processing : %d" % counts["processing"])
    print("  done       : %d" % counts["done"])
    print("  dead       : %d" % counts["dead"])
    print("  最老 pending: %s" % format_time(oldest))
    if oldest:
        age = max(0, int(time.time()) - int(oldest))
        print("  pending 最老已等待: %d 秒" % age)

    dead_result = one_list_dead_outbox_events(args.limit)
    if dead_result["status"] != "success":
        print("[错误] 读取 dead 事件失败: %s" % dead_result["message"], file=sys.stderr)
        return 1

    dead_events = dead_result["data"]
    if not dead_events:
        print("\n没有 dead 事件。")
        return 0

    print("\ndead 事件（最多 %d 条）:" % args.limit)
    for event in dead_events:
        print(
            "  event_id=%s  type=%s  retry=%s  created=%s  error=%s"
            % (
                event["event_id"],
                event["event_type"],
                event["retry_count"],
                format_time(event["created_at"]),
                event["last_error"],
            )
        )
    print("\n可用 --retry-dead 或 --retry-event <id> 重新入队。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
