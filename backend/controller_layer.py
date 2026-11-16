from .io_layer import (
    one_get_current_time,
    one_get_current_time_pair,

    # 前端查询接口使用的 MySQL 函数。
    one_load_products_from_mysql,

    # 开台、关台流程使用的 MySQL 函数。
    one_execute_in_transaction,
    one_insert_ongoing_table_fee_to_mysql,
    one_update_ongoing_table_fee_in_mysql,
    one_lock_billiards_table,
    one_load_billiards_tables_from_mysql,
    one_load_all_ongoing_from_mysql,
    one_load_ongoing_by_table_from_mysql,
    one_load_ongoing_by_table_with_cursor,

    # 创建未支付订单流程使用的 MySQL 函数。
    one_load_unpaid_orders_from_mysql,
    one_insert_order_to_mysql,
    one_insert_order_table_fee_to_mysql,
    one_insert_order_product_consumption_to_mysql,
    one_delete_ongoing_consumption_from_mysql,

    # 确认收款流程使用的 MySQL 函数。
    one_load_order_by_id_from_mysql,
    one_update_order_payment_status_in_mysql,

    # 加商品消费流程使用的 MySQL 函数。
    one_load_product_by_id_from_mysql,
    one_insert_ongoing_product_consumption_to_mysql,
    one_deduct_product_stock_in_mysql,
    one_get_completed_idempotency_result_from_mysql,
    one_execute_in_transaction_with_idempotency,

    # 业务日志函数。
    one_write_business_log
)

from .business_layer import (
    # 开台及共用的取数、检查函数。
    two_check_billiards_tables,
    two_get_table_info,
    two_check_all_table_consumption_boxes,
    two_get_table_consumption_box,
    two_get_frontend_table_basic_info,
    two_get_empty_billiards_tables,
    two_create_flow_box,

    # 关台流程。
    two_get_open_table_consumption,
    two_add_close_time_to_table_consumption,
    two_calculate_table_fee,
    two_prepare_core_backup,

    # 创建未支付订单与清扫消费流程。
    two_get_frontend_checkout_options,
    two_get_unsettled_consumption_summary,
    two_check_selected_consumptions,
    two_check_selected_consumptions_not_in_unpaid_orders,
    two_create_unpaid_bill,

    # 确认收款。
    two_get_frontend_unpaid_orders_info,
    two_check_unpaid_order_for_payment,

    # 商品消费流程。
    two_check_products,
    two_get_frontend_products_info,
    two_get_product_info,
    two_check_product_can_be_ordered,
    two_create_product_consumption,
)

from .outbox import (
    EVENT_CORE_BACKUP,
    EVENT_CORE_BACKUP_PRODUCT,
    build_product_event_payload,
    build_table_fee_event_payload,
    one_insert_outbox_event,
)

from . import local_cache
from .flash_sale_queue import flash_sale_engine
from .schemas import MAX_IDEMPOTENCY_KEY_LENGTH


IDEMPOTENCY_OPERATION_ADD_PRODUCT = "add_product_consumption"


def _load_with_local_cache(key, loader, success_message):
    """按 Cache-Aside + 版本号回填读取配置型数据。

    缓存故障只降级为查数据库，不向主业务抛异常。
    """
    cached_value = None
    cache_version = 0

    try:
        cached_value, cache_version = local_cache.get_with_version(key)
    except Exception:
        # 正常情况下 local_cache 已自行吞掉异常；这里保留第二道防线。
        cached_value = None
        cache_version = 0

    if cached_value is not None:
        return {
            "status": "success",
            "error_type": None,
            "data": cached_value,
            "message": success_message
        }

    result = loader()
    if result.get("status") == "success":
        try:
            local_cache.set_if_version(
                key,
                result.get("data"),
                cache_version
            )
        except Exception:
            pass

    return result


def _load_products_list_cached():
    return _load_with_local_cache(
        local_cache.PRODUCTS_LIST_KEY,
        one_load_products_from_mysql,
        "商品总表读取成功"
    )


def _load_billiards_tables_cached():
    return _load_with_local_cache(
        local_cache.TABLES_CONFIG_KEY,
        one_load_billiards_tables_from_mysql,
        "桌台配置表读取成功"
    )


def invalidate_products_cache_after_commit():
    """商品新增、修改或库存变化提交成功后调用。"""
    try:
        local_cache.invalidate(local_cache.PRODUCTS_LIST_KEY)
    except Exception:
        pass


def invalidate_tables_cache_after_commit():
    """桌台配置或开台/关台状态提交成功后调用。"""
    try:
        local_cache.invalidate(local_cache.TABLES_CONFIG_KEY)
    except Exception:
        pass


def check_table_exists(table_no) -> bool:
    # 最普通的桌号检查：查 billiards_tables，存在返回 True，不存在返回 False。
    # 数据库读取失败时抛 RuntimeError，避免把“数据库故障”误判成“桌子不存在”。
    tables_result = _load_billiards_tables_cached()

    if tables_result["status"] != "success":
        raise RuntimeError(tables_result["message"])

    return str(table_no) in tables_result["data"]


def build_table_not_found_result(table_no) -> dict:
    # 统一的“桌子不存在”返回结构，HTTP 层会映射成 404。
    return {
        "status": "error",
        "error_type": "TableNotFound",
        "data": None,
        "message": f"桌台配置表中找不到 {table_no} 号桌"
    }


