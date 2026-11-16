"""热点抢购的内存发牌引擎与单线程任务传送带。

本模块只负责进程内发牌、排队、状态追踪和失败退牌，不直接访问 MySQL。
真正的库存校验、桌台校验和事务落库由外部注入的 task_handler 完成。

边界：
- 发牌池与 local_cache.TTLCache 完全独立，不自动过期；
- 只适用于单进程热点抢购，多进程部署需要替换为 Redis 等共享发牌中心；
- HTTP 抢到牌子只代表获得一次落库尝试资格，最终状态以 task_handler 为准。
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


PRODUCT_KIND = "product"
TABLE_KIND = "table"

TICKET_QUEUED = "queued"
TICKET_SUCCEEDED = "succeeded"
TICKET_FAILED = "failed"


@dataclass(frozen=True)
class FlashToken:
    kind: str
    resource_id: str
    token_id: str


@dataclass
class FlashTask:
    ticket_id: str
    kind: str
    resource_id: str
    token: FlashToken
    payload: dict = field(default_factory=dict)


@dataclass
class TicketState:
    ticket_id: str
    kind: str
    resource_id: str
    status: str = TICKET_QUEUED
    created_at: int = field(default_factory=lambda: int(time.time()))
    finished_at: int | None = None
    attempts: int = 0
    error: str | None = None
    result: Any = None

    def as_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "kind": self.kind,
            "resource_id": self.resource_id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "attempts": self.attempts,
            "error": self.error,
            "result": copy.deepcopy(self.result),
        }


TaskHandler = Callable[[FlashTask], dict]


def _result(status: str, error_type, data, message: str) -> dict:
    return {
        "status": status,
        "error_type": error_type,
        "data": data,
        "message": message,
    }


class FlashSaleEngine:
    """线程安全的内存发牌池和单 worker 任务队列。"""

    def __init__(
        self,
        task_handler: TaskHandler | None = None,
        *,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
    ):
        self._task_handler = task_handler
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

        self._product_tokens: dict[str, deque[FlashToken]] = {}

        self._table_tokens: deque[FlashToken] = deque()
        self._available_table_ids: set[str] = set()
        self._managed_table_ids: set[str] = set()
        self._occupied_table_ids: set[str] = set()

        self._task_queue: deque[FlashTask] = deque()
        self._tickets: dict[str, TicketState] = {}
        self._processing = False

        self._worker: threading.Thread | None = None
        self._stop_requested = False

    # ==================== worker 生命周期 ====================

    def set_task_handler(self, task_handler: TaskHandler) -> None:
        with self._lock:
            self._task_handler = task_handler

    def start(self) -> bool:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return True
            if self._task_handler is None:
                return False

            self._stop_requested = False
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="flash-sale-worker",
                daemon=True,
            )
            self._worker.start()
            return True

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> bool:
        if drain and not self.wait_until_idle(timeout=timeout):
            return False

        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
            worker = self._worker

        if worker is not None:
            worker.join(timeout=timeout)

        with self._lock:
            if self._worker is not None and not self._worker.is_alive():
                self._worker = None
        return worker is None or not worker.is_alive()

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._task_queue or self._processing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._task_queue and not self._stop_requested:
                    self._condition.wait()

                if self._stop_requested:
                    return

                task = self._task_queue.popleft()
                self._processing = True

            try:
                self._execute_task(task)
            except Exception as error:
                # worker 必须继续消化后续任务，单个 ticket 的意外错误不能杀死线程。
                try:
                    self._finish_ticket(
                        task,
                        success=False,
                        error=f"WorkerInternalError: {error}",
                        result=None,
                        refund_token=True,
                    )
                except Exception:
                    pass
            finally:
                with self._condition:
                    self._processing = False
                    self._condition.notify_all()

    # ==================== 预热 / 初始化 ====================

    def preheat_product(self, product_id: str, quantity: int) -> dict:
        normalized_id = str(product_id or "").strip()
        if not normalized_id:
            return _result(
                "error",
                "ProductIdFormatError",
                None,
                "商品编号不能为空",
            )
        if type(quantity) is not int or quantity < 0:
            return _result(
                "error",
                "FlashSaleQuantityInvalid",
                None,
                "抢购库存数量必须是非负整数",
            )

        with self._condition:
            self._product_tokens[normalized_id] = deque(
                FlashToken(
                    kind=PRODUCT_KIND,
                    resource_id=normalized_id,
                    token_id=f"{normalized_id}:{index}:{uuid.uuid4().hex}",
                )
                for index in range(quantity)
            )
            self._condition.notify_all()

        return _result(
            "success",
            None,
            {
                "product_id": normalized_id,
                "available": quantity,
            },
            f"商品 {normalized_id} 已预热 {quantity} 张单件牌子",
        )

    def preheat_tables(self, table_ids: list[str]) -> dict:
        normalized_ids = []
        seen = set()
        for table_id in table_ids or []:
            normalized = str(table_id or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                normalized_ids.append(normalized)

        with self._condition:
            self._table_tokens = deque(
                FlashToken(
                    kind=TABLE_KIND,
                    resource_id=table_id,
                    token_id=f"{table_id}:{uuid.uuid4().hex}",
                )
                for table_id in normalized_ids
            )
            self._available_table_ids = set(normalized_ids)
            self._managed_table_ids = set(normalized_ids)
            self._occupied_table_ids.difference_update(normalized_ids)
            self._condition.notify_all()

        return _result(
            "success",
            None,
            {
                "table_ids": normalized_ids,
                "available": len(normalized_ids),
            },
            f"桌台抢开台池已预热 {len(normalized_ids)} 张牌子",
        )

    # ==================== 发牌 ====================

    def acquire_product_ticket(
        self,
        table_id: str,
        product_id: str,
    ) -> dict:
        table_id = str(table_id or "").strip()
        product_id = str(product_id or "").strip()
        if not table_id:
            return _result(
                "error",
                "TableIdEmptyError",
                None,
                "桌号不能为空",
            )
        if not product_id:
            return _result(
                "error",
                "ProductIdFormatError",
                None,
                "商品编号不能为空",
            )
        if not self.start():
            return _result(
                "error",
                "FlashSaleEngineNotReady",
                None,
                "热点抢购 worker 尚未配置",
            )

        with self._condition:
            tokens = self._product_tokens.get(product_id)
            if not tokens:
                return _result(
                    "error",
                    "FlashSaleSoldOut",
                    None,
                    f"商品 {product_id} 已抢完",
                )

            token = tokens.popleft()
            ticket = self._create_ticket_locked(
                kind=PRODUCT_KIND,
                resource_id=product_id,
            )
            task = FlashTask(
                ticket_id=ticket.ticket_id,
                kind=PRODUCT_KIND,
                resource_id=product_id,
                token=token,
                payload={
                    "table_id": table_id,
                    "product_id": product_id,
                    "quantity": 1,
                },
            )
            self._task_queue.append(task)
            self._condition.notify_all()

        return _result(
            "success",
            None,
            {
                "ticket_id": ticket.ticket_id,
                "status": TICKET_QUEUED,
            },
            "抢购资格已获得，正在异步落库",
        )

    def acquire_table_ticket(self, table_id: str) -> dict:
        table_id = str(table_id or "").strip()
        if not table_id:
            return _result(
                "error",
                "TableIdEmptyError",
                None,
                "桌号不能为空",
            )
        if not self.start():
            return _result(
                "error",
                "FlashSaleEngineNotReady",
                None,
                "热点抢购 worker 尚未配置",
            )

        with self._condition:
            if (
                table_id not in self._managed_table_ids
                or table_id not in self._available_table_ids
            ):
                return _result(
                    "error",
                    "TableOccupied",
                    None,
                    f"{table_id} 号桌已被占用",
                )

            self._available_table_ids.discard(table_id)
            token = self._remove_table_token_locked(table_id)
            if token is None:
                return _result(
                    "error",
                    "TableOccupied",
                    None,
                    f"{table_id} 号桌已被占用",
                )
            ticket = self._create_ticket_locked(
                kind=TABLE_KIND,
                resource_id=table_id,
            )
            task = FlashTask(
                ticket_id=ticket.ticket_id,
                kind=TABLE_KIND,
                resource_id=table_id,
                token=token,
                payload={"table_id": table_id},
            )
            self._task_queue.append(task)
            self._condition.notify_all()

        return _result(
            "success",
            None,
            {
                "ticket_id": ticket.ticket_id,
                "status": TICKET_QUEUED,
            },
            "桌台抢开台资格已获得，正在异步落库",
        )

    # ==================== 老路状态同步 ====================

    def on_table_opened(self, table_id: str) -> bool:
        """老路或热路开台提交成功后，从可用池摘牌并标记占用。"""
        table_id = str(table_id or "").strip()
        if not table_id:
            return False

        try:
            with self._condition:
                if table_id not in self._managed_table_ids:
                    return False
                self._available_table_ids.discard(table_id)
                self._remove_table_token_locked(table_id)
                self._occupied_table_ids.add(table_id)
                self._condition.notify_all()
                return True
        except Exception:
            return False

    def on_table_closed(self, table_id: str) -> bool:
        """老路或热路关台提交成功后，将牌子补回已管理的桌台池尾部。"""
        table_id = str(table_id or "").strip()
        if not table_id:
            return False

        try:
            with self._condition:
                if table_id not in self._managed_table_ids:
                    return False

                self._occupied_table_ids.discard(table_id)
                if table_id not in self._available_table_ids:
                    self._table_tokens.append(FlashToken(
                        kind=TABLE_KIND,
                        resource_id=table_id,
                        token_id=f"{table_id}:{uuid.uuid4().hex}",
                    ))
                    self._available_table_ids.add(table_id)
                self._condition.notify_all()
                return True
        except Exception:
            return False

    def on_product_sold(self, product_id: str, quantity: int) -> int:
        """老路成功卖出商品后，从热点池尾部移除对应数量的空闲牌子。"""
        product_id = str(product_id or "").strip()
        if not product_id or type(quantity) is not int or quantity <= 0:
            return 0

        removed = 0
        try:
            with self._condition:
                tokens = self._product_tokens.get(product_id)
                if tokens is None:
                    return 0
                while tokens and removed < quantity:
                    tokens.pop()
                    removed += 1
                self._condition.notify_all()
        except Exception:
            return removed
        return removed

    # ==================== 查询与测试管理 ====================

    def get_ticket(self, ticket_id: str) -> dict:
        ticket_id = str(ticket_id or "").strip()
        try:
            with self._lock:
                ticket = self._tickets.get(ticket_id)
                if ticket is None:
                    return _result(
                        "error",
                        "FlashTicketNotFound",
                        None,
                        f"抢购单号不存在：{ticket_id}",
                    )
                return _result(
                    "success",
                    None,
                    ticket.as_dict(),
                    "抢购状态读取成功",
                )
        except Exception as error:
            return _result(
                "error",
                "FlashTicketReadFailed",
                None,
                f"抢购状态读取失败：{error}",
            )

    def get_pool_status(self) -> dict:
        with self._lock:
            ticket_status_counts = {
                TICKET_QUEUED: 0,
                TICKET_SUCCEEDED: 0,
                TICKET_FAILED: 0,
            }
            for ticket in self._tickets.values():
                ticket_status_counts[ticket.status] = (
                    ticket_status_counts.get(ticket.status, 0) + 1
                )

            return _result(
                "success",
                None,
                {
                    "products": {
                        product_id: len(tokens)
                        for product_id, tokens in self._product_tokens.items()
                    },
                    "tables": {
                        "managed": sorted(self._managed_table_ids),
                        "available": sorted(self._available_table_ids),
                        "occupied": sorted(self._occupied_table_ids),
                        "available_count": len(self._available_table_ids),
                    },
                    "queue": {
                        "pending": len(self._task_queue),
                        "processing": self._processing,
                    },
                    "tickets": len(self._tickets),
                    "ticket_statuses": ticket_status_counts,
                },
                "热点抢购池状态读取成功",
            )

    def reset(self) -> dict:
        with self._condition:
            if self._task_queue or self._processing:
                return _result(
                    "error",
                    "FlashSalePoolBusy",
                    None,
                    "任务队列尚未清空，拒绝重置热点抢购池",
                )
            self._product_tokens.clear()
            self._table_tokens.clear()
            self._available_table_ids.clear()
            self._managed_table_ids.clear()
            self._occupied_table_ids.clear()
            self._tickets.clear()
            self._condition.notify_all()
        return _result("success", None, None, "热点抢购池已重置")

    # ==================== 内部实现 ====================

    def _create_ticket_locked(self, kind: str, resource_id: str) -> TicketState:
        ticket_id = f"tkt_{uuid.uuid4().hex}"
        ticket = TicketState(
            ticket_id=ticket_id,
            kind=kind,
            resource_id=resource_id,
        )
        self._tickets[ticket_id] = ticket
        return ticket

    def _remove_table_token_locked(
        self,
        table_id: str,
    ) -> FlashToken | None:
        for index, token in enumerate(self._table_tokens):
            if token.resource_id == table_id:
                del self._table_tokens[index]
                return token
        return None

    def _execute_task(self, task: FlashTask) -> None:
        attempts = 0

        while True:
            attempts += 1
            self._update_attempts(task.ticket_id, attempts)

            try:
                outcome = self._task_handler(task)
                if not isinstance(outcome, dict):
                    raise TypeError("task_handler 必须返回字典")
            except Exception as error:
                outcome = {
                    "status": "error",
                    "retryable": True,
                    "error": f"{type(error).__name__}: {error}",
                }

            if outcome.get("status") == "success":
                self._finish_ticket(
                    task,
                    success=True,
                    error=None,
                    result=outcome.get("result"),
                )
                return

            retryable = outcome.get("retryable") is True
            if retryable and attempts <= self._max_retries:
                if self._retry_backoff_seconds > 0:
                    time.sleep(
                        self._retry_backoff_seconds * attempts
                    )
                continue

            self._finish_ticket(
                task,
                success=False,
                error=str(outcome.get("error") or "异步落库失败"),
                result=outcome.get("result"),
                refund_token=outcome.get("refund_token") is not False,
            )
            return

    def _update_attempts(self, ticket_id: str, attempts: int) -> None:
        with self._condition:
            ticket = self._tickets.get(ticket_id)
            if ticket is not None:
                ticket.attempts = attempts
            self._condition.notify_all()

    def _finish_ticket(
        self,
        task: FlashTask,
        *,
        success: bool,
        error: str | None,
        result: Any,
        refund_token: bool = True,
    ) -> None:
        with self._condition:
            ticket = self._tickets.get(task.ticket_id)
            if ticket is not None:
                ticket.status = (
                    TICKET_SUCCEEDED if success else TICKET_FAILED
                )
                ticket.finished_at = int(time.time())
                ticket.error = error
                try:
                    ticket.result = copy.deepcopy(result)
                except Exception:
                    ticket.result = repr(result)

            if not success and refund_token:
                self._refund_token_locked(task)

            self._condition.notify_all()

    def _refund_token_locked(self, task: FlashTask) -> None:
        if task.kind == PRODUCT_KIND:
            tokens = self._product_tokens.get(task.resource_id)
            if tokens is not None:
                tokens.append(task.token)
            return

        if (
            task.kind == TABLE_KIND
            and task.resource_id in self._managed_table_ids
            and task.resource_id not in self._occupied_table_ids
            and task.resource_id not in self._available_table_ids
        ):
            self._table_tokens.append(task.token)
            self._available_table_ids.add(task.resource_id)


# 进程级默认实例。业务 HTTP 层会注入真正调用 controller 的 worker handler，
# controller 也会引用本实例同步老路开台/关台的内存牌状态。
flash_sale_engine = FlashSaleEngine()
