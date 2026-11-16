"""热点抢购 worker 与现有 controller 写库逻辑的接线层。"""

from __future__ import annotations

from . import controller_layer
from .flash_sale_queue import (
    PRODUCT_KIND,
    TABLE_KIND,
    FlashTask,
    flash_sale_engine,
)


RETRYABLE_DB_ERROR_TYPES = {
    "OperationalError",
    "InterfaceError",
    "InternalError",
    "DatabaseError",
    "TimeoutError",
}

TABLE_NO_REFUND_ERROR_TYPES = {
    "TableOccupied",
    "TableNotFound",
}


def _outcome_from_result(result: dict, *, refund_token: bool = True) -> dict:
    if result.get("status") == "success":
        return {
            "status": "success",
            "retryable": False,
            "result": result,
        }

    error_type = str(result.get("error_type") or "UnknownError")
    return {
        "status": "error",
        "retryable": error_type in RETRYABLE_DB_ERROR_TYPES,
        "refund_token": refund_token,
        "error": f"{error_type}: {result.get('message') or '异步落库失败'}",
        "result": result,
    }


def _execute_flash_task(task: FlashTask) -> dict:
    if task.kind == PRODUCT_KIND:
        result = controller_layer.three_add_product_consumption(
            task.payload["table_id"],
            task.payload["product_id"],
            task.payload["quantity"],
            idempotency_key=f"flash:{task.ticket_id}",
            source="flash-sale",
        )
        # three_add_product_consumption 成功后会在 commit 之后失效商品缓存。
        return _outcome_from_result(result, refund_token=True)

    if task.kind == TABLE_KIND:
        result = controller_layer.three_open_table(
            task.payload["table_id"],
            source="flash-sale",
        )
        # three_open_table 成功后会在 commit 之后失效桌台缓存，并通过
        # flash_sale_engine.on_table_opened 标记该桌台已占用。
        return _outcome_from_result(
            result,
            refund_token=(
                result.get("error_type")
                not in TABLE_NO_REFUND_ERROR_TYPES
            ),
        )

    return {
        "status": "error",
        "retryable": False,
        "refund_token": True,
        "error": f"未知热点任务类型：{task.kind}",
        "result": None,
    }


def ensure_flash_sale_worker() -> bool:
    flash_sale_engine.set_task_handler(_execute_flash_task)
    return flash_sale_engine.start()


def stop_flash_sale_worker(*, drain: bool = True, timeout: float = 5.0) -> bool:
    return flash_sale_engine.stop(drain=drain, timeout=timeout)


def preheat_product(product_id: str, quantity: int) -> dict:
    return flash_sale_engine.preheat_product(product_id, quantity)


def preheat_tables(table_ids: list[str]) -> dict:
    return flash_sale_engine.preheat_tables(table_ids)


def acquire_product_ticket(table_id: str, product_id: str) -> dict:
    ensure_flash_sale_worker()
    return flash_sale_engine.acquire_product_ticket(table_id, product_id)


def acquire_table_ticket(table_id: str) -> dict:
    ensure_flash_sale_worker()
    return flash_sale_engine.acquire_table_ticket(table_id)


def get_ticket(ticket_id: str) -> dict:
    return flash_sale_engine.get_ticket(ticket_id)


def get_pool_status() -> dict:
    return flash_sale_engine.get_pool_status()


def reset_flash_sale_pool() -> dict:
    return flash_sale_engine.reset()