def three_get_frontend_products_data() -> dict:
    # 获取前端商品选择区域需要的最新商品数据。
    #
    # 本函数只读取真实商品数据并调用第二层整理函数，
    # 不处理商品消费、不修改库存，也不保存任何前端刷新结果。

    # ==================== 第一步：读取商品总表 ====================

    products_result = _load_products_list_cached()

    if products_result["status"] != "success":
        return products_result

    products = products_result["data"]

    # ==================== 第二步：检查商品总表 ====================

    check_result = two_check_products(products)

    if check_result["status"] != "success":
        return check_result

    products = check_result["data"]

    # ==================== 第三步：整理前端商品数据 ====================

    frontend_products_result = two_get_frontend_products_info(products)

    if frontend_products_result["status"] != "success":
        return frontend_products_result

    return {
        "status": "success",
        "error_type": None,
        "data": frontend_products_result["data"],
        "message": "前端商品数据获取成功"
    }


def three_get_frontend_unpaid_orders_data() -> dict:
    # 获取“确认收款”前端选择区域需要的最新未支付订单数据。
    #
    # 本函数只读取真实未支付订单并调用第二层整理函数，
    # 不确认收款、不转存历史订单，也不保存任何前端刷新结果。

    # ==================== 第一步：读取完整未支付订单 ====================

    unpaid_order_result = one_load_unpaid_orders_from_mysql()

    if unpaid_order_result["status"] != "success":
        return unpaid_order_result

    unpaid_order = unpaid_order_result["data"]

    # ==================== 第二步：整理前端选择和核对数据 ====================

    frontend_unpaid_orders_result = two_get_frontend_unpaid_orders_info(
        unpaid_order
    )

    if frontend_unpaid_orders_result["status"] != "success":
        return frontend_unpaid_orders_result

    return {
        "status": "success",
        "error_type": None,
        "data": frontend_unpaid_orders_result["data"],
        "message": "前端未支付订单数据获取成功"
    }


def three_get_frontend_checkout_options(table_id: str) -> dict:
    # 获取指定桌子“选择结账”需要的最新待结算明细。
    #
    # 本函数只读取和整理当前桌子的真实待结算消费，
    # 不创建订单、不清扫消费，也不保存任何前端刷新结果。

    # 第一行先确认桌号真实存在，不存在直接按 404 返回。
    if not check_table_exists(table_id):
        return build_table_not_found_result(table_id)

    # ==================== 入参检查 ====================

    # 前端传入的桌号必须是字符串。
    if not isinstance(table_id, str):
        return {
            "status": "error",
            "error_type": "TableIdFormatError",
            "data": None,
            "message": "桌号格式错误，应该是字符串"
        }

    # 去掉首尾空白后不能为空。
    if not table_id.strip():
        return {
            "status": "error",
            "error_type": "TableIdEmptyError",
            "data": None,
            "message": "桌号不能为空"
        }

    table_id = str(table_id)

    # ==================== 第一步：读单桌待结算消费 ====================

    ongoing_result = one_load_ongoing_by_table_from_mysql(table_id)

    if ongoing_result["status"] != "success":
        return ongoing_result

    table_consumption_boxes = ongoing_result["data"]

    # ==================== 第二步：检查待结算消费 ====================

    check_result = two_check_all_table_consumption_boxes(table_consumption_boxes)

    if check_result["status"] != "success":
        return check_result

    table_consumption_boxes = check_result["data"]

    # ==================== 第三步：取得指定桌子的消费框 ====================

    table_consumption_box_result = two_get_table_consumption_box(
        table_consumption_boxes,
        table_id
    )

    if table_consumption_box_result["status"] != "success":
        return table_consumption_box_result

    table_consumption_box = table_consumption_box_result["data"]

    # ==================== 第四步：整理前端结账明细 ====================

    checkout_options_result = two_get_frontend_checkout_options(
        table_consumption_box,
        table_id
    )

    if checkout_options_result["status"] != "success":
        return checkout_options_result

    return {
        "status": "success",
        "error_type": None,
        "data": checkout_options_result["data"],
        "message": f"{table_id} 号桌结账明细获取成功"
    }


def three_get_frontend_refresh_data() -> dict:
    # 获取前端统一刷新需要的数据。
    #
    # 本函数负责：
    # 1. 读取刷新所需的真实业务数据。
    # 2. 按顺序调用第二层取数和整理函数。
    # 3. 检查每一步是否成功，失败时立即返回。
    # 4. 汇总当前需要返回给前端的刷新内容。
    #
    # 本函数不修改数据，也不保存刷新结果。
    # 后续其他刷新内容继续加入 data 字典即可。

    # ==================== 第一步：读取全部待结算消费 ====================

    ongoing_order_result = one_load_all_ongoing_from_mysql()

    if ongoing_order_result["status"] != "success":
        return ongoing_order_result

    ongoing_order = ongoing_order_result["data"]

    # ==================== 第二步：检查待结算消费 ====================

    check_ongoing_result = two_check_all_table_consumption_boxes(ongoing_order)

    if check_ongoing_result["status"] != "success":
        return check_ongoing_result

    table_consumption_boxes = check_ongoing_result["data"]

    # ==================== 第三步：逐桌取得基础信息 ====================

    table_basic_info = {}

    for table_id in table_consumption_boxes:
        table_consumption_box_result = two_get_table_consumption_box(
            table_consumption_boxes,
            table_id
        )

        if table_consumption_box_result["status"] != "success":
            return table_consumption_box_result

        table_consumption_box = table_consumption_box_result["data"]

        table_basic_info_result = two_get_frontend_table_basic_info(
            table_consumption_box,
            table_id
        )

        if table_basic_info_result["status"] != "success":
            return table_basic_info_result

        table_basic_info[table_id] = table_basic_info_result["data"]

    # ==================== 第四步：取得商品信息 ====================

    frontend_products_result = three_get_frontend_products_data()

    if frontend_products_result["status"] != "success":
        return frontend_products_result

    # ==================== 第五步：汇总并返回刷新数据 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": {
            "桌台基础信息": table_basic_info,
            "商品信息": frontend_products_result["data"]
        },
        "message": "前端刷新数据获取成功"
    }


