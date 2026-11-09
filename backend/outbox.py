# -*- coding: utf-8 -*-
"""
事务性 outbox + 异步 worker 的核心实现。

边界：
- 主业务事务只负责写业务表 + 写一条 outbox 事件（待办便签）。
- worker 从主库领取 pending 事件。
- 真正写备份时，worker 必须新建到 billiards_backup 的连接，
  绝不能复用主库的 connection / cursor。
- 备份写入按 consumption_id 幂等，重复处理不会产生重复行。
"""

import json
import os
import socket
import time

from .io_layer import (
    LOG_DIR,
    one_convert_mysql_error,
    one_get_backup_mysql_connection,
    one_get_current_time,
    one_get_mysql_connection,
    one_insert_core_backup_product_to_mysql,
    one_insert_core_backup_to_mysql,
)


# ==================== 事件类型与状态 ====================

EVENT_CORE_BACKUP = "core_backup"
EVENT_CORE_BACKUP_PRODUCT = "core_backup_product"

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_DEAD = "dead"


# ==================== 可配置参数 ====================

OUTBOX_MAX_RETRIES = max(1, int(os.environ.get("OUTBOX_MAX_RETRIES", "5")))
OUTBOX_POLL_INTERVAL = float(os.environ.get("OUTBOX_POLL_INTERVAL", "1.0"))
OUTBOX_BATCH_SIZE = max(1, int(os.environ.get("OUTBOX_BATCH_SIZE", "20")))
OUTBOX_LOCK_TIMEOUT = max(10, int(os.environ.get("OUTBOX_LOCK_TIMEOUT", "300")))
OUTBOX_WORKER_ID = "%s:%s" % (socket.gethostname(), os.getpid())


# ==================== 事件载荷组装 ====================

def build_table_fee_event_payload(backup_record: dict) -> dict:
    # 把业务层准备好的台位费备份数据转成 JSON 安全的 outbox 载荷。
    return {
        "consumption_id": str(backup_record["消费记录编号"]),
        "table_id": str(backup_record["桌子号码"]),
        "start_time": int(backup_record["开台时间"]),
        "close_time": int(backup_record["关台时间"]),
    }


def build_product_event_payload(backup_record: dict) -> dict:
    # 把业务层准备好的商品备份数据转成 JSON 安全的 outbox 载荷。
    # 金额是 Decimal，写入 JSON 前显式转成字符串，避免 JSON 序列化失败。
    return {
        "consumption_id": str(backup_record["消费记录编号"]),
        "table_id": str(backup_record["桌子号码"]),
        "product_id": str(backup_record["商品编号"]),
        "product_name": str(backup_record["商品名称"]),
        "unit_price": str(backup_record["单价"]),
        "quantity": int(backup_record["数量"]),
        "amount": str(backup_record["金额"]),
        "created_at": int(backup_record["创建时间"]),
    }


# ==================== 主库 outbox 读写 ====================

