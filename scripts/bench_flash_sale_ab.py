"""热点抢购入口 A/B 压测：入口发牌延迟、队列排空和最终一致性核对。

脚本要求：
- API 已在测试数据库上启动；
- MYSQL_DATABASE 包含 test；
- ALLOW_WRITE_TESTS=1；
- 建议 API 为本轮压测新启动，确保桌台配置缓存从空开始。

脚本会创建专用测试桌台和商品，逐轮预热商品牌、发请求、等待 worker
排空、核对数据库，然后清理本轮数据。最后会删除专用商品和桌台。
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymysql


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.io_layer import DB_CONFIG  # noqa: E402


DEFAULT_SCENARIOS = "1:50,10:300,25:500,50:1000"


@dataclass
class RequestOutcome:
    accepted: bool
    rejected: bool
    milliseconds: float
    ticket_id: str | None = None
    error_type: str | None = None
    http_status: int | None = None


def percentile_nearest_rank(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("没有可计算的样本")
    if not 0 < percentile <= 100:
        raise ValueError("percentile 必须在 (0, 100] 范围内")
    rank = math.ceil(percentile / 100 * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def parse_scenarios(value: str) -> list[tuple[int, int]]:
    scenarios = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        workers_text, requests_text = item.split(":", 1)
        workers = int(workers_text)
        requests = int(requests_text)
        if workers <= 0 or requests <= 0:
            raise ValueError(f"场景参数必须大于 0：{item!r}")
        scenarios.append((workers, requests))
    if not scenarios:
        raise ValueError("至少需要一个压测场景")
    return scenarios


def database_connection():
    return pymysql.connect(**DB_CONFIG)


def scalar(sql, params=()):
    conn = database_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def call_json(
    host: str,
    port: int,
    method: str,
    path: str,
    payload=None,
    timeout: float = 30.0,
) -> tuple[int, dict]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        status = response.status
        if not response_body:
            return status, {}
        return status, json.loads(response_body.decode("utf-8"))
    finally:
        connection.close()


def admin_reset(host: str, port: int, timeout: float) -> dict:
    status, payload = call_json(
        host,
        port,
        "POST",
        "/flash-sale/admin/reset",
        timeout=timeout,
    )
    if status != 200 or payload.get("status") != "success":
        raise RuntimeError(f"热点池重置失败：HTTP {status} {payload}")
    return payload


def preheat_product(
    host: str,
    port: int,
    product_id: str,
    quantity: int,
    timeout: float,
) -> dict:
    status, payload = call_json(
        host,
        port,
        "POST",
        "/flash-sale/admin/products/preheat",
        {"product_id": product_id, "quantity": quantity},
        timeout=timeout,
    )
    if status != 200 or payload.get("status") != "success":
        raise RuntimeError(f"商品牌预热失败：HTTP {status} {payload}")
    return payload


def pool_status(host: str, port: int, timeout: float) -> dict:
    status, payload = call_json(
        host,
        port,
        "GET",
        "/flash-sale/admin/pools",
        timeout=timeout,
    )
    if status != 200 or payload.get("status") != "success":
        raise RuntimeError(f"读取热点池失败：HTTP {status} {payload}")
    return payload["data"]


def wait_for_drain(host: str, port: int, timeout: float) -> tuple[float, dict]:
    started = time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        status = pool_status(host, port, timeout)
        queue = status["queue"]
        ticket_statuses = status["ticket_statuses"]
        if (
            queue["pending"] == 0
            and queue["processing"] is False
            and ticket_statuses.get("queued", 0) == 0
        ):
            return time.perf_counter() - started, status
        time.sleep(0.05)
    raise TimeoutError("热点 worker 未能在超时时间内排空队列")


def request_once(
    connection: http.client.HTTPConnection,
    path: str,
    body: bytes,
    timeout: float,
) -> RequestOutcome:
    connection.timeout = timeout
    started = time.perf_counter()
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response_body = response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000
        status = response.status
        payload = json.loads(response_body.decode("utf-8"))

        if status == 200 and payload.get("status") == "success":
            return RequestOutcome(
                accepted=True,
                rejected=False,
                milliseconds=elapsed_ms,
                ticket_id=payload["data"]["ticket_id"],
                http_status=status,
            )

        return RequestOutcome(
            accepted=False,
            rejected=payload.get("error_type") == "FlashSaleSoldOut",
            milliseconds=elapsed_ms,
            error_type=str(payload.get("error_type") or f"HTTP{status}"),
            http_status=status,
        )
    except Exception as error:
        return RequestOutcome(
            accepted=False,
            rejected=False,
            milliseconds=(time.perf_counter() - started) * 1000,
            error_type=type(error).__name__,
        )


def warmup_requests(
    host: str,
    port: int,
    path: str,
    body: bytes,
    count: int,
    timeout: float,
) -> list[str]:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    ticket_ids = []
    try:
        for _ in range(count):
            outcome = request_once(connection, path, body, timeout)
            if not outcome.accepted:
                raise RuntimeError(f"预热请求失败：{outcome}")
            ticket_ids.append(outcome.ticket_id)
    finally:
        connection.close()
    return ticket_ids


def run_burst(
    host: str,
    port: int,
    path: str,
    body: bytes,
    workers: int,
    requests: int,
    timeout: float,
) -> tuple[list[RequestOutcome], float]:
    actual_workers = min(workers, requests)
    barrier = threading.Barrier(actual_workers + 1)
    outcomes = []
    outcomes_lock = threading.Lock()
    threads = []
    base_count = requests // actual_workers
    remainder = requests % actual_workers

    def run_worker(worker_index: int):
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        local_outcomes = []
        count = base_count + (1 if worker_index < remainder else 0)
        try:
            barrier.wait()
            for _ in range(count):
                local_outcomes.append(
                    request_once(connection, path, body, timeout)
                )
        finally:
            connection.close()
        with outcomes_lock:
            outcomes.extend(local_outcomes)

    for worker_index in range(actual_workers):
        thread = threading.Thread(
            target=run_worker,
            args=(worker_index,),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    started = time.perf_counter()
    barrier.wait()
    for thread in threads:
        thread.join()
    return outcomes, time.perf_counter() - started


def metrics(outcomes: list[RequestOutcome], wall_seconds: float) -> dict:
    latencies = sorted(outcome.milliseconds for outcome in outcomes)
    accepted = [outcome for outcome in outcomes if outcome.accepted]
    rejected = [outcome for outcome in outcomes if outcome.rejected]
    errored = [
        outcome for outcome in outcomes
        if not outcome.accepted and not outcome.rejected
    ]
    return {
        "requests": len(outcomes),
        "accepted": len(accepted),
        "rejected_sold_out": len(rejected),
        "errors": len(errored),
        "p50_ms": percentile_nearest_rank(latencies, 50),
        "p95_ms": percentile_nearest_rank(latencies, 95),
        "p99_ms": percentile_nearest_rank(latencies, 99),
        "average_ms": sum(latencies) / len(latencies),
        "minimum_ms": latencies[0],
        "maximum_ms": latencies[-1],
        "wall_seconds": wall_seconds,
        "throughput_requests_per_second": len(outcomes) / wall_seconds,
        "accepted_p50_ms": (
            percentile_nearest_rank(
                sorted(item.milliseconds for item in accepted),
                50,
            ) if accepted else None
        ),
        "rejected_p50_ms": (
            percentile_nearest_rank(
                sorted(item.milliseconds for item in rejected),
                50,
            ) if rejected else None
        ),
        "error_samples": [
            {
                "error_type": item.error_type,
                "http_status": item.http_status,
            }
            for item in errored[:5]
        ],
    }


def setup_environment(table_id: str, product_id: str):
    conn = database_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM products WHERE id = %s",
                (product_id,),
            )
            cursor.execute(
                "DELETE FROM billiards_tables WHERE id = %s",
                (table_id,),
            )
            cursor.execute(
                "INSERT INTO billiards_tables (id, `每小时价格`) VALUES (%s, %s)",
                (table_id, 30),
            )
            cursor.execute(
                "INSERT INTO products "
                "(id, `商品名称`, `单价`, `库存`) VALUES (%s, %s, %s, %s)",
                (product_id, "AB压测商品", 1, 0),
            )
        conn.commit()
    finally:
        conn.close()


def reset_round_database(
    table_id: str,
    product_id: str,
    idempotency_keys: list[str],
    initial_stock: int | None,
):
    conn = database_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM outbox_events "
                "WHERE payload->>\"$.table_id\" = %s",
                (table_id,),
            )
            cursor.execute(
                "DELETE FROM ongoing_product_consumptions "
                "WHERE 桌台编号 = %s",
                (table_id,),
            )
            cursor.execute(
                "DELETE FROM ongoing_table_fees "
                "WHERE consumption_id IN ("
                "SELECT id FROM ongoing_consumptions WHERE 桌台编号 = %s"
                ")",
                (table_id,),
            )
            cursor.execute(
                "DELETE FROM ongoing_consumptions WHERE 桌台编号 = %s",
                (table_id,),
            )

            if idempotency_keys:
                placeholders = ", ".join(["%s"] * len(idempotency_keys))
                cursor.execute(
                    "DELETE FROM idempotency_records "
                    f"WHERE idempotency_key IN ({placeholders})",
                    tuple(idempotency_keys),
                )

            if initial_stock is not None:
                cursor.execute(
                    "UPDATE products SET `库存` = %s WHERE id = %s",
                    (initial_stock, product_id),
                )
        conn.commit()
    finally:
        conn.close()


def verify_database(
    table_id: str,
    product_id: str,
    initial_stock: int,
    expected_successes: int,
    idempotency_keys: list[str],
) -> dict:
    stock = scalar(
        "SELECT `库存` FROM products WHERE id = %s",
        (product_id,),
    )
    consumption_rows = scalar(
        "SELECT COUNT(*) FROM ongoing_product_consumptions "
        "WHERE 桌台编号 = %s AND 商品编号 = %s",
        (table_id, product_id),
    )
    consumption_quantity = scalar(
        "SELECT COALESCE(SUM(`数量`), 0) "
        "FROM ongoing_product_consumptions "
        "WHERE 桌台编号 = %s AND 商品编号 = %s",
        (table_id, product_id),
    )
    outbox_rows = scalar(
        "SELECT COUNT(*) FROM outbox_events "
        "WHERE payload->>\"$.table_id\" = %s",
        (table_id,),
    )

    idempotency_rows = 0
    if idempotency_keys:
        placeholders = ", ".join(["%s"] * len(idempotency_keys))
        idempotency_rows = scalar(
            "SELECT COUNT(*) FROM idempotency_records "
            f"WHERE idempotency_key IN ({placeholders}) "
            "AND status = 'completed'",
            tuple(idempotency_keys),
        )

    stock = int(stock) if stock is not None else None
    consumption_rows = int(consumption_rows)
    consumption_quantity = int(consumption_quantity)
    outbox_rows = int(outbox_rows)
    idempotency_rows = int(idempotency_rows)
    expected_stock = initial_stock - expected_successes
    checks = {
        "stock_matches": stock == expected_stock,
        "consumption_rows_match": consumption_rows == expected_successes,
        "consumption_quantity_match": consumption_quantity == expected_successes,
        "idempotency_rows_match": idempotency_rows == expected_successes,
        "outbox_rows_match": outbox_rows == expected_successes,
    }
    return {
        "expected_successes": expected_successes,
        "expected_stock": expected_stock,
        "actual_stock": stock,
        "consumption_rows": consumption_rows,
        "consumption_quantity": consumption_quantity,
        "idempotency_rows": idempotency_rows,
        "outbox_rows": outbox_rows,
        "all_checks_passed": all(checks.values()),
        "checks": checks,
    }


def run_round(
    host: str,
    port: int,
    table_id: str,
    product_id: str,
    workers: int,
    requests: int,
    warmup: int,
    timeout: float,
    round_index: int,
) -> dict:
    admin_reset(host, port, timeout)
    initial_stock = warmup + requests + 20
    reset_round_database(table_id, product_id, [], initial_stock)
    preheat_product(
        host,
        port,
        product_id,
        warmup + requests,
        timeout,
    )

    path = "/flash-sale/products/consume"
    body = json.dumps({
        "table_id": table_id,
        "product_id": product_id,
    }, ensure_ascii=False).encode("utf-8")

    warmup_ticket_ids = warmup_requests(
        host,
        port,
        path,
        body,
        warmup,
        timeout,
    )
    if warmup:
        wait_for_drain(host, port, timeout)

    outcomes, burst_wall = run_burst(
        host,
        port,
        path,
        body,
        workers,
        requests,
        timeout,
    )
    burst_metrics = metrics(outcomes, burst_wall)
    after_burst_status = pool_status(host, port, timeout)
    drain_after_burst, final_status = wait_for_drain(
        host,
        port,
        timeout,
    )
    end_to_end_seconds = burst_wall + drain_after_burst

    accepted_ticket_ids = [
        outcome.ticket_id for outcome in outcomes
        if outcome.ticket_id is not None
    ]
    all_ticket_ids = warmup_ticket_ids + accepted_ticket_ids
    idempotency_keys = [
        f"flash:{ticket_id}" for ticket_id in all_ticket_ids
    ]
    expected_successes = warmup + requests
    status_counts = final_status["ticket_statuses"]
    verification = verify_database(
        table_id,
        product_id,
        initial_stock,
        expected_successes,
        idempotency_keys,
    )

    ticket_checks = {
        "all_tickets_succeeded": (
            status_counts.get("succeeded", 0) == expected_successes
        ),
        "no_failed_tickets": status_counts.get("failed", 0) == 0,
        "no_queued_tickets": status_counts.get("queued", 0) == 0,
    }

    result = {
        "round_index": round_index,
        "workers": min(workers, requests),
        "requests": requests,
        "warmup": warmup,
        "preheated_tokens": warmup + requests,
        "entry": burst_metrics,
        "queue_at_burst_end": after_burst_status["queue"],
        "drain_after_burst_seconds": drain_after_burst,
        "end_to_end_seconds": end_to_end_seconds,
        "worker_average_rate_per_second": (
            requests / end_to_end_seconds if end_to_end_seconds else None
        ),
        "ticket_statuses": status_counts,
        "ticket_checks": ticket_checks,
        "database_verification": verification,
        "passed": (
            burst_metrics["accepted"] == requests
            and burst_metrics["errors"] == 0
            and all(ticket_checks.values())
            and verification["all_checks_passed"]
        ),
    }

    reset_round_database(
        table_id,
        product_id,
        idempotency_keys,
        initial_stock,
    )
    admin_reset(host, port, timeout)
    return result


def run_sold_out_round(
    host: str,
    port: int,
    table_id: str,
    product_id: str,
    workers: int,
    requests: int,
    tokens: int,
    timeout: float,
) -> dict:
    admin_reset(host, port, timeout)
    initial_stock = tokens + 10
    reset_round_database(table_id, product_id, [], initial_stock)
    preheat_product(host, port, product_id, tokens, timeout)

    path = "/flash-sale/products/consume"
    body = json.dumps({
        "table_id": table_id,
        "product_id": product_id,
    }, ensure_ascii=False).encode("utf-8")
    outcomes, wall_seconds = run_burst(
        host,
        port,
        path,
        body,
        workers,
        requests,
        timeout,
    )
    burst_metrics = metrics(outcomes, wall_seconds)
    drain_after_burst, final_status = wait_for_drain(
        host,
        port,
        timeout,
    )
    ticket_ids = [
        outcome.ticket_id for outcome in outcomes
        if outcome.ticket_id is not None
    ]
    idempotency_keys = [f"flash:{ticket_id}" for ticket_id in ticket_ids]
    verification = verify_database(
        table_id,
        product_id,
        initial_stock,
        tokens,
        idempotency_keys,
    )
    status_counts = final_status["ticket_statuses"]
    checks = {
        "accepted_matches_tokens": burst_metrics["accepted"] == tokens,
        "rejected_matches_remainder": (
            burst_metrics["rejected_sold_out"] == requests - tokens
        ),
        "no_other_errors": burst_metrics["errors"] == 0,
        "tickets_succeeded": status_counts.get("succeeded", 0) == tokens,
        "tickets_not_failed": status_counts.get("failed", 0) == 0,
        "database_matches": verification["all_checks_passed"],
    }
    result = {
        "workers": min(workers, requests),
        "requests": requests,
        "tokens": tokens,
        "entry": burst_metrics,
        "drain_after_burst_seconds": drain_after_burst,
        "ticket_statuses": status_counts,
        "database_verification": verification,
        "checks": checks,
        "passed": all(checks.values()),
    }

    reset_round_database(
        table_id,
        product_id,
        idempotency_keys,
        initial_stock,
    )
    admin_reset(host, port, timeout)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="热点抢购入口 A/B 压测")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--label", default="flash-sale-ab")
    parser.add_argument("--json-output", default="")
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat 必须大于 0")
    if args.warmup < 0:
        parser.error("--warmup 不能小于 0")
    try:
        args.parsed_scenarios = parse_scenarios(args.scenarios)
    except ValueError as error:
        parser.error(str(error))
    return args


def print_round(result: dict):
    entry = result["entry"]
    print(
        f"round={result['round_index']} workers={result['workers']} "
        f"requests={result['requests']} tokens={result['preheated_tokens']}"
    )
    print(
        f"  entry p50={entry['p50_ms']:.2f}ms "
        f"p95={entry['p95_ms']:.2f}ms "
        f"p99={entry['p99_ms']:.2f}ms "
        f"throughput={entry['throughput_requests_per_second']:.2f} req/s"
    )
    print(
        f"  accepted={entry['accepted']}/{result['requests']} "
        f"errors={entry['errors']} "
        f"drain_after_burst={result['drain_after_burst_seconds']:.3f}s "
        f"worker_rate={result['worker_average_rate_per_second']:.2f} req/s"
    )
    print(
        f"  tickets={result['ticket_statuses']} "
        f"db_verify={'PASS' if result['database_verification']['all_checks_passed'] else 'FAIL'} "
        f"passed={result['passed']}"
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()
    database = str(DB_CONFIG.get("database", ""))
    if "test" not in database.lower():
        print(f"拒绝运行：MYSQL_DATABASE={database!r} 不是测试库", file=sys.stderr)
        return 2
    if os.environ.get("ALLOW_WRITE_TESTS") != "1":
        print("拒绝运行：请设置 ALLOW_WRITE_TESTS=1", file=sys.stderr)
        return 2

    suffix = uuid.uuid4().hex[:6].upper()
    table_id = f"FBT{suffix}"[:10]
    product_id = f"FBP{suffix}"[:10]
    rounds = []
    sold_out_result = None
    failed = False

    setup_environment(table_id, product_id)
    try:
        round_index = 0
        for _repeat_index in range(args.repeat):
            for workers, requests in args.parsed_scenarios:
                round_index += 1
                result = run_round(
                    args.host,
                    args.port,
                    table_id,
                    product_id,
                    workers,
                    requests,
                    args.warmup,
                    args.timeout,
                    round_index,
                )
                rounds.append(result)
                print_round(result)
                failed = failed or not result["passed"]

        sold_out_result = run_sold_out_round(
            args.host,
            args.port,
            table_id,
            product_id,
            workers=20,
            requests=20,
            tokens=5,
            timeout=args.timeout,
        )
        print(
            f"sold_out accepted={sold_out_result['entry']['accepted']} "
            f"rejected={sold_out_result['entry']['rejected_sold_out']} "
            f"p50={sold_out_result['entry']['p50_ms']:.2f}ms "
            f"passed={sold_out_result['passed']}"
        )
        failed = failed or not sold_out_result["passed"]
    finally:
        try:
            admin_reset(args.host, args.port, args.timeout)
        except Exception:
            pass
        reset_round_database(table_id, product_id, [], None)
        conn = database_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
                cursor.execute(
                    "DELETE FROM billiards_tables WHERE id = %s",
                    (table_id,),
                )
            conn.commit()
        finally:
            conn.close()

    document = {
        "metadata": {
            "label": args.label,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "target": f"http://{args.host}:{args.port}/flash-sale/products/consume",
            "database": database,
            "table_id": table_id,
            "product_id": product_id,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "scenarios": [
                {"workers": workers, "requests": requests}
                for workers, requests in args.parsed_scenarios
            ],
            "repeat": args.repeat,
            "warmup_per_round": args.warmup,
            "sold_out_scenario": {
                "workers": 20,
                "requests": 20,
                "tokens": 5,
            },
            "notes": [
                "入口延迟只覆盖 HTTP 接单与内存发牌，不等待数据库落库。",
                "drain_after_burst_seconds 从正式请求全部返回后开始计时。",
                "worker_average_rate_per_second 使用正式请求数 / 入口至排空总耗时。",
                "每轮数据库检查商品库存、消费行、消费数量、幂等行和 outbox 行。",
            ],
        },
        "rounds": rounds,
        "sold_out": sold_out_result,
    }

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