def three_open_table(
    table_id: str,
    source: str = "人",
    *,
    confirm_unsettled: bool = False
) -> dict:
    # source 是日志用的来源标记，默认 "人"，AI 调用时显式传 "AI"。
    # 外面三行统一收口：调内部 + 写业务日志 + 返回结果。

    def 开台_内部(table_id):
        # 开台流程
        #
        # 本函数负责：
        # 1. 从 MySQL 读取桌台数据和待结算消费。
        # 2. 按顺序调用第二层函数完成检查和判断。
        # 3. 检查每一步是否成功，失败时立即返回。
        # 4. 指定桌子被占用时，返回空闲桌子供前端选择。
        # 5. 用事务写入台位消费，成功后返回开台成功。
        #
        # 入口检查由本函数负责，其余由第二层负责。

        # 第一行先确认桌号真实存在，不存在直接按 404 返回。
        if not check_table_exists(table_id):
            return build_table_not_found_result(table_id)

        # ==================== 入参检查 ====================

        # 前端传入的桌号必须是字符串。
        if not isinstance(table_id, str):
            return {
                "status": "error",
                "error_type": "TableIdFormatError",
                "data": None,
                "message": "桌号格式错误，应该是字符串"
            }

        # 去掉首尾空白后不能为空。
        if not table_id.strip():
            return {
                "status": "error",
                "error_type": "TableIdEmptyError",
                "data": None,
                "message": "桌号不能为空"
            }

        # 桌号统一使用字符串。
        table_id = str(table_id)

        # ==================== 第一步：读桌台数据 ====================

        tables_result = _load_billiards_tables_cached()

        if tables_result["status"] != "success":
            return tables_result

        billiards_tables = tables_result["data"]

        # ==================== 第二步：检查桌台数据 ====================

        check_tables_result = two_check_billiards_tables(billiards_tables)

        if check_tables_result["status"] != "success":
            return check_tables_result

        billiards_tables = check_tables_result["data"]

        # ==================== 第三步：取指定桌的信息 ====================

        table_info_result = two_get_table_info(billiards_tables, table_id)

        if table_info_result["status"] != "success":
            return table_info_result

        table_info = table_info_result["data"]

        # ==================== 第四步：读所有待结算消费 ====================

        ongoing_result = one_load_all_ongoing_from_mysql()

        if ongoing_result["status"] != "success":
            return ongoing_result

        ongoing_order = ongoing_result["data"]

        # ==================== 第五步：检查待结算消费 ====================

        check_ongoing_result = two_check_all_table_consumption_boxes(ongoing_order)

        if check_ongoing_result["status"] != "success":
            return check_ongoing_result

        table_consumption_boxes = check_ongoing_result["data"]

        # ==================== 第六步：取指定桌的消费框 ====================

        box_result = two_get_table_consumption_box(table_consumption_boxes, table_id)

        if box_result["status"] != "success":
            return box_result

        table_consumption_box = box_result["data"]

        # ==================== 第七步：创建 flow_box ====================

        # flow_box 需要用到事务外读到的 table_info（每小时价格），
        # 创建好以后交给下面的事务负责写入。
        current_time, current_time_ms = one_get_current_time_pair()

        flow_box_result = two_create_flow_box(
            table_id,
            current_time,
            table_info,
            current_time_ms
        )

        if flow_box_result["status"] != "success":
            return flow_box_result

        flow_box = flow_box_result["data"]

        # ==================== 第八步：事务里锁桌子 + 重新检查空闲 + 写入 ====================

        # 并发说明：
        # 两个人可能同时开同一桌，事务外读到的空闲判断可能已经过期，
        # 所以把"重新检查是否空闲 + 写入"放进同一个事务：
        # 先锁住桌子那一行（别人也要锁这行时会卡住排队），
        # 拿到锁后用事务里的 cursor 重新读一次该桌待结算消费，
        # 重新确认空闲后才写入台位费。
        def open_table_operation(cursor):

            # 8.1 锁住桌子那一行；桌子不存在就直接返回错误。
            lock_result = one_lock_billiards_table(cursor, table_id)

            if lock_result["status"] != "success":
                return lock_result

            # 8.2 重新读该桌的待结算消费。
            #     必须用事务里的 cursor 新读，不能复用事务外读到的数据，
            #     事务外读到的可能已经过期。
            ongoing_result = one_load_ongoing_by_table_with_cursor(cursor, table_id)

            if ongoing_result["status"] != "success":
                return ongoing_result

            table_consumption_boxes = ongoing_result["data"]

            # 8.3 检查待结算消费（结构检查，复用第二层现有函数）。
            check_result = two_check_all_table_consumption_boxes(table_consumption_boxes)

            if check_result["status"] != "success":
                return check_result

            table_consumption_boxes = check_result["data"]

            # 8.4 取该桌的消费框。
            box_result = two_get_table_consumption_box(table_consumption_boxes, table_id)

            if box_result["status"] != "success":
                return box_result

            table_consumption_box = box_result["data"]

            # 8.5 重新判断该桌是否空闲。
            empty_result = two_get_empty_billiards_tables(table_consumption_boxes)

            if empty_result["status"] != "success":
                return empty_result

            empty_tables = empty_result["data"]

            # 重新检查时发现已被占用：返回错误和空闲桌子列表，
            # 让前端展示并由客人重新选择。
            if table_id not in empty_tables:
                return {
                    "status": "error",
                    "error_type": "TableOccupied",
                    "data": {
                        "空闲桌子": empty_tables
                    },
                    "message": f"{table_id} 号桌已被占用，无法开台"
                }

            # 8.6 检查该桌是否还有未结账消费。
            #
            # 此时已经确认该桌没有未关台的台位费，但可能还有：
            # - 上一轮已经关台、尚未建单的台费；
            # - 尚未建单的商品消费。
            # 直接开台会把上一轮消费和新台费混在一起，所以先返回
            # 可确认的提醒；确认后由前端带 confirm_unsettled=true 重试。
            pending_consumptions = table_consumption_box["待结算消费"]

            if pending_consumptions and not confirm_unsettled:
                summary_result = two_get_unsettled_consumption_summary(
                    table_consumption_box,
                    table_id
                )

                if summary_result["status"] != "success":
                    return summary_result

                return {
                    "status": "error",
                    "error_type": "TableHasUnsettledConsumptions",
                    "data": summary_result["data"],
                    "message": summary_result["message"]
                }

            # 8.7 插入台位费（主表 1 行 + 明细表 1 行）。
            insert_result = one_insert_ongoing_table_fee_to_mysql(
                cursor,
                flow_box["桌子号码"],
                flow_box["台位消费"]["消费记录编号"],
                flow_box["台位消费"]["开台时间"],
                flow_box["台位消费"]["每小时价格"]
            )

            return insert_result

        transaction_result = one_execute_in_transaction(open_table_operation)

        if transaction_result["status"] != "success":
            return transaction_result

        # 事务已经 commit；此时才递增版本并删除桌台配置缓存。
        invalidate_tables_cache_after_commit()
        flash_sale_engine.on_table_opened(table_id)

        # ==================== 第九步：返回成功 ====================

        return {
            "status": "success",
            "error_type": None,
            "data": transaction_result["data"],
            "message": f"{table_id} 号桌开台成功"
        }

    result = 开台_内部(table_id)
    log_params = f"桌号={table_id}"
    if confirm_unsettled:
        log_params += "，确认未结账消费=是"
    one_write_business_log(source, "开台", log_params, result)
    return result