def one_insert_outbox_event(cursor, event_type: str, payload: dict, created_at: int) -> dict:
    # 作用：在调用方的主业务事务里写入一条 outbox 事件。
    # 接收：主库事务 cursor、事件类型、事件载荷、创建时间。
    # 返回：四键结果字典；data 为 event_id。
    # 说明：本函数不 commit，事务由调用方的 one_execute_in_transaction 控制。
    try:
        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        return {
            "status": "error",
            "error_type": "OutboxPayloadFormatError",
            "data": None,
            "message": f"outbox 事件内容无法序列化：{error}"
        }

    try:
        cursor.execute(
            "INSERT INTO outbox_events "
            "(event_type, payload, status, retry_count, next_retry_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, 0, %s, %s, %s)",
            (
                event_type,
                payload_text,
                STATUS_PENDING,
                created_at,
                created_at,
                created_at
            )
        )
        return {
            "status": "success",
            "error_type": None,
            "data": cursor.lastrowid,
            "message": f"outbox 事件 {event_type} 写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "写入 outbox 事件")


def one_claim_outbox_events(limit: int = None, worker_id: str = None) -> dict:
    # 从主库领取一批 pending 事件，并标记为 processing。
    # 使用 FOR UPDATE SKIP LOCKED，多个 worker 同时运行时不会抢到同一行。
    limit = OUTBOX_BATCH_SIZE if limit is None else max(1, int(limit))
    worker_id = (worker_id or OUTBOX_WORKER_ID)[:80]
    now = one_get_current_time()
    connection = None

    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT event_id, event_type, payload, retry_count "
            "FROM outbox_events "
            "WHERE status = %s AND next_retry_at <= %s "
            "ORDER BY event_id "
            "LIMIT %s "
            "FOR UPDATE SKIP LOCKED",
            (STATUS_PENDING, now, limit)
        )
        rows = cursor.fetchall()

        if not rows:
            connection.commit()
            return {
                "status": "success",
                "error_type": None,
                "data": [],
                "message": "没有待处理的 outbox 事件"
            }

        event_ids = [row[0] for row in rows]
        placeholders = ", ".join(["%s"] * len(event_ids))
        cursor.execute(
            "UPDATE outbox_events "
            "SET status = %s, locked_at = %s, locked_by = %s, updated_at = %s "
            f"WHERE event_id IN ({placeholders})",
            (STATUS_PROCESSING, now, worker_id, now, *event_ids)
        )
        connection.commit()

        events = []
        for event_id, event_type, payload, retry_count in rows:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            events.append({
                "event_id": event_id,
                "event_type": event_type,
                "payload": json.loads(payload),
                "retry_count": int(retry_count),
            })

        return {
            "status": "success",
            "error_type": None,
            "data": events,
            "message": f"领取 {len(events)} 条 outbox 事件"
        }
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "领取 outbox 事件")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_mark_outbox_event_done(event_id: int) -> dict:
    connection = None
    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE outbox_events "
            "SET status = %s, locked_at = NULL, locked_by = NULL, "
            "    last_error = NULL, updated_at = %s "
            "WHERE event_id = %s",
            (STATUS_DONE, one_get_current_time(), event_id)
        )
        connection.commit()
        return {
            "status": "success",
            "error_type": None,
            "data": event_id,
            "message": f"outbox 事件 {event_id} 已标记 done"
        }
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "标记 outbox 事件完成")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_mark_outbox_event_failed(
    event_id: int,
    retry_count: int,
    error_text: str,
    max_retries: int = None
) -> dict:
    # 处理失败：重试次数 +1；未超过上限时回到 pending 并按指数退避，
    # 超过上限时标记 dead，等待人工/脚本重新入队。
    max_retries = OUTBOX_MAX_RETRIES if max_retries is None else max(1, int(max_retries))
    new_retry = int(retry_count) + 1
    now = one_get_current_time()

    if new_retry >= max_retries:
        next_status = STATUS_DEAD
        next_retry_at = now
    else:
        next_status = STATUS_PENDING
        next_retry_at = now + min(2 ** new_retry, 60)

    error_text = str(error_text)[:500]
    connection = None

    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE outbox_events "
            "SET status = %s, retry_count = %s, next_retry_at = %s, "
            "    last_error = %s, locked_at = NULL, locked_by = NULL, updated_at = %s "
            "WHERE event_id = %s",
            (next_status, new_retry, next_retry_at, error_text, now, event_id)
        )
        connection.commit()
        return {
            "status": "success",
            "error_type": None,
            "data": next_status,
            "message": f"outbox 事件 {event_id} 处理失败，状态={next_status}"
        }
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "标记 outbox 事件失败")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_requeue_stale_outbox_events(stale_before: int = None) -> dict:
    # worker 崩溃后，processing 事件可能一直卡住。
    # 超过锁定时间的事件重新放回 pending；处理函数幂等，允许重放。
    if stale_before is None:
        stale_before = one_get_current_time() - OUTBOX_LOCK_TIMEOUT

    connection = None
    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE outbox_events "
            "SET status = %s, locked_at = NULL, locked_by = NULL, updated_at = %s "
            "WHERE status = %s AND (locked_at IS NULL OR locked_at < %s)",
            (STATUS_PENDING, one_get_current_time(), STATUS_PROCESSING, stale_before)
        )
        count = cursor.rowcount
        connection.commit()
        return {
            "status": "success",
            "error_type": None,
            "data": count,
            "message": f"回收 {count} 条超时 processing 事件"
        }
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "回收超时 outbox 事件")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_get_outbox_stats() -> dict:
    connection = None
    try:
        connection = one_get_mysql_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT status, COUNT(*) FROM outbox_events GROUP BY status"
        )
        counts = {
            STATUS_PENDING: 0,
            STATUS_PROCESSING: 0,
            STATUS_DONE: 0,
            STATUS_DEAD: 0,
        }
        for status, count in cursor.fetchall():
            counts[status] = int(count)

        cursor.execute(
            "SELECT MIN(created_at) FROM outbox_events WHERE status = %s",
            (STATUS_PENDING,)
        )
        oldest_pending_at = cursor.fetchone()[0]

        return {
            "status": "success",
            "error_type": None,
            "data": {
                "counts": counts,
                "oldest_pending_at": oldest_pending_at,
            },
            "message": "outbox 统计读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取 outbox 统计")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_list_dead_outbox_events(limit: int = 50) -> dict:
    limit = max(1, int(limit))
    connection = None
    try:
        connection = one_get_mysql_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT event_id, event_type, retry_count, last_error, created_at "
            "FROM outbox_events "
            "WHERE status = %s "
            "ORDER BY event_id DESC "
            "LIMIT %s",
            (STATUS_DEAD, limit)
        )
        rows = cursor.fetchall()
        events = [
            {
                "event_id": row[0],
                "event_type": row[1],
                "retry_count": row[2],
                "last_error": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]
        return {
            "status": "success",
            "error_type": None,
            "data": events,
            "message": f"读取 {len(events)} 条 dead 事件"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取 dead outbox 事件")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_requeue_dead_outbox_events(event_id: int = None) -> dict:
    # 把 dead 事件重新放回 pending。
    # event_id 为空时重排全部 dead；指定时只重排一条。
    now = one_get_current_time()
    connection = None
    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()
        if event_id is None:
            cursor.execute(
                "UPDATE outbox_events "
                "SET status = %s, retry_count = 0, next_retry_at = %s, "
                "    last_error = NULL, locked_at = NULL, locked_by = NULL, updated_at = %s "
                "WHERE status = %s",
                (STATUS_PENDING, now, now, STATUS_DEAD)
            )
        else:
            cursor.execute(
                "UPDATE outbox_events "
                "SET status = %s, retry_count = 0, next_retry_at = %s, "
                "    last_error = NULL, locked_at = NULL, locked_by = NULL, updated_at = %s "
                "WHERE status = %s AND event_id = %s",
                (STATUS_PENDING, now, now, STATUS_DEAD, event_id)
            )
        count = cursor.rowcount
        connection.commit()
        return {
            "status": "success",
            "error_type": None,
            "data": count,
            "message": f"重新入队 {count} 条 dead 事件"
        }
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "重新入队 dead outbox 事件")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


# ==================== 备份处理器：必须新建备份库连接 ====================

def _write_table_fee_backup(payload: dict) -> None:
    # 注意：这里必须新建到 billiards_backup 的连接。
    # 不允许接收或复用主库的 connection / cursor。
    connection = one_get_backup_mysql_connection()
    try:
        connection.begin()
        cursor = connection.cursor()
        result = one_insert_core_backup_to_mysql(
            cursor,
            payload["consumption_id"],
            payload["table_id"],
            payload["start_time"],
            payload["close_time"]
        )
        if result["status"] != "success":
            connection.rollback()
            raise RuntimeError(result["message"])
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _write_product_backup(payload: dict) -> None:
    # 注意：这里必须新建到 billiards_backup 的连接。
    # 不允许接收或复用主库的 connection / cursor。
    connection = one_get_backup_mysql_connection()
    try:
        connection.begin()
        cursor = connection.cursor()
        result = one_insert_core_backup_product_to_mysql(
            cursor,
            payload["consumption_id"],
            payload["table_id"],
            payload["product_id"],
            payload["product_name"],
            payload["unit_price"],
            payload["quantity"],
            payload["amount"],
            payload["created_at"]
        )
        if result["status"] != "success":
            connection.rollback()
            raise RuntimeError(result["message"])
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def process_outbox_event(event: dict) -> None:
    event_type = event["event_type"]
    payload = event["payload"]

    if event_type == EVENT_CORE_BACKUP:
        _write_table_fee_backup(payload)
    elif event_type == EVENT_CORE_BACKUP_PRODUCT:
        _write_product_backup(payload)
    else:
        raise ValueError(f"未知 outbox 事件类型：{event_type}")


# ==================== worker 循环 ====================

def _worker_log(message: str) -> None:
    time_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"[{time_text}] {message}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "outbox_worker.log").open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except Exception as error:
        print(f"【outbox】写 outbox_worker.log 失败：{error}")
    print(line)


_STOP_REQUESTED = False


def request_stop() -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def run_once(max_events: int = 100) -> dict:
    # 回收超时 processing 事件，然后领取并处理一批 pending 事件。
    stats = {
        "claimed": 0,
        "done": 0,
        "failed": 0,
        "dead": 0,
    }

    stale_result = one_requeue_stale_outbox_events()
    if stale_result["status"] != "success":
        _worker_log(f"回收超时事件失败：{stale_result['message']}")
    elif stale_result["data"]:
        _worker_log(f"回收超时 processing 事件 {stale_result['data']} 条")

    remaining = max(0, int(max_events))
    while remaining > 0:
        claim_result = one_claim_outbox_events(min(OUTBOX_BATCH_SIZE, remaining))
        if claim_result["status"] != "success":
            _worker_log(f"领取 outbox 事件失败：{claim_result['message']}")
            break

        events = claim_result["data"]
        if not events:
            break

        for event in events:
            remaining -= 1
            stats["claimed"] += 1

            try:
                process_outbox_event(event)
            except Exception as error:
                error_text = str(error)
                fail_result = one_mark_outbox_event_failed(
                    event["event_id"],
                    event["retry_count"],
                    error_text
                )
                if fail_result["status"] == "success":
                    stats["failed"] += 1
                    if fail_result["data"] == STATUS_DEAD:
                        stats["dead"] += 1
                    _worker_log(
                        f"事件 {event['event_id']}（{event['event_type']}）处理失败："
                        f"{error_text}"
                    )
                else:
                    _worker_log(
                        f"事件 {event['event_id']} 标记失败时又出错："
                        f"{fail_result['message']}"
                    )
                continue

            done_result = one_mark_outbox_event_done(event["event_id"])
            if done_result["status"] == "success":
                stats["done"] += 1
            else:
                _worker_log(
                    f"事件 {event['event_id']} 备份已写入，但标记 done 失败："
                    f"{done_result['message']}"
                )

    return stats


def run_forever(poll_interval: float = None) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = False
    interval = OUTBOX_POLL_INTERVAL if poll_interval is None else float(poll_interval)
    _worker_log(
        f"outbox worker 启动，worker_id={OUTBOX_WORKER_ID}，轮询间隔={interval}s"
    )

    try:
        while not _STOP_REQUESTED:
            stats = run_once()
            if stats["claimed"] == 0:
                time.sleep(interval)
            else:
                _worker_log(
                    "本轮处理：claimed=%d done=%d failed=%d dead=%d"
                    % (
                        stats["claimed"],
                        stats["done"],
                        stats["failed"],
                        stats["dead"],
                    )
                )
    except KeyboardInterrupt:
        _worker_log("收到 KeyboardInterrupt，准备退出")
    finally:
        _worker_log("outbox worker 已停止")
