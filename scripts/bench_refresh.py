"""首页刷新接口的只读压测脚本。

用法示例：
    python scripts/bench_refresh.py --label baseline
    python scripts/bench_refresh.py --workers 10 --requests 200

脚本只发送 GET 请求，不修改业务数据。p50/p95 使用 nearest-rank 口径：
先把成功请求的耗时升序排列，再取 ceil(p/100 * n) 位置的值。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


DEFAULT_URL = "http://127.0.0.1:8000/frontend/refresh"


@dataclass
class RequestResult:
    ok: bool
    milliseconds: float
    error: str | None = None


def percentile_nearest_rank(sorted_values: list[float], percentile: float) -> float:
    """返回 nearest-rank 口径的分位数。"""
    if not sorted_values:
        raise ValueError("没有可计算的样本")
    if not 0 < percentile <= 100:
        raise ValueError("percentile 必须在 (0, 100] 范围内")

    rank = math.ceil(percentile / 100 * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def one_request(url: str, timeout: float) -> RequestResult:
    """请求一次首页刷新接口，并校验 HTTP 状态和业务 status。"""
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            http_status = getattr(response, "status", response.getcode())

        elapsed_ms = (time.perf_counter() - start) * 1000

        if http_status != 200:
            return RequestResult(False, elapsed_ms, f"HTTP {http_status}")

        # 接口正常时应返回 JSON，且 status 必须为 success；
        # 否则“业务失败但响应很快”会被误当成性能数据。
        import json

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return RequestResult(False, elapsed_ms, f"响应不是合法 JSON: {exc}")

        if not isinstance(payload, dict) or payload.get("status") != "success":
            message = payload.get("message") if isinstance(payload, dict) else None
            return RequestResult(False, elapsed_ms, f"业务失败: {message!r}")

        return RequestResult(True, elapsed_ms)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return RequestResult(False, elapsed_ms, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - 压测脚本要把单个异常转成失败样本
        elapsed_ms = (time.perf_counter() - start) * 1000
        return RequestResult(False, elapsed_ms, f"{type(exc).__name__}: {exc}")


def run_batch(url: str, workers: int, count: int, timeout: float) -> list[RequestResult]:
    """用固定线程池并发执行 count 次请求。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one_request, url, timeout) for _ in range(count)]
        return [future.result() for future in futures]


def print_failures(results: list[RequestResult], limit: int = 5) -> None:
    failures = [result for result in results if not result.ok]
    if not failures:
        return
    print(f"failed_samples={len(failures)}")
    for result in failures[:limit]:
        print(f"  error={result.error}")
    if len(failures) > limit:
        print(f"  ... 其余 {len(failures) - limit} 条失败已省略")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="压测 /frontend/refresh 的只读脚本")
    parser.add_argument("--url", default=DEFAULT_URL, help="接口地址")
    parser.add_argument("--workers", type=int, default=10, help="并发线程数")
    parser.add_argument("--requests", type=int, default=200, help="正式测试的请求总数")
    parser.add_argument("--warmup", type=int, default=10, help="正式测试前的预热请求数")
    parser.add_argument("--timeout", type=float, default=30.0, help="单个请求超时秒数")
    parser.add_argument("--label", default="benchmark", help="本轮测试的名称，便于记录")
    args = parser.parse_args()

    if args.workers <= 0:
        parser.error("--workers 必须大于 0")
    if args.requests <= 0:
        parser.error("--requests 必须大于 0")
    if args.warmup < 0:
        parser.error("--warmup 不能小于 0")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    return args


def main() -> int:
    args = parse_args()

    print(f"label={args.label}")
    print(f"url={args.url}")
    print(f"workers={args.workers} requests={args.requests} warmup={args.warmup}")

    if args.warmup:
        warmup_results = run_batch(
            args.url, args.workers, args.warmup, args.timeout
        )
        warmup_failures = sum(1 for result in warmup_results if not result.ok)
        if warmup_failures == len(warmup_results):
            print("预热请求全部失败，停止测试")
            print_failures(warmup_results)
            return 2
        if warmup_failures:
            print(f"warmup_failed={warmup_failures}/{len(warmup_results)}")

    started_at = time.perf_counter()
    results = run_batch(args.url, args.workers, args.requests, args.timeout)
    wall_seconds = time.perf_counter() - started_at

    successes = sorted(
        result.milliseconds for result in results if result.ok
    )
    failures = len(results) - len(successes)

    if not successes:
        print("正式测试没有成功样本")
        print_failures(results)
        return 2

    p50 = percentile_nearest_rank(successes, 50)
    p95 = percentile_nearest_rank(successes, 95)
    average = sum(successes) / len(successes)

    print(f"p50={p50:.2f}ms p95={p95:.2f}ms")
    print(f"avg={average:.2f}ms min={successes[0]:.2f}ms max={successes[-1]:.2f}ms")
    print(f"success={len(successes)}/{len(results)} failed={failures}")
    print(
        f"wall={wall_seconds:.2f}s throughput={len(results) / wall_seconds:.2f} req/s"
    )
    print_failures(results)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