def three_close_table(table_id: str, source: str = "人") -> dict:
    # source 是日志用的来源标记，默认 "人"，AI 调用时显式传 "AI"。
    # 外面三行统一收口：调内部 + 写业务日志 + 返回结果。

    def 关台_内部(table_id):
        # 关台流程
        #
        # 本函数负责：
        # 1. 从 MySQL 读取指定桌子的待结算消费。
        # 2. 按顺序调用第二层函数完成检查和关台准备。
        # 3. 检查每一步是否成功，失败时停止后续步骤。
        # 4. 用事务写回关台结果，并在同一事务里写 outbox 待办事件。
        #
        # 入口检查由本函数负责，其余由第二层负责。
        # 关台使用原有消费记录，不生成新的消费记录编号。

        # 第一行先确认桌号真实存在，不存在直接按 404 返回。
        if not check_table_exists(table_id):
            return build_table_not_found_result(table_id)

        # ==================== 入参检查 ====================

        # 前端传入的桌号必须是字符串。
        if not isinstance(table_id, str):
            return {
                "status": "error",
                "error_type": "TableIdFormatError",
                "data": None,
                "message": "桌号格式错误，应该是字符串"
            }

        # 去掉首尾空白后不能为空。
        if not table_id.strip():
            return {
                "status": "error",
                "error_type": "TableIdEmptyError",
                "data": None,
                "message": "桌号不能为空"
            }

        # 桌号统一使用字符串。
        table_id = str(table_id)

        # ==================== 第一步：读单桌待结算消费 ====================

        ongoing_result = one_load_ongoing_by_table_from_mysql(table_id)

        if ongoing_result["status"] != "success":
            return ongoing_result

        table_consumption_boxes = ongoing_result["data"]

        # ==================== 第二步：检查待结算消费 ====================

        check_result = two_check_all_table_consumption_boxes(table_consumption_boxes)

        if check_result["status"] != "success":
            return check_result

        table_consumption_boxes = check_result["data"]

        # ==================== 第三步：取指定桌的消费框 ====================

        box_result = two_get_table_consumption_box(table_consumption_boxes, table_id)

        if box_result["status"] != "success":
            return box_result

        table_consumption_box = box_result["data"]

        # ==================== 第四步：找未关台台位费 ====================

        flow_result = two_get_open_table_consumption(table_consumption_box, table_id)

        if flow_result["status"] != "success":
            return flow_result

        flow_box = flow_result["data"]

        # ==================== 第五步：获取关台时间 ====================

        close_time = one_get_current_time()

        # ==================== 第六步：补关台时间 ====================

        close_time_result = two_add_close_time_to_table_consumption(flow_box, close_time)

        if close_time_result["status"] != "success":
            return close_time_result

        flow_box = close_time_result["data"]

        # ==================== 第七步：算台费 ====================

        fee_result = two_calculate_table_fee(flow_box)

        if fee_result["status"] != "success":
            return fee_result

        flow_box = fee_result["data"]

        # ==================== 第八步：准备 outbox 备份事件 ====================

        # 备份数据由业务层只做校验和组装。
        # 真正的备份写入交给 worker，关台事务只负责写一条待办事件。
        backup_payload_result = two_prepare_core_backup(
            table_id,
            flow_box["台位消费"]
        )

        if backup_payload_result["status"] != "success":
            return backup_payload_result

        backup_record = backup_payload_result["data"]
        backup_event_payload = build_table_fee_event_payload(backup_record)

        # ==================== 第九步：事务里更新关台结果 + 写 outbox 便签 ====================

        def close_table_operation(cursor):
            table_consumption = flow_box["台位消费"]

            update_result = one_update_ongoing_table_fee_in_mysql(
                cursor,
                table_consumption["消费记录编号"],
                table_consumption["关台时间"],
                table_consumption["金额"]
            )

            if update_result["status"] != "success":
                return update_result

            outbox_result = one_insert_outbox_event(
                cursor,
                EVENT_CORE_BACKUP,
                backup_event_payload,
                table_consumption["关台时间"]
            )

            if outbox_result["status"] != "success":
                return outbox_result

            return update_result

        transaction_result = one_execute_in_transaction(close_table_operation)

        if transaction_result["status"] != "success":
            return transaction_result

        # 事务已经 commit；此时才递增版本并删除桌台配置缓存。
        invalidate_tables_cache_after_commit()
        flash_sale_engine.on_table_closed(table_id)

        # ==================== 第十步：返回成功 ====================

        return {
            "status": "success",
            "error_type": None,
            "data": flow_box,
            "message": f"{table_id} 号桌关台成功，核心备份已加入异步队列"
        }

    result = 关台_内部(table_id)
    one_write_business_log(source, "关台", f"桌号={table_id}", result)
    return result

