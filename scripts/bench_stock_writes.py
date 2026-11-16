"""压测现有「加商品并扣库存」写接口。

该脚本只允许在数据库名包含 test 的环境中运行，并使用专用测试桌台和商品。
每轮开始前重置库存和测试数据，结束后清理本次写入的消费、幂等记录和 outbox
事件，避免污染其他测试数据。

用法示例：
    $env:MYSQL_DATABASE = "billiards_hall_test"
    python scripts/bench_stock_writes.py --port 8011

默认测试矩阵会依次运行 1/10/25/50 个并发线程。分位数使用 nearest-rank 口径；
吞吐按全部请求完成耗时计算，成功吞吐另行给出。
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import platform
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pymysql


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.io_layer import DB_CONFIG  # noqa: E402


TEST_TABLE_ID = "B99"
TEST_PRODUCT_ID = "PBENCH01"
TEST_PRODUCT_NAME = "库存压测商品"
IDEMPOTENCY_PREFIX = "bench-stock-"
DEFAULT_SCENARIOS = "1:50,10:300,25:500,50:1000"


@dataclass
class RequestOutcome:
    ok: bool
    milliseconds: float
    error_type: str | None = None
    http_status: int | None = None
    message: str | None = None


@dataclass
class WorkerResult:
    outcomes: list[RequestOutcome]


def percentile_nearest_rank(sorted_values: list[float], percentile: float) -> float:
    """返回 nearest-rank 口径的分位数。"""
    if not sorted_values:
        raise ValueError("没有可计算的样本")
    if not 0 < percentile <= 100:
        raise ValueError("percentile 必须在 (0, 100] 范围内")

    rank = math.ceil(percentile / 100 * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def parse_scenarios(value: str) -> list[tuple[int, int]]:
    scenarios: list[tuple[int, int]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue

        parts = item.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"场景格式错误：{item!r}，应写成 workers:requests")

        workers = int(parts[0])
        requests = int(parts[1])
        if workers <= 0 or requests <= 0:
            raise ValueError(f"场景参数必须大于 0：{item!r}")
        scenarios.append((workers, requests))

    if not scenarios:
        raise ValueError("至少需要一个压测场景")
    return scenarios


def open_database():
    return pymysql.connect(**DB_CONFIG)


def scalar(connection, sql: str, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else None


def cleanup_test_data() -> None:
    """只清理本脚本专用桌台、商品和带专用前缀的幂等记录。"""
    connection = open_database()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM outbox_events "
                "WHERE payload->>\"$.table_id\" = %s",
                (TEST_TABLE_ID,),
            )
            cursor.execute(
                "DELETE FROM idempotency_records "
                "WHERE idempotency_key LIKE %s",
                (f"{IDEMPOTENCY_PREFIX}%",),
            )
            cursor.execute(
                "DELETE FROM ongoing_product_consumptions "
                "WHERE 商品编号 = %s",
                (TEST_PRODUCT_ID,),
            )
            cursor.execute(
                "DELETE FROM ongoing_table_fees "
                "WHERE consumption_id IN ("
                "SELECT id FROM ongoing_consumptions WHERE 桌台编号 = %s"
                ")",
                (TEST_TABLE_ID,),
            )
            cursor.execute(
                "DELETE FROM ongoing_consumptions WHERE 桌台编号 = %s",
                (TEST_TABLE_ID,),
            )
            cursor.execute(
                "DELETE FROM products WHERE id = %s",
                (TEST_PRODUCT_ID,),
            )
            cursor.execute(
                "DELETE FROM billiards_tables WHERE id = %s",
                (TEST_TABLE_ID,),
            )
        connection.commit()
    finally:
        connection.close()


def seed_test_data(stock: int) -> None:
    connection = open_database()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO billiards_tables (id, `每小时价格`) VALUES (%s, %s)",
                (TEST_TABLE_ID, 30),
            )
            cursor.execute(
                "INSERT INTO products "
                "(id, `商品名称`, `单价`, `库存`) VALUES (%s, %s, %s, %s)",
                (TEST_PRODUCT_ID, TEST_PRODUCT_NAME, 1, stock),
            )
        connection.commit()
    finally:
        connection.close()


def build_request_path() -> str:
    query = urlencode({
        "table_id": TEST_TABLE_ID,
        "product_id": TEST_PRODUCT_ID,
        "product_count": 1,
    })
    return f"/products/consumption?{query}"


def request_once(
    connection: http.client.HTTPConnection,
    path: str,
    idempotency_key: str,
    timeout: float,
) -> RequestOutcome:
    connection.timeout = timeout
    started = time.perf_counter()

    try:
        connection.request(
            "POST",
            path,
            body=b"",
            headers={"Idempotency-Key": idempotency_key},
        )
        response = connection.getresponse()
        body = response.read()
        http_status = response.status
        milliseconds = (time.perf_counter() - started) * 1000

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return RequestOutcome(
                False,
                milliseconds,
                "InvalidJson",
                http_status,
                body[:200].decode("utf-8", errors="replace"),
            )

        if http_status == 200 and payload.get("status") == "success":
            return RequestOutcome(True, milliseconds, http_status=http_status)

        return RequestOutcome(
            False,
            milliseconds,
            str(payload.get("error_type") or f"HTTP{http_status}"),
            http_status,
            str(payload.get("message") or "")[:300],
        )
    except Exception as error:  # noqa: BLE001 - 单个请求失败不能中断整轮压测
        milliseconds = (time.perf_counter() - started) * 1000
        return RequestOutcome(
            False,
            milliseconds,
            type(error).__name__,
            message=str(error)[:300],
        )


def run_warmup(
    host: str,
    port: int,
    warmup: int,
    round_token: str,
    timeout: float,
) -> list[RequestOutcome]:
    if warmup <= 0:
        return []

    path = build_request_path()
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    outcomes: list[RequestOutcome] = []
    try:
        for index in range(warmup):
            key = f"{IDEMPOTENCY_PREFIX}{round_token}-warmup-{index}"
            outcomes.append(request_once(connection, path, key, timeout))
    finally:
        connection.close()
    return outcomes


def run_worker(
    host: str,
    port: int,
    path: str,
    request_count: int,
    worker_index: int,
    round_token: str,
    timeout: float,
    barrier: threading.Barrier,
) -> WorkerResult:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    outcomes: list[RequestOutcome] = []

    try:
        barrier.wait()
        for index in range(request_count):
            key = (
                f"{IDEMPOTENCY_PREFIX}{round_token}-"
                f"w{worker_index}-{index}"
            )
            outcomes.append(request_once(connection, path, key, timeout))
    finally:
        connection.close()

    return WorkerResult(outcomes)


def load_verification(
    expected_successes: int,
    initial_stock: int,
) -> dict:
    connection = open_database()
    try:
        stock = scalar(
            connection,
            "SELECT `库存` FROM products WHERE id = %s",
            (TEST_PRODUCT_ID,),
        )
        ongoing_consumptions = scalar(
            connection,
            "SELECT COUNT(*) FROM ongoing_consumptions WHERE 桌台编号 = %s",
            (TEST_TABLE_ID,),
        )
        product_consumptions = scalar(
            connection,
            "SELECT COUNT(*) FROM ongoing_product_consumptions "
            "WHERE 商品编号 = %s",
            (TEST_PRODUCT_ID,),
        )
        idempotency_records = scalar(
            connection,
            "SELECT COUNT(*) FROM idempotency_records "
            "WHERE idempotency_key LIKE %s AND status = %s",
            (f"{IDEMPOTENCY_PREFIX}%", "completed"),
        )
        outbox_events = scalar(
            connection,
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE payload->>\"$.table_id\" = %s",
            (TEST_TABLE_ID,),
        )
    finally:
        connection.close()

    expected_stock = initial_stock - expected_successes
    checks = {
        "stock_matches": stock == expected_stock,
        "ongoing_consumptions_match": ongoing_consumptions == expected_successes,
        "product_consumptions_match": product_consumptions == expected_successes,
        "idempotency_records_match": idempotency_records == expected_successes,
        "outbox_events_match": outbox_events == expected_successes,
    }
    return {
        "expected_stock": expected_stock,
        "actual_stock": stock,
        "expected_rows": expected_successes,
        "ongoing_consumptions": ongoing_consumptions,
        "product_consumptions": product_consumptions,
        "idempotency_records": idempotency_records,
        "outbox_events": outbox_events,
        "all_checks_passed": all(checks.values()),
        "checks": checks,
    }


def run_round(
    host: str,
    port: int,
    workers: int,
    requests: int,
    warmup: int,
    timeout: float,
    round_index: int,
) -> dict:
    actual_workers = min(workers, requests)
    initial_stock = warmup + requests + 20
    round_token = uuid.uuid4().hex[:12]

    cleanup_test_data()
    seed_test_data(initial_stock)

    try:
        warmup_outcomes = run_warmup(
            host,
            port,
            warmup,
            round_token,
            timeout,
        )
        warmup_successes = sum(1 for item in warmup_outcomes if item.ok)
        if warmup and warmup_successes != warmup:
            return {
                "round_index": round_index,
                "workers": actual_workers,
                "requests": requests,
                "warmup": warmup,
                "warmup_successes": warmup_successes,
                "failed": True,
                "error": "预热请求失败，未进入正式压测",
                "warmup_errors": summarize_errors(warmup_outcomes),
            }

        path = build_request_path()
        barrier = threading.Barrier(actual_workers + 1)
        worker_results: list[WorkerResult] = [None] * actual_workers  # type: ignore[list-item]
        threads: list[threading.Thread] = []

        base_count = requests // actual_workers
        remainder = requests % actual_workers

        def thread_target(worker_index: int) -> None:
            count = base_count + (1 if worker_index < remainder else 0)
            worker_results[worker_index] = run_worker(
                host,
                port,
                path,
                count,
                worker_index,
                round_token,
                timeout,
                barrier,
            )

        for worker_index in range(actual_workers):
            thread = threading.Thread(
                target=thread_target,
                args=(worker_index,),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        started = time.perf_counter()
        barrier.wait()
        for thread in threads:
            thread.join()
        wall_seconds = time.perf_counter() - started

        outcomes = [
            outcome
            for result in worker_results
            for outcome in result.outcomes
        ]
        successes = sorted(
            outcome.milliseconds for outcome in outcomes if outcome.ok
        )
        failures = len(outcomes) - len(successes)
        total_successes = warmup_successes + len(successes)
        verification = load_verification(total_successes, initial_stock)

        if successes:
            p50 = percentile_nearest_rank(successes, 50)
            p95 = percentile_nearest_rank(successes, 95)
            p99 = percentile_nearest_rank(successes, 99)
            average = sum(successes) / len(successes)
        else:
            p50 = p95 = p99 = average = None

        return {
            "round_index": round_index,
            "workers": actual_workers,
            "requests": requests,
            "warmup": warmup,
            "warmup_successes": warmup_successes,
            "successes": len(successes),
            "failures": failures,
            "success_rate_percent": (
                len(successes) / len(outcomes) * 100 if outcomes else 0
            ),
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "average_ms": average,
            "minimum_ms": successes[0] if successes else None,
            "maximum_ms": successes[-1] if successes else None,
            "wall_seconds": wall_seconds,
            "throughput_requests_per_second": (
                len(outcomes) / wall_seconds if wall_seconds else None
            ),
            "success_throughput_per_second": (
                len(successes) / wall_seconds if wall_seconds else None
            ),
            "errors": summarize_errors(outcomes),
            "verification": verification,
            "failed": failures > 0 or not verification["all_checks_passed"],
        }
    finally:
        cleanup_test_data()


def summarize_errors(outcomes: list[RequestOutcome]) -> dict:
    counts: dict[str, int] = {}
    examples: list[dict] = []

    for outcome in outcomes:
        if outcome.ok:
            continue
        key = outcome.error_type or "UnknownError"
        counts[key] = counts.get(key, 0) + 1
        if len(examples) < 5:
            examples.append({
                "error_type": key,
                "http_status": outcome.http_status,
                "message": outcome.message,
            })

    return {"counts": counts, "examples": examples}


def print_round(result: dict) -> None:
    print(
        f"round={result['round_index']} workers={result['workers']} "
        f"requests={result['requests']} warmup={result['warmup']}"
    )
    if result.get("error"):
        print(f"  failed: {result['error']}")
        return

    print(
        f"  p50={result['p50_ms']:.2f}ms "
        f"p95={result['p95_ms']:.2f}ms "
        f"p99={result['p99_ms']:.2f}ms"
    )
    print(
        f"  avg={result['average_ms']:.2f}ms "
        f"min={result['minimum_ms']:.2f}ms "
        f"max={result['maximum_ms']:.2f}ms"
    )
    print(
        f"  success={result['successes']}/{result['requests']} "
        f"failed={result['failures']} "
        f"wall={result['wall_seconds']:.2f}s "
        f"throughput={result['throughput_requests_per_second']:.2f} req/s"
    )
    print(
        f"  db_verify={'PASS' if result['verification']['all_checks_passed'] else 'FAIL'} "
        f"stock={result['verification']['actual_stock']} "
        f"rows={result['verification']['product_consumptions']}"
    )
    if result["failures"]:
        print(f"  errors={json.dumps(result['errors'], ensure_ascii=False)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="压测加商品并扣库存写接口")
    parser.add_argument("--host", default="127.0.0.1", help="API 主机")
    parser.add_argument("--port", type=int, default=8011, help="API 端口")
    parser.add_argument(
        "--scenarios",
        default=DEFAULT_SCENARIOS,
        help="场景列表，格式 workers:requests，用逗号分隔",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每个场景重复轮数",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=10,
        help="每轮正式测试前的顺序预热请求数",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="单个 HTTP 请求超时秒数",
    )
    parser.add_argument("--label", default="stock-write-baseline")
    parser.add_argument(
        "--json-output",
        default="",
        help="可选的 JSON 结果输出路径",
    )
    args = parser.parse_args()

    if args.repeat <= 0:
        parser.error("--repeat 必须大于 0")
    if args.warmup < 0:
        parser.error("--warmup 不能小于 0")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    try:
        args.parsed_scenarios = parse_scenarios(args.scenarios)
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()
    database = str(DB_CONFIG.get("database", ""))
    if "test" not in database.lower():
        print(
            "错误：拒绝在非测试库运行，MYSQL_DATABASE 必须包含 test；"
            f"当前为 {database!r}",
            file=sys.stderr,
        )
        return 2

    rounds: list[dict] = []
    failed = False
    round_index = 0

    print(f"label={args.label}")
    print(f"target=http://{args.host}:{args.port}")
    print(f"database={database}")
    print(
        f"test_table={TEST_TABLE_ID} test_product={TEST_PRODUCT_ID} "
        f"warmup={args.warmup} repeat={args.repeat}"
    )

    try:
        for _repeat_index in range(args.repeat):
            for workers, requests in args.parsed_scenarios:
                round_index += 1
                result = run_round(
                    args.host,
                    args.port,
                    workers,
                    requests,
                    args.warmup,
                    args.timeout,
                    round_index,
                )
                rounds.append(result)
                print_round(result)
                failed = failed or bool(result.get("failed"))
    finally:
        cleanup_test_data()

    metadata = {
        "label": args.label,
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": f"http://{args.host}:{args.port}/products/consumption",
        "database": database,
        "test_table_id": TEST_TABLE_ID,
        "test_product_id": TEST_PRODUCT_ID,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "warmup_per_round": args.warmup,
        "repeat": args.repeat,
        "scenarios": [
            {"workers": workers, "requests": requests}
            for workers, requests in args.parsed_scenarios
        ],
        "notes": [
            "HTTP 压测包含 FastAPI/控制器/MySQL 事务/幂等记录/outbox 写入。",
            "每轮使用独立商品库存，请求数量小于库存，因此成功请求不应出现库存不足。",
            "本机合成压测结果不代表生产 SLA。",
        ],
    }
    document = {"metadata": metadata, "rounds": rounds}

    if args.json_output:
        output_path = Path(args.json_output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"json_output={output_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
