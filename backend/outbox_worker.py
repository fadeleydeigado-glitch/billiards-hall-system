# -*- coding: utf-8 -*-
"""
outbox 异步 worker 的启动入口。

常驻模式：
    python -m backend.outbox_worker

只跑一轮（适合测试或手动补跑）：
    python -m backend.outbox_worker --once

可调参数：
    --poll-interval 1.0   空闲时轮询间隔（秒）
    --max-events 100      单轮最多处理多少条事件
"""

import argparse
import signal
import sys

from .outbox import request_stop, run_forever, run_once


def _install_signal_handlers():
    def handle_signal(signum, frame):
        request_stop()

    # Windows 下优先监听 SIGINT / SIGBREAK，其他平台监听 SIGINT / SIGTERM。
    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is None:
            continue
        try:
            signal.signal(signal_value, handle_signal)
        except (ValueError, OSError):
            # 某些环境不允许注册该信号，忽略即可。
            pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="处理主库 outbox_events，并将备份写入 billiards_backup。",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只处理一轮可用事件后退出，适合测试或手动补跑。",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="空闲时轮询间隔（秒），默认读取 OUTBOX_POLL_INTERVAL 或 1.0。",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=100,
        help="单轮最多处理多少条事件，默认 100。",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.once:
        stats = run_once(args.max_events)
        print(
            "本轮 outbox 处理完成：claimed=%d done=%d failed=%d dead=%d"
            % (
                stats["claimed"],
                stats["done"],
                stats["failed"],
                stats["dead"],
            )
        )
        return 0

    _install_signal_handlers()
    run_forever(args.poll_interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