def three_create_unpaid_order(selected_data: dict, source: str = "人") -> dict:
    # source 是日志用的来源标记，默认 "人"，AI 调用时显式传 "AI"。
    # 外面统一收口：调内部 + 写业务日志 + 返回结果。

    def 创建订单_内部(selected_data):
        # 创建未支付订单，并在同一个事务里清扫已进入订单的待结算消费。
        #
        # selected_data 是前端传来的选择数据，包含：
        # - 桌子号码
        # - 消费记录编号列表
        #
        # 本函数负责按顺序调用第一层和第二层，并检查每一步的结果。
        # 前端选择和消费记录的详细检查，交给第二层检查函数完成。
        #
        # 订单主表、订单明细、待结算消费清扫在同一个事务里完成：
        # 要么全部成功，要么全部回滚。

        # ==================== 第一步：检查前端选择数据格式 ====================

        # 先粗检一遍，防止传进来的不是字典。
        # 详细检查交给第二层 two_check_selected_consumptions。
        if not isinstance(selected_data, dict):
            return {
                "status": "error",
                "error_type": "SelectedDataFormatError",
                "data": None,
                "message": "前端结账选择数据格式错误，应该是字典"
            }

        if "桌子号码" not in selected_data:
            return {
                "status": "error",
                "error_type": "SelectedTableIdMissing",
                "data": None,
                "message": "前端结账选择数据缺少桌子号码"
            }

        if "消费记录编号列表" not in selected_data:
            return {
                "status": "error",
                "error_type": "SelectedConsumptionIdListMissing",
                "data": None,
                "message": "前端结账选择数据缺少消费记录编号列表"
            }

        table_id = selected_data["桌子号码"]
        consumption_id_list = selected_data["消费记录编号列表"]

        # 桌号一旦取到，先确认它真实存在，不存在直接按 404 返回。
        if not check_table_exists(table_id):
            return build_table_not_found_result(table_id)

        # 桌子号码必须是非空字符串。
        if not isinstance(table_id, str) or not table_id.strip():
            return {
                "status": "error",
                "error_type": "SelectedTableIdFormatError",
                "data": None,
                "message": "前端传入的桌子号码应该是非空字符串"
            }

        # 一次可以选择多条消费，所以这里继续使用非空列表。
        if not isinstance(consumption_id_list, list) or not consumption_id_list:
            return {
                "status": "error",
                "error_type": "SelectedConsumptionIdListFormatError",
                "data": None,
                "message": "消费记录编号列表应该是非空列表"
            }

        # 逐条检查消费记录编号，必须都是非空字符串。
        for consumption_id in consumption_id_list:
            if not isinstance(consumption_id, str) or not consumption_id.strip():
                return {
                    "status": "error",
                    "error_type": "SelectedConsumptionIdFormatError",
                    "data": None,
                    "message": "消费记录编号列表中存在格式错误的编号"
                }

        # 前面已确认编号都是字符串，现在可以安全放入集合查重。
        # 集合会去掉重复项；数量不同，说明存在重复编号。
        if len(consumption_id_list) != len(set(consumption_id_list)):
            return {
                "status": "error",
                "error_type": "SelectedConsumptionIdDuplicate",
                "data": None,
                "message": "消费记录编号列表中存在重复编号"
            }

        # ==================== 第二步：读单桌待结算消费 ====================

        ongoing_result = one_load_ongoing_by_table_from_mysql(table_id)

        if ongoing_result["status"] != "success":
            return ongoing_result

        table_consumption_boxes = ongoing_result["data"]

        # ==================== 第三步：检查待结算消费 ====================

        check_result = two_check_all_table_consumption_boxes(table_consumption_boxes)

        if check_result["status"] != "success":
            return check_result

        table_consumption_boxes = check_result["data"]

        # ==================== 第四步：取指定桌的消费框 ====================

        box_result = two_get_table_consumption_box(table_consumption_boxes, table_id)

        if box_result["status"] != "success":
            return box_result

        table_consumption_box = box_result["data"]

        # ==================== 第五步：检查前端选中的消费 ====================

        selected_result = two_check_selected_consumptions(
            table_consumption_box,
            selected_data
        )

        if selected_result["status"] != "success":
            return selected_result

        selected_consumptions = selected_result["data"]

        # ==================== 第六步：读未支付订单（用于检查重复） ====================

        unpaid_result = one_load_unpaid_orders_from_mysql()

        if unpaid_result["status"] != "success":
            return unpaid_result

        unpaid_order = unpaid_result["data"]

        # ==================== 第七步：检查选中的消费有没有已在别的订单里 ====================

        dup_check_result = two_check_selected_consumptions_not_in_unpaid_orders(
            unpaid_order,
            selected_consumptions
        )

        if dup_check_result["status"] != "success":
            return dup_check_result

        # ==================== 第八步：准备订单数据 ====================

        # 业务时间取秒级，编号时间取毫秒级，两者由同一次时钟读取取得。
        order_time, order_time_ms = one_get_current_time_pair()

        prepare_result = two_create_unpaid_bill(
            table_id,
            selected_consumptions,
            order_time,
            order_time_ms
        )

        if prepare_result["status"] != "success":
            return prepare_result

        order_data = prepare_result["data"]

        # order_data 结构：
        # {"桌子号码": ..., "订单编号": ..., "总金额": ..., "创建时间": ..., "已选消费": [...]}

        # ==================== 第九步：事务里写入 ====================

        # 事务内依次完成：插订单主表、按类型插订单明细、逐条删待结算消费。
        # 任何一步失败，整个事务回滚，数据库保持原样。
        def create_order_operation(cursor):
            order_id = order_data["订单编号"]

            # 9.1 插订单主表
            insert_order_result = one_insert_order_to_mysql(
                cursor,
                order_id,
                order_data["桌子号码"],
                order_data["总金额"],
                order_data["创建时间"]
            )

            if insert_order_result["status"] != "success":
                return insert_order_result

            # 统一按消费编号升序处理，保证所有并发请求以相同顺序加锁，
            # 避免 A/B 与 B/A 的相反删除顺序形成 ABBA 死锁。
            selected_consumptions_for_write = sorted(
                order_data["已选消费"],
                key=lambda consumption: consumption["消费记录编号"]
            )

            # 9.2 遍历已选消费，按类型插明细
            for consumption in selected_consumptions_for_write:

                consumption_id = consumption["消费记录编号"]
                consumption_type = consumption["类型"]

                if consumption_type == "台位费":

                    insert_detail_result = one_insert_order_table_fee_to_mysql(
                        cursor,
                        order_id,
                        consumption_id,
                        consumption["开台时间"],
                        consumption["关台时间"],
                        consumption["每小时价格"],
                        consumption["金额"]
                    )

                    if insert_detail_result["status"] != "success":
                        return insert_detail_result

                elif consumption_type == "商品":

                    insert_detail_result = one_insert_order_product_consumption_to_mysql(
                        cursor,
                        order_id,
                        consumption_id,
                        consumption["商品编号"],
                        consumption["商品名称"],
                        consumption["单价"],
                        consumption["数量"],
                        consumption["金额"]
                    )

                    if insert_detail_result["status"] != "success":
                        return insert_detail_result

                else:
                    return {
                        "status": "error",
                        "error_type": "UnknownConsumptionType",
                        "data": None,
                        "message": f"无法识别的消费类型：{consumption_type}"
                    }

            # 9.3 遍历已选消费，逐条删待结算消费
            for consumption in selected_consumptions_for_write:

                consumption_id = consumption["消费记录编号"]

                delete_result = one_delete_ongoing_consumption_from_mysql(
                    cursor,
                    consumption_id
                )

                if delete_result["status"] != "success":
                    return delete_result

            # 全部成功
            return {
                "status": "success",
                "error_type": None,
                "data": {
                    "订单编号": order_id
                },
                "message": "订单写入成功，待结算消费已清扫"
            }

        transaction_result = one_execute_in_transaction(create_order_operation)

        if transaction_result["status"] != "success":
            return transaction_result

        # ==================== 第十步：返回成功 ====================

        return {
            "status": "success",
            "error_type": None,
            "data": order_data,
            "message": f"{table_id} 号桌未支付订单 {order_data['订单编号']} 已创建"
        }

    result = 创建订单_内部(selected_data)

    if isinstance(selected_data, dict):
        桌号 = selected_data.get("桌子号码", "?")
        编号列表 = selected_data.get("消费记录编号列表", [])
        if isinstance(编号列表, list):
            编号字符串 = "/".join(str(x) for x in 编号列表) if 编号列表 else "无"
        else:
            编号字符串 = "无效"
        params = f"桌号={桌号}, 消费编号={编号字符串}"
    else:
        params = "参数=无效"

    one_write_business_log(source, "创建订单", params, result)
    return result

def three_confirm_payment(payment_data: dict, source: str = "人") -> dict:
    # source 是日志用的来源标记，默认 "人"，AI 调用时显式传 "AI"。
    # 外面统一收口：调内部 + 写业务日志 + 返回结果。

    def 确认收款_内部(payment_data):
        # 人工确认收款。
        #
        # payment_data 由前端传入，例如：
        # {"订单编号": "1_tmv7ggbkz_v4bq51"}
        #
        # 调用前提：
        # 店员已经核实钱款到账，并点击“确认收款”。
        # 本函数负责记录这个确认结果，不执行实际扣款。

        # ==================== 第一步：检查前端支付数据格式 ====================

        # 先粗检一遍，防止传进来的不是字典。
        if not isinstance(payment_data, dict):
            return {
                "status": "error",
                "error_type": "PaymentDataFormatError",
                "data": None,
                "message": "前端支付数据格式错误，应该是字典"
            }

        if "订单编号" not in payment_data:
            return {
                "status": "error",
                "error_type": "PaymentOrderIdMissing",
                "data": None,
                "message": "前端支付数据缺少订单编号"
            }

        order_id = payment_data["订单编号"]

        # 订单编号必须是非空字符串。
        if not isinstance(order_id, str) or not order_id.strip():
            return {
                "status": "error",
                "error_type": "PaymentOrderIdFormatError",
                "data": None,
                "message": "前端传入的订单编号格式错误"
            }

        # ==================== 第二步：读取单张订单 ====================

        order_result = one_load_order_by_id_from_mysql(order_id)

        if order_result["status"] != "success":
            return order_result

        order_data = order_result["data"]

        # ==================== 第三步：检查订单是否可以收款 ====================

        check_result = two_check_unpaid_order_for_payment(order_data, payment_data)

        if check_result["status"] != "success":
            return check_result

        # ==================== 第四步：取支付时间 ====================

        payment_time = one_get_current_time()

        # ==================== 第五步：事务里更新订单支付状态 ====================

        # 事务内的写操作：把这张订单改为已支付，并记录支付时间。
        def confirm_payment_operation(cursor):
            return one_update_order_payment_status_in_mysql(
                cursor,
                order_id,
                payment_time
            )

        transaction_result = one_execute_in_transaction(confirm_payment_operation)

        if transaction_result["status"] != "success":
            return transaction_result

        # ==================== 第六步：返回成功 ====================

        return {
            "status": "success",
            "error_type": None,
            "data": {
                "订单编号": order_id
            },
            "message": f"订单 {order_id} 已确认收款"
        }

    result = 确认收款_内部(payment_data)

    if isinstance(payment_data, dict):
        params = f"订单={payment_data.get('订单编号', '?')}"
    else:
        params = "参数=无效"

    one_write_business_log(source, "确认收款", params, result)
    return result

def three_add_product_consumption(
    table_id: str,
    product_id: str,
    product_count: int,
    idempotency_key: str | None = None,
    *,
    source: str = "人"
) -> dict:
    # source 是日志用的来源标记，默认 "人"，AI 调用时显式传 "AI"。
    # 外面三行统一收口：调内部 + 写业务日志 + 返回结果。

    def 加商品_内部(table_id, product_id, product_count):
        # 商品消费流程
        #
        # 本函数负责：
        # 1. 粗检前端传来的商品编号和购买数量。
        # 2. 从 MySQL 读取单个商品的真实资料并检查字段。
        # 3. 检查购买数量和库存。
        # 4. 读单桌待结算消费，确认商品消费有一个可挂载的桌号。
        # 5. 组装商品消费记录。
        # 6. 用事务写入：幂等占位 + 插商品消费 + 扣库存 + 写 outbox + 存结果。
        #
        # 第 6 步的所有写操作在同一个事务里完成：
        # 任何一步失败，整个事务回滚，数据库保持原样。
        #
        # 商品消费不要求桌子当前处于开台状态。
        # 只要桌子真实存在，就可以产生商品消费。

        # 第一行先确认桌号真实存在，不存在直接按 404 返回。
        if not check_table_exists(table_id):
            return build_table_not_found_result(table_id)

        # 桌号通过后立刻校验并读取幂等键。
        # 同一 Key 已经成功过时，直接返回第一次结果，不再读商品、不再查库存。
        if idempotency_key is None:
            return {
                "status": "error",
                "error_type": "IdempotencyKeyRequired",
                "data": None,
                "message": "缺少 Idempotency-Key 请求头"
            }

        if not isinstance(idempotency_key, str):
            return {
                "status": "error",
                "error_type": "IdempotencyKeyFormatError",
                "data": None,
                "message": "Idempotency-Key 必须是字符串"
            }

        normalized_key = idempotency_key.strip()
        if not normalized_key:
            return {
                "status": "error",
                "error_type": "IdempotencyKeyRequired",
                "data": None,
                "message": "Idempotency-Key 不能为空"
            }

        if len(normalized_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            return {
                "status": "error",
                "error_type": "IdempotencyKeyFormatError",
                "data": None,
                "message": f"Idempotency-Key 长度不能超过 {MAX_IDEMPOTENCY_KEY_LENGTH}"
            }

        replay_result = one_get_completed_idempotency_result_from_mysql(
            normalized_key,
            IDEMPOTENCY_OPERATION_ADD_PRODUCT
        )

        if replay_result["status"] != "success":
            return replay_result

        if replay_result["data"] is not None:
            return replay_result["data"]

        # ==================== 入参检查 ====================

        # 前端传入的桌号必须是字符串。
        if not isinstance(table_id, str):
            return {
                "status": "error",
                "error_type": "TableIdFormatError",
                "data": None,
                "message": "桌号格式错误，应该是字符串"
            }

        # 去掉首尾空白后不能为空。
        if not table_id.strip():
            return {
                "status": "error",
                "error_type": "TableIdEmptyError",
                "data": None,
                "message": "桌号不能为空"
            }

        # 桌号统一使用字符串。
        table_id = str(table_id)

        # ==================== 第一步：入参粗检 ====================

        # 详细的数量和库存检查交给第二层检查函数。
        if not isinstance(product_id, str) or not product_id.strip():
            return {
                "status": "error",
                "error_type": "ProductIdFormatError",
                "data": None,
                "message": "商品编号格式错误，应该是非空字符串"
            }

        if not (type(product_count) is int and product_count > 0):
            return {
                "status": "error",
                "error_type": "ProductCountInvalid",
                "data": None,
                "message": "商品数量格式错误，必须是大于 0 的整数"
            }

        # ==================== 第二步：读单个商品 ====================

        product_result = one_load_product_by_id_from_mysql(product_id)

        if product_result["status"] != "success":
            return product_result

        product_raw = product_result["data"]

        # ==================== 第三步：检查商品字段完整性 ====================

        info_result = two_get_product_info(product_raw, product_id)

        if info_result["status"] != "success":
            return info_result

        product_info = info_result["data"]

        # ==================== 第四步：检查库存 ====================

        check_result = two_check_product_can_be_ordered(
            product_id,
            product_info,
            product_count
        )

        if check_result["status"] != "success":
            return check_result

        check_data = check_result["data"]

        # ==================== 第五步：读单桌待结算消费 ====================

        ongoing_result = one_load_ongoing_by_table_from_mysql(table_id)

        if ongoing_result["status"] != "success":
            return ongoing_result

        table_consumption_boxes = ongoing_result["data"]

        # ==================== 第六步：检查待结算数据 ====================

        check_box_result = two_check_all_table_consumption_boxes(
            table_consumption_boxes
        )

        if check_box_result["status"] != "success":
            return check_box_result

        table_consumption_boxes = check_box_result["data"]

        # ==================== 第七步：确认指定桌存在 ====================

        box_result = two_get_table_consumption_box(
            table_consumption_boxes,
            table_id
        )

        if box_result["status"] != "success":
            return box_result

        # ==================== 第八步：组装商品消费 ====================

        # 创建时间用秒级，消费编号用毫秒级，两者由同一次时钟读取取得。
        current_time, current_time_ms = one_get_current_time_pair()

        create_result = two_create_product_consumption(
            table_id,
            check_data,
            current_time,
            current_time_ms
        )

        if create_result["status"] != "success":
            return create_result

        product_consumption_data = create_result["data"]

        # ==================== 第九步：准备商品备份 outbox 事件 ====================

        backup_payload_result = two_prepare_core_backup(
            table_id,
            product_consumption_data
        )

        if backup_payload_result["status"] != "success":
            return backup_payload_result

        backup_record = backup_payload_result["data"]
        backup_event_payload = build_product_event_payload(backup_record)

        # ==================== 第十步：事务里幂等占位 + 写消费 + 扣库存 + 写 outbox ====================

        # 事务内依次完成：查/占位幂等键、插商品消费、扣商品库存、
        # 写 outbox 待办事件、保存首次成功结果。
        # 任何一步失败，整个事务回滚，数据库保持原样。
        def add_product_operation(cursor):

            consumption = product_consumption_data

            # 10.1 插商品消费
            insert_result = one_insert_ongoing_product_consumption_to_mysql(
                cursor,
                consumption["桌子号码"],
                consumption["消费记录编号"],
                consumption["商品编号"],
                consumption["商品名称"],
                consumption["单价"],
                consumption["数量"],
                consumption["金额"],
                current_time
            )

            if insert_result["status"] != "success":
                return insert_result

            # 10.2 扣库存
            stock_result = one_deduct_product_stock_in_mysql(
                cursor,
                consumption["商品编号"],
                consumption["数量"]
            )

            if stock_result["status"] != "success":
                return stock_result

            # 10.3 写 outbox 待办事件
            outbox_result = one_insert_outbox_event(
                cursor,
                EVENT_CORE_BACKUP_PRODUCT,
                backup_event_payload,
                current_time
            )

            if outbox_result["status"] != "success":
                return outbox_result

            return {
                "status": "success",
                "error_type": None,
                "data": consumption,
                "message": f"{table_id} 号桌商品消费添加成功，核心备份已加入异步队列"
            }

        transaction_result = one_execute_in_transaction_with_idempotency(
            add_product_operation,
            normalized_key,
            IDEMPOTENCY_OPERATION_ADD_PRODUCT
        )

        if transaction_result["status"] != "success":
            return transaction_result

        # 事务已经 commit；库存已变化，删除整张商品列表缓存。
        invalidate_products_cache_after_commit()
        if source != "flash-sale":
            flash_sale_engine.on_product_sold(product_id, product_count)

        # ==================== 第十一步：返回首次成功结果 ====================

        return transaction_result

    result = 加商品_内部(table_id, product_id, product_count)

    # 并发窗口兜底：预查时首次请求可能还没提交，随后业务检查失败。
    # 如果此刻防重表里已经出现同一 Key 的成功结果，仍按幂等语义返回首次结果。
    if (
        result.get("status") != "success"
        and isinstance(idempotency_key, str)
        and idempotency_key.strip()
    ):
        replay_result = one_get_completed_idempotency_result_from_mysql(
            idempotency_key.strip(),
            IDEMPOTENCY_OPERATION_ADD_PRODUCT
        )

        if (
            replay_result["status"] == "success"
            and replay_result["data"] is not None
        ):
            result = replay_result["data"]

    one_write_business_log(source, "加商品", f"桌号={table_id}, 商品={product_id}, 数量={product_count}", result)
    return result
