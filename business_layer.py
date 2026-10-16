import time
import copy

from io_layer import (
    one_load_json_file,
    one_save_json_file,
    one_change_time_format,
    one_get_current_time,
    one_insert_core_backup_to_mysql
)


# ==================== 开台流程 ====================

def two_check_billiards_tables(billiards_tables: dict) -> dict:
    # 检查桌台总表
    # 作用：只做检查，不再切数据
    # （billiards_tables 子树已经由上一层切好并传入）
    # 不关心商品、不关心助教、不修改数据、不写文件
    #
    # 本函数负责：
    # 1. 检查整张桌台总表是不是字典。
    # 2. 检查每张桌子的信息是不是字典。
    # 3. 检查每张桌子有没有 每小时价格 字段。
    # 4. 检查 每小时价格 不是 None、是数字（不是布尔）、并且大于 0。
    #
    # 本函数不负责：
    # 1. 不检查桌号本身的格式。
    # 2. 不检查桌台是否被占用。
    # 3. 不修改数据，也不写文件。

    # ==================== 第一步：检查整体结构 ====================

    if not isinstance(billiards_tables, dict):
        return {
            "status": "error",
            "error_type": "BilliardsTablesFormatError",
            "data": None,
            "message": "billiards_tables 格式错误，应该是字典"
        }

    # ==================== 第二步：逐张检查桌子 ====================

    for table_id, table_info in billiards_tables.items():

        # 每张桌子的信息必须是字典。
        if not isinstance(table_info, dict):
            return {
                "status": "error",
                "error_type": "TableInfoFormatError",
                "data": None,
                "message": f"{table_id} 号桌信息格式错误，应该是字典"
            }

        # 每张桌子都必须带有 每小时价格。
        if "每小时价格" not in table_info:
            return {
                "status": "error",
                "error_type": "TableHourPriceMissing",
                "data": None,
                "message": f"{table_id} 号桌缺少 每小时价格 字段"
            }

        hour_price = table_info["每小时价格"]

        # 每小时价格 不能是 None。
        # None 表示这张桌子在数据库里没有配置价格，无法计算台费。
        if hour_price is None:
            return {
                "status": "error",
                "error_type": "TableHourPriceEmpty",
                "data": None,
                "message": f"{table_id} 号桌的 每小时价格 不能为空"
            }

        # 每小时价格 必须是数字。
        # 这里用 type() 判断，True 和 False 是布尔，会被排除。
        if type(hour_price) not in (int, float):
            return {
                "status": "error",
                "error_type": "TableHourPriceFormatError",
                "data": None,
                "message": f"{table_id} 号桌的 每小时价格 格式错误，应该是数字"
            }

        # 每小时价格 必须大于 0。
        if hour_price <= 0:
            return {
                "status": "error",
                "error_type": "TableHourPriceInvalid",
                "data": None,
                "message": f"{table_id} 号桌的 每小时价格 必须大于 0"
            }

    # ==================== 第三步：返回检查通过的桌台总表 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": billiards_tables,
        "message": "桌台总表获取成功"
    }

def two_get_table_info(billiards_tables: dict, table_id: str) -> dict:
    # 获取某一张桌子的桌台信息
    # billiards_tables 已经由上一层函数获取并检查完成
    # 这里只负责根据 table_id 取出对应桌台资料
    # 不修改数据，不写文件

    if table_id not in billiards_tables:
        return {
            "status": "error",
            "error_type": "Input_TableNotFound",
            "data": None,
            "message": f"桌台总表中找不到 {table_id} 号桌"
        }

    table_info = billiards_tables[table_id]

    if not isinstance(table_info, dict):
        return {
            "status": "error",
            "error_type": "TableInfoFormatError",
            "data": None,
            "message": f"{table_id} 号桌信息格式错误：应该是字典"
        }

    return {
        "status": "success",
        "error_type": None,
        "data": table_info,
        "message": f"成功获取 {table_id} 号桌信息"
    }

def two_check_all_table_consumption_boxes(table_consumption_boxes: dict) -> dict:
    # 检查所有桌子的消费框
    #
    # table_consumption_boxes 是已经去掉 _说明 的
    # 所有桌台消费框数据。
    # 本函数只做检查，不再切数据。
    #
    # 本函数负责：
    # 1. 检查 table_consumption_boxes 的整体结构。
    # 2. 检查每张桌子的消费框是否为字典。
    # 3. 检查每张桌子的待结算消费是否为字典。
    # 4. 逐条检查消费记录的基础字段：
    #    记录本身是字典、带有消费记录编号、带有类型，
    #    编号和类型都是非空字符串。
    #
    # 本函数不负责：
    # 1. 不取得某一张指定桌子的消费框。
    # 2. 不检查消费记录编号是否重复。
    # 3. 不检查外层标签和内部消费记录编号是否一致。
    # 4. 不检查消费记录的业务字段（关台时间、金额等）。
    # 5. 不修改数据，也不写文件。
    #
    # 下一步取得指定桌子的函数，
    # 再对那张桌子的消费记录进行详细检查。

    # ==================== 第一步：检查整体结构 ====================

    if not isinstance(table_consumption_boxes, dict):
        return {
            "status": "error",
            "error_type": "OngoingOrderFormatError",
            "data": None,
            "message": "Ongoing_order 数据格式错误，应该是字典"
        }

    # ==================== 第二步：检查每张桌子的消费框和消费记录 ====================

    for table_id, table_box in table_consumption_boxes.items():

        # 每个桌号对应的内容必须是一个消费框字典。
        if not isinstance(table_box, dict):
            return {
                "status": "error",
                "error_type": "TableConsumptionBoxFormatError",
                "data": None,
                "message": f"{table_id} 号桌消费框格式错误，应该是字典"
            }

        # 每张桌子的消费框都必须包含待结算消费。
        if "待结算消费" not in table_box:
            return {
                "status": "error",
                "error_type": "PendingConsumptionMissing",
                "data": None,
                "message": f"{table_id} 号桌缺少 待结算消费 字段"
            }

        pending_consumptions = table_box["待结算消费"]

        # 新的数据结构中，待结算消费必须是字典。
        #
        # 有消费时：
        # {
        #     "T_1_xxx": {...},
        #     "S_1_xxx": {...}
        # }
        #
        # 没有消费时：
        # {}
        if not isinstance(pending_consumptions, dict):
            return {
                "status": "error",
                "error_type": "PendingConsumptionFormatError",
                "data": None,
                "message": f"{table_id} 号桌的待结算消费应该是字典"
            }

        # ==================== 逐条检查这张桌子的消费记录 ====================

        for consumption_key, consumption in pending_consumptions.items():

            # 每条具体消费必须是字典。
            if not isinstance(consumption, dict):
                return {
                    "status": "error",
                    "error_type": "ConsumptionRecordFormatError",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的消费记录 "
                        f"{consumption_key} 格式错误，应该是字典"
                    )
                }

            # 每条消费内部都必须保留消费记录编号。
            if "消费记录编号" not in consumption:
                return {
                    "status": "error",
                    "error_type": "ConsumptionIdMissing",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的消费记录 "
                        f"{consumption_key} 缺少消费记录编号"
                    )
                }

            consumption_id = consumption["消费记录编号"]

            # 内部消费记录编号必须是非空字符串。
            if (
                not isinstance(consumption_id, str)
                or not consumption_id.strip()
            ):
                return {
                    "status": "error",
                    "error_type": "ConsumptionIdFormatError",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的消费记录 "
                        f"{consumption_key} 编号格式错误"
                    )
                }

            # 后续判断消费种类时需要读取 类型。
            if "类型" not in consumption:
                return {
                    "status": "error",
                    "error_type": "ConsumptionTypeMissing",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的消费记录 "
                        f"{consumption_key} 缺少消费类型"
                    )
                }

            consumption_type = consumption["类型"]

            # 消费类型必须是非空字符串。
            if (
                not isinstance(consumption_type, str)
                or not consumption_type.strip()
            ):
                return {
                    "status": "error",
                    "error_type": "ConsumptionTypeFormatError",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的消费记录 "
                        f"{consumption_key} 类型格式错误"
                    )
                }

    # ==================== 第三步：返回检查完成的消费框 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": table_consumption_boxes,
        "message": "所有桌台消费数据获取成功"
    }

def two_get_table_consumption_box(table_consumption_boxes: dict,table_id: str) -> dict:
    # 获取并检查指定桌子的消费框
    #
    # 前一步 two_get_all_table_consumption_boxes() 已经完成：
    # 1. 排除 _说明。
    # 2. 确认所有桌子的消费框是字典。
    # 3. 确认每张桌子都有 待结算消费。
    # 4. 确认待结算消费使用新的字典结构。
    #
    # 本函数负责：
    # 1. 根据桌号取得指定桌子的消费框。
    # 2. 检查该桌每条消费记录的基础结构。
    # 3. 检查内部消费记录编号是否重复。
    # 4. 检查外层标签和内部消费记录编号是否一致。
    # 5. 检查台位费的关台时间和金额状态是否一致。
    # 6. 返回已经检查完成的指定桌子消费框。
    #
    # 本函数不负责：
    # 1. 不寻找未关台的台位费。
    # 2. 不判断桌子是否占用。
    # 3. 不写入关台时间。
    # 4. 不计算台位费。
    # 5. 不修改数据，也不写文件。

    # JSON 中的桌号使用字符串作为 key。
    table_id = str(table_id)

    # ==================== 第一步：取得指定桌子的消费框 ====================

    if table_id not in table_consumption_boxes:
        return {
            "status": "error",
            "error_type": "TableConsumptionBoxNotFound",
            "data": None,
            "message": f"待结算消费中不存在 {table_id} 号桌的消费框"
        }

    table_consumption_box = table_consumption_boxes[table_id]

    # 上一步已经确认这里是字典，
    # 所以不重复检查 table_consumption_box 的格式。
    pending_consumptions = table_consumption_box["待结算消费"]

    # ==================== 第二步：准备检查消费编号 ====================

    # 用来记录这张桌子已经出现过的内部消费记录编号。
    #
    # 集合中的元素不可重复。
    # 如果后面再次遇到相同编号，
    # 就说明不同消费记录使用了同一个内部编号。
    seen_consumption_ids = set()

    # ==================== 第三步：逐条检查消费记录 ====================

    # consumption_key 是待结算消费外层的标签。
    # consumption 是标签对应的完整消费记录。
    for consumption_key, consumption in pending_consumptions.items():

        # 外层标签以后用于查找、修改和删除消费，
        # 因此必须是非空字符串。
        if (
            not isinstance(consumption_key, str)
            or not consumption_key.strip()
        ):
            return {
                "status": "error",
                "error_type": "ConsumptionKeyFormatError",
                "data": None,
                "message": f"{table_id} 号桌存在格式错误的消费记录标签"
            }

        # 每条具体消费必须是字典。
        if not isinstance(consumption, dict):
            return {
                "status": "error",
                "error_type": "ConsumptionRecordFormatError",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{consumption_key} 格式错误，应该是字典"
                )
            }

        # 每条消费内部都必须保留消费记录编号。
        if "消费记录编号" not in consumption:
            return {
                "status": "error",
                "error_type": "ConsumptionIdMissing",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{consumption_key} 缺少消费记录编号"
                )
            }

        consumption_id = consumption["消费记录编号"]

        # 内部消费记录编号必须是非空字符串。
        if (
            not isinstance(consumption_id, str)
            or not consumption_id.strip()
        ):
            return {
                "status": "error",
                "error_type": "ConsumptionIdFormatError",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{consumption_key} 编号格式错误"
                )
            }

        # 检查内部消费记录编号是否重复。
        if consumption_id in seen_consumption_ids:
            return {
                "status": "error",
                "error_type": "ConsumptionIdDuplicate",
                "data": None,
                "message": (
                    f"{table_id} 号桌存在重复的消费记录编号："
                    f"{consumption_id}"
                )
            }

        # 当前编号第一次出现，将它加入已见编号集合。
        seen_consumption_ids.add(consumption_id)

        # 外层标签和内部编号必须一致，
        # 后续才能使用同一个编号查找、修改和删除消费。
        if consumption_key != consumption_id:
            return {
                "status": "error",
                "error_type": "ConsumptionIdMismatch",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录编号不一致："
                    f"外层标签是 {consumption_key}，"
                    f"内部编号是 {consumption_id}"
                )
            }

        # 后续判断消费种类时需要读取 类型。
        if "类型" not in consumption:
            return {
                "status": "error",
                "error_type": "ConsumptionTypeMissing",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{consumption_key} 缺少消费类型"
                )
            }

        consumption_type = consumption["类型"]

        if (
            not isinstance(consumption_type, str)
            or not consumption_type.strip()
        ):
            return {
                "status": "error",
                "error_type": "ConsumptionTypeFormatError",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{consumption_key} 类型格式错误"
                )
            }

        # ==================== 第四步：检查台位费状态 ====================

        if consumption_type == "台位费":

            # 台位费必须保存关台时间。
            if "关台时间" not in consumption:
                return {
                    "status": "error",
                    "error_type": "TableFeeEndTimeMissing",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的台位费记录 "
                        f"{consumption_key} 缺少关台时间"
                    )
                }

            # 台位费必须保存金额字段。
            # 尚未关台时，金额应该保存为 None。
            if "金额" not in consumption:
                return {
                    "status": "error",
                    "error_type": "TableFeeAmountMissing",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的台位费记录 "
                        f"{consumption_key} 缺少金额"
                    )
                }

            # 分别取得关台时间和金额，
            # 用来检查台位费当前保存的状态是否完整。
            close_time = consumption["关台时间"]
            amount = consumption["金额"]

            # 没有关台时间却已经有金额，
            # 说明尚未关台的消费被提前写入了金额。
            if close_time is None and amount is not None:
                return {
                    "status": "error",
                    "error_type": "TableFeeStatusMismatch",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的台位费记录 "
                        f"{consumption_key} 状态异常："
                        "没有关台时间，但已经存在金额"
                    )
                }

            # 已经有了关台时间却仍然没有金额，
            # 说明关台流程没有完整保存台费计算结果。
            if close_time is not None and amount is None:
                return {
                    "status": "error",
                    "error_type": "TableFeeStatusMismatch",
                    "data": None,
                    "message": (
                        f"{table_id} 号桌的台位费记录 "
                        f"{consumption_key} 状态异常："
                        "已经有关台时间，但金额仍为空"
                    )
                }

    # ==================== 第五步：返回指定桌子的消费框 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": table_consumption_box,
        "message": f"已获取并检查 {table_id} 号桌的消费框"
    }

def two_get_frontend_table_basic_info(
    table_consumption_box: dict,
    table_id: str
) -> dict:
    # 获取前端刷新需要的单桌基础信息。
    #
    # 前提：
    # 1. table_consumption_box 已经由前面的取数函数取得并检查。
    # 2. 消费框中的“待结算消费”是字典。
    #
    # 本函数只负责：
    # 1. 寻找当前唯一一条未关台的台位消费。
    # 2. 提取桌号、是否开台、开台时间和每小时价格。
    # 3. 返回前端统一刷新需要的原始业务数据。
    #
    # 本函数不读取文件、不修改数据、不保存文件，
    # 也不转换时间、价格或计算台费。

    # JSON 中的桌号使用字符串作为 key。
    table_id = str(table_id)

    # ==================== 第一步：取得待结算消费字典 ====================

    pending_consumptions = table_consumption_box["待结算消费"]

    # ==================== 第二步：筛选未关台的台位费 ====================

    open_table_consumptions = []

    for consumption in pending_consumptions.values():
        if (
            consumption["类型"] == "台位费"
            and consumption["关台时间"] is None
        ):
            open_table_consumptions.append(consumption)

    # ==================== 第三步：处理数据异常 ====================

    if len(open_table_consumptions) > 1:
        return {
            "status": "error",
            "error_type": "MultipleOpenTableConsumptions",
            "data": None,
            "message": (
                f"{table_id} 号桌存在多条未关台的台位消费，"
                "无法确定当前桌台基础信息"
            )
        }

    # ==================== 第四步：返回未开台状态 ====================

    if len(open_table_consumptions) == 0:
        return {
            "status": "success",
            "error_type": None,
            "data": {
                "table_id": table_id,
                "is_open": False,
                "start_time": None,
                "hour_price": None
            },
            "message": f"{table_id} 号桌当前未开台"
        }

    # ==================== 第五步：返回正在使用的桌台信息 ====================

    open_table_consumption = open_table_consumptions[0]

    return {
        "status": "success",
        "error_type": None,
        "data": {
            "table_id": table_id,
            "is_open": True,
            "start_time": open_table_consumption["开台时间"],
            "hour_price": open_table_consumption["每小时价格"]
        },
        "message": f"{table_id} 号桌基础信息获取成功"
    }


def two_get_frontend_checkout_options(
    table_consumption_box: dict,
    table_id: str
) -> dict:
    # 整理指定桌子“选择结账”所需的待结算消费。
    #
    # 调用前，table_consumption_box 已由两层取数函数取得并检查。
    # 本函数只提取展示和选择需要的数据，不修改消费、不创建订单。

    table_id = str(table_id)
    pending_consumptions = table_consumption_box["待结算消费"]
    checkout_options = []

    for consumption_id, consumption in pending_consumptions.items():
        consumption_type = consumption["类型"]
        amount = consumption.get("金额")
        amount_is_ready = type(amount) in (int, float)

        checkout_option = {
            "consumption_id": consumption_id,
            "consumption_type": consumption_type,
            "amount": amount,
            "selectable": amount_is_ready,
            "unavailable_message": None
        }

        if consumption_type == "台位费":
            table_is_closed = consumption["关台时间"] is not None
            checkout_option.update({
                "display_name": "台费",
                "start_time": consumption.get("开台时间"),
                "close_time": consumption["关台时间"],
                "hour_price": consumption.get("每小时价格"),
                "selectable": table_is_closed and amount_is_ready
            })

            if not table_is_closed:
                checkout_option["unavailable_message"] = "尚未关台，请先关台"
            elif not amount_is_ready:
                checkout_option["unavailable_message"] = "台费金额尚未确定"

        elif consumption_type == "商品":
            checkout_option.update({
                "display_name": consumption.get("商品名称", "商品"),
                "unit_price": consumption.get("单价"),
                "quantity": consumption.get("数量")
            })

            if not amount_is_ready:
                checkout_option["unavailable_message"] = "商品金额尚未确定"

        else:
            checkout_option["display_name"] = consumption_type

            if not amount_is_ready:
                checkout_option["unavailable_message"] = "消费金额尚未确定"

        checkout_options.append(checkout_option)

    return {
        "status": "success",
        "error_type": None,
        "data": {
            "table_id": table_id,
            "消费明细": checkout_options
        },
        "message": f"{table_id} 号桌结账明细获取成功"
    }


def two_get_empty_billiards_tables(table_consumption_boxes: dict) -> dict:
    # 获取当前可以开台的桌子
    #
    # 前一步 two_get_all_table_consumption_boxes() 已经完成：
    # 1. 排除 _说明。
    # 2. 确认每张桌子的消费框是字典。
    # 3. 确认每张桌子都有 待结算消费。
    # 4. 确认待结算消费使用字典结构。
    #
    # 本函数负责：
    # 1. 遍历所有桌子的待结算消费。
    # 2. 判断每张桌子当前是否被占用。
    # 3. 返回所有空闲桌子的桌号。
    #
    # 判断规则：
    # 存在 类型=台位费 且 关台时间=None 的消费记录，
    # 说明桌子当前被占用。
    #
    # 不存在这样的消费记录，
    # 说明桌子当前空闲。
    #
    # 本函数不负责：
    # 1. 不检查消费记录编号是否重复。
    # 2. 不检查外层标签和内部编号是否一致。
    # 3. 不修改数据，也不写文件。

    # 用来保存所有当前可以开台的桌号。
    empty_tables = []

    # ==================== 第一步：逐桌判断是否占用 ====================

    for table_id, table_box in table_consumption_boxes.items():

        pending_consumptions = table_box["待结算消费"]

        # 先假设当前桌子没有被占用。
        is_occupied = False

        # 新结构中的待结算消费是字典。
        #
        # 空桌判断只需要读取里面的消费记录，
        # 不需要使用外层的消费编号，
        # 因此这里使用 values()。
        for consumption in pending_consumptions.values():

            # 判断空桌需要读取消费记录中的字段，
            # 因此消费记录必须是字典。
            if not isinstance(consumption, dict):
                return {
                    "status": "error",
                    "error_type": "ConsumptionRecordFormatError",
                    "data": None,
                    "message": f"{table_id} 号桌存在格式错误的消费记录"
                }

            # 判断是否为台位费，需要读取 类型。
            if "类型" not in consumption:
                return {
                    "status": "error",
                    "error_type": "ConsumptionTypeMissing",
                    "data": None,
                    "message": f"{table_id} 号桌存在缺少消费类型的记录"
                }

            consumption_type = consumption["类型"]

            if (
                not isinstance(consumption_type, str)
                or not consumption_type.strip()
            ):
                return {
                    "status": "error",
                    "error_type": "ConsumptionTypeFormatError",
                    "data": None,
                    "message": f"{table_id} 号桌存在消费类型格式错误的记录"
                }

            # 商品消费不会占用桌子，直接检查下一条消费。
            if consumption_type != "台位费":
                continue

            # 台位费必须有 关台时间，
            # 否则无法判断桌子是否正在占用。
            if "关台时间" not in consumption:
                return {
                    "status": "error",
                    "error_type": "TableFeeEndTimeMissing",
                    "data": None,
                    "message": f"{table_id} 号桌存在缺少关台时间的台位费记录"
                }

            # 找到一条尚未关台的台位费，
            # 就可以确定该桌当前被占用。
            if consumption["关台时间"] is None:
                is_occupied = True
                break

        # 没有找到未关台的台位费，说明该桌可以开台。
        if not is_occupied:
            empty_tables.append(table_id)

    # ==================== 第二步：返回空闲桌子 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": empty_tables,
        "message": "空闲桌子列表获取成功"
    }

def two_create_flow_box(table_id: str, current_time: int, table_info: dict) -> dict:
    # 创建一条新的业务流程箱子
    # 一个流程箱子对应一次独立的台位消费流程
    # 后面的业务函数继续处理同一个 flow_box，不重新创建新的箱子

    # T = 台位费
    # 桌号 + 开台时间共同组成这一次台位消费的记录编号
    consumption_id = f"T_{table_id}_{current_time}"

    flow_box = {
        "桌子号码": table_id,

        "台位消费": {
            "消费记录编号": consumption_id,
            "类型": "台位费",
            "开台时间": current_time,
            "关台时间": None,
            "每小时价格": table_info["每小时价格"],
            "金额": None
        },

        "当前步骤": "已创建台位消费记录"
    }

    return {
        "status": "success",
        "error_type": None,
        "data": flow_box,
        "message": f"{table_id} 号桌业务流程箱子创建成功"
    }

def two_update_hall_order(ongoing_order: dict,flow_box: dict) -> dict:
    # 将流程箱子中的台位消费加入待结算消费，并保存文件
    #
    # 前提：
    # 1. ongoing_order 已经由前面的拿数据函数检查完成。
    # 2. 指定桌子的消费框真实存在，且已经确认可以开台。
    # 3. flow_box 已由 two_create_flow_box() 创建。
    # 4. flow_box 中已经有桌子号码和完整台位消费。
    #
    # 整体过程：
    # 取得本次台位消费
    # → 复制完整 Ongoing_order，作为本次要修改的数据
    # → 找到指定桌子的待结算消费
    # → 确认新消费编号尚未存在
    # → 复制本次台位消费，并加入待结算消费
    # → 将修改后的完整数据保存到文件

    # ==================== 第一步：取得本次台位消费 ====================

    table_id = flow_box["桌子号码"]
    table_consumption = flow_box["台位消费"]

    # 用这条消费内部的编号，作为待结算消费字典的外层 key。
    consumption_id = table_consumption["消费记录编号"]

    # ==================== 第二步：创建完整的工作副本 ====================

    # 这里复制的是整个 Ongoing_order 数据。
    #
    # ongoing_order 保留原样。
    # updated_ongoing_order 是本次实际要修改、最后要保存的数据。
    #
    # 这样，即使后面保存失败，
    # 传进来的 ongoing_order 也不会被本函数改变。
    updated_ongoing_order = copy.deepcopy(ongoing_order)

    # ==================== 第三步：定位需要修改的位置 ====================

    # 取得工作副本中，指定桌子的待结算消费字典。
    # 这一行没有再次复制数据。
    # pending_consumptions 指向的就是updated_ongoing_order 里面的这一部分字典。
    # 先确定新增消费的位置：
    # 完整数据 → 指定桌号 → 该桌的“待结算消费”字典。
    # 后面对 pending_consumptions 的修改，
    # 都发生在这张桌子的“待结算消费”里面。
    # 后面向 pending_consumptions 添加记录，
    # updated_ongoing_order 中也就有了这条记录。
    pending_consumptions = (
        updated_ongoing_order[table_id]["待结算消费"]
    )

    # ==================== 第四步：防止覆盖旧消费 ====================

    # 当前是在新增消费。
    # 如果编号已经存在，直接赋值会覆盖旧记录，
    # 因此必须在修改之前返回错误。
    if consumption_id in pending_consumptions:
        return {
            "status": "error",
            "error_type": "ConsumptionIdDuplicate",
            "data": None,
            "message": (
                f"{table_id} 号桌已经存在消费记录编号："
                f"{consumption_id}"
            )
        }

    # ==================== 第五步：复制一条消费，并加入内存数据 ====================

    # 下面这一整行按顺序完成两个动作：
    #
    # 1. 先执行等号右边：
    #    copy.deepcopy(table_consumption)
    #    将流程箱子中的这一条台位消费复制成独立的新字典。
    #
    # 2. 再执行等号左边的赋值：
    #    pending_consumptions[consumption_id] = 复制得到的新字典
    #    使用消费记录编号作为外层 key，把新字典放到这个编号下面。
    #
    # 执行完成后：
    # updated_ongoing_order 已经包含新增的台位消费。
    # 这就是本函数真正修改内存数据的位置。
    #
    # 新加入的消费与 flow_box 中的原消费彼此独立，
    # 后面修改其中一条，不会连带改变另一条。
    pending_consumptions[consumption_id] = copy.deepcopy(
        table_consumption
    )

    # ==================== 第六步：保存修改后的完整数据 ====================

    # 前面已经在内存中完成修改。
    # 现在把包含新消费的整份 updated_ongoing_order 交给底层保存。
    #
    # 底层先写临时文件，验证成功后，
    # 再用临时文件替换正式的 Ongoing_order.json。
    save_result = one_save_json_file(
        "Ongoing_order.json",
        updated_ongoing_order
    )

    if save_result["status"] != "success":
        return {
            "status": "error",
            "error_type": save_result["error_type"],
            "data": None,
            "message": (
                f"{table_id} 号桌台位消费保存失败："
                f"{save_result['message']}"
            )
        }

    # ==================== 第七步：返回已经保存的完整数据 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": updated_ongoing_order,
        "message": f"{table_id} 号桌台位消费已加入待结算消费"
    }

# ==================== 关台流程 ====================
def two_get_open_table_consumption(table_consumption_box: dict,table_id: str) -> dict:
    # 获取指定桌当前唯一一条未关台的台位消费。
    #
    # 前提：
    # 1. 前面的取数函数已经取得并检查指定桌子的消费框。
    # 2. 消费框中的“待结算消费”是字典。
    # 3. 每条消费的编号、类型和台位费关台时间等字段已检查。
    #
    # 本函数负责：
    # 1. 从待结算消费字典中筛选未关台的台位费。
    # 2. 确认筛选结果必须刚好只有一条。
    # 3. 复制这条真实消费，准备关台使用的 flow_box。
    #
    # 本函数不修改原消费框，也不保存文件。
    # 后续补时间、算金额，都处理 flow_box 中复制出来的消费。

    # JSON 中的桌号使用字符串作为 key。
    table_id = str(table_id)

    # ==================== 第一步：取得待结算消费字典 ====================

    # 定位到指定桌子消费框内部的“待结算消费”。
    # 这一行没有复制数据，也没有修改数据。
    # pending_consumptions 指向的就是该桌的待结算消费字典。
    pending_consumptions = table_consumption_box["待结算消费"]

    # ==================== 第二步：筛选未关台的台位费 ====================

    # 临时收集符合条件的消费，后面用于判断数量。
    # 这个列表只在本次函数处理中使用，不会保存到文件，
    # 也不会改变 pending_consumptions 原本的字典结构。
    open_table_consumptions = []

    # 待结算消费字典的 key 是消费记录编号，
    # value 才是编号对应的完整消费记录。值
    # 因此使用 values()，逐条取得消费记录进行判断。
    for consumption in pending_consumptions.values():

        # 只有“台位费，并且关台时间为 None”，
        # 才属于本次需要寻找的未关台消费。
        # 商品和已经关台的台位费都不会加入筛选结果。
        if (
            consumption["类型"] == "台位费"
            and consumption["关台时间"] is None
        ):
            # 这里只把找到的记录收集起来，尚未修改记录。
            open_table_consumptions.append(consumption)

    # ==================== 第三步：确认必须恰好找到一条 ====================

    # 待结算消费为空字典，或其中没有未关台台位费，
    # 都会进入这个分支。
    if len(open_table_consumptions) == 0:
        return {
            "status": "error",
            "error_type": "OpenTableConsumptionNotFound",
            "data": None,
            "message": f"{table_id} 号桌没有未关台的台位消费，无法关台"
        }

    # 同一张桌子存在多条未关台台位费，属于数据异常。
    # 必须返回错误，不能随便选择其中一条继续处理。
    if len(open_table_consumptions) > 1:
        return {
            "status": "error",
            "error_type": "MultipleOpenTableConsumptions",
            "data": None,
            "message": (
                f"{table_id} 号桌存在多条未关台的台位消费，"
                "无法确定应该关闭哪一条"
            )
        }

    # ==================== 第四步：准备关台流程箱子 ====================

    # 前面已经确认临时筛选列表中恰好只有一条记录，
    # 所以这里的 [0] 就是本次关台目标。
    open_table_consumption = open_table_consumptions[0]

    # 将找到的真实消费深复制一份，再放进流程箱子。
    # 消费记录编号保持原样。
    #
    # 后面修改 flow_box 中的关台时间和金额，
    # 不会连带修改传进来的 table_consumption_box。
    flow_box = {
        "桌子号码": table_id,
        "台位消费": copy.deepcopy(open_table_consumption)
    }

    return {
        "status": "success",
        "error_type": None,
        "data": flow_box,
        "message": f"已获取 {table_id} 号桌当前未关台的台位消费"
    }

def two_add_close_time_to_table_consumption(flow_box: dict, close_time: int) -> dict:
    # 为当前台位消费补上关台时间
    #
    # 前提：
    # 1. flow_box 已由 two_get_open_table_consumption() 创建。
    # 2. flow_box 中的台位消费是唯一一条未关台台位费。
    # 3. close_time 是第一层获取的当前 Unix 时间戳。
    #
    # 本函数负责：
    # 1. 将关台时间写入 flow_box 中的台位消费。
    # 2. 返回更新后的 flow_box。
    #
    # 本函数不负责：
    # 1. 不计算台费金额。
    # 2. 不保存 Ongoing_order.json。
    # 3. 不修改原始 ongoing_order。

    # 第一步：取出当前需要关台的台位消费。
    table_consumption = flow_box["台位消费"]

    # 第二步：写入本次关台时间。
    table_consumption["关台时间"] = close_time

    # 第三步：返回已补上关台时间的流程箱子。
    return {
        "status": "success",
        "error_type": None,
        "data": flow_box,
        "message": f"{flow_box['桌子号码']} 号桌已补上关台时间"
    }

def two_calculate_table_fee(flow_box: dict) -> dict:
    # 计算台位费
    # flow_box 和台位消费的数据结构已经由前面的流程准备完成
    # 这里只负责根据：开台时间 + 关台时间 + 每小时价格
    # 计算台位消费金额
    #
    # 当前暂时不负责修改流程步骤
    # 不负责生成订单
    # 不负责清扫数据

    table_consumption = flow_box["台位消费"]

    start_time = table_consumption["开台时间"]
    end_time = table_consumption["关台时间"]
    unit_price = table_consumption["每小时价格"]

    # 还没有关台，不能计算最终台费
    if end_time is None:
        return {
            "status": "error",
            "error_type": "TableNotClosed",
            "data": None,
            "message": "当前台位消费还没有关台，无法计算台费"
        }

    # 这是业务数据本身不合理，不属于格式检查
    if start_time > end_time:
        return {
            "status": "error",
            "error_type": "StartTimeAfterEndTime",
            "data": None,
            "message": "开台时间大于关台时间，无法计算台费"
        }

    duration = end_time - start_time
    played_hours = duration / 3600

    table_fee = played_hours * unit_price

    # 当前收费规则：直接舍弃小数部分
    table_fee = int(table_fee)

    # 金额直接写回这一条台位消费记录
    table_consumption["金额"] = table_fee

    return {
        "status": "success",
        "error_type": None,
        "data": flow_box,
        "message": f"台位费计算成功，金额为 {table_fee}"
    }

def two_backup_consumption_to_core_blackbox(cursor, table_id, consumption_record) -> dict:
    # 把一条消费记录的最小核心数据备份到 MySQL 的核心备份表。
    # consumption_record 已经由前面的取数据 / 创建数据流程检查完成。
    # 这里只负责：
    # 1. 根据消费记录编号判断消费类型。
    # 2. 台位费调用 io 层写函数写入，写入由外层事务统一提交或回滚。
    #
    # T = 台位费
    # S = 商品（core_backup 表结构暂只覆盖台位费，商品备份保留占位）

    consumption_id = consumption_record["消费记录编号"]

    # 台位费
    if consumption_id.startswith("T_"):

        return one_insert_core_backup_to_mysql(
            cursor,
            consumption_id,
            table_id,
            consumption_record["开台时间"],
            consumption_record["关台时间"]
        )

    # 商品
    elif consumption_id.startswith("S_"):

        # 商品备份暂时不支持（core_backup 表结构只有台位费字段）。
        # 这一段是保留占位，商品流程以后再处理。
        return {
            "status": "error",
            "error_type": "ProductBackupNotSupported",
            "data": None,
            "message": "商品备份暂未支持（core_backup 表结构只覆盖台位费）"
        }

    # 其他前缀
    else:

        return {
            "status": "error",
            "error_type": "UnknownConsumptionType",
            "data": None,
            "message": f"无法识别消费记录编号：{consumption_id}"
        }

# ==================== 创建未支付订单与清扫消费 ====================
def two_check_selected_consumptions(table_consumption_box: dict, selected_data: dict) -> dict:
    # 检查前端选中的待结算消费是否可以结账。
    #
    # 前提：
    # table_consumption_box 已经由前面的取数函数完成基础检查：
    # 单桌消费框是字典，其中的“待结算消费”也是字典。
    #
    # 本函数负责：
    # 1. 检查前端传来的桌号和消费记录编号列表。
    # 2. 按编号直接取得真实消费。
    # 3. 检查选中的消费，包括内外编号是否一致、是否可以结账。
    # 4. 检查通过后返回已选消费列表，不再装箱。
    #
    # 前端只传选择的编号。
    # 商品名称、价格、数量和金额等内容，从后端真实记录中取得。
    # 本函数不修改待结算消费，也不保存文件。

    # ==================== 第一步：检查前端选择数据 ====================

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

    for consumption_id in consumption_id_list:
        if not isinstance(consumption_id, str) or not consumption_id.strip():
            return {
                "status": "error",
                "error_type": "SelectedConsumptionIdFormatError",
                "data": None,
                "message": "消费记录编号列表中存在格式错误的编号"
            }

    # 前面已经确认编号都是字符串，现在才能安全地放入集合。
    # 集合会去掉重复项；数量不同，说明前端重复选择了同一编号。
    if len(consumption_id_list) != len(set(consumption_id_list)):
        return {
            "status": "error",
            "error_type": "SelectedConsumptionIdDuplicate",
            "data": None,
            "message": "消费记录编号列表中存在重复编号"
        }

    # ==================== 第二步：取得该桌待结算消费字典 ====================

    # 单桌消费框已经由前面的取数函数取好并检查过。
    # 该桌是否存在由 two_get_table_consumption_box 负责判断，
    # 这里直接读取消费框内部的“待结算消费”字典。
    # 字典的 key 是消费记录编号，value 是完整消费记录。
    # 这一行只是取得数据，没有复制，也没有修改。
    pending_consumptions = table_consumption_box["待结算消费"]

    # 保存本次选中并检查通过的消费。
    # 后面的创建订单函数需要列表，因此这里继续使用列表。
    selected_consumptions = []

    # ==================== 第三步：按选中的编号直接取得消费 ====================

    for selected_consumption_id in consumption_id_list:

        # 直接检查编号是否存在于字典中，
        # 不再遍历该桌全部消费，逐条比较内部编号。
        if selected_consumption_id not in pending_consumptions:
            return {
                "status": "error",
                "error_type": "SelectedConsumptionNotFound",
                "data": None,
                "message": (
                    f"{table_id} 号桌不存在消费记录："
                    f"{selected_consumption_id}"
                )
            }

        # 使用外层消费编号，直接取得它对应的真实消费记录。
        found_consumption = pending_consumptions[selected_consumption_id]

        # ==================== 第四步：检查找到的真实消费 ====================

        if not isinstance(found_consumption, dict):
            return {
                "status": "error",
                "error_type": "ConsumptionRecordFormatError",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录 "
                    f"{selected_consumption_id} 应该是字典"
                )
            }

        # 取得记录内部保存的消费编号。
        # 如果缺少这个字段，get() 会返回 None，同样无法通过下面的核对。
        internal_consumption_id = found_consumption.get("消费记录编号")

        # 外层 key 与记录内部的编号必须一致。
        # 前端选中的编号已经确认是非空字符串，
        # 因此内部编号只有与它完全相同，才能通过。
        if internal_consumption_id != selected_consumption_id:
            return {
                "status": "error",
                "error_type": "ConsumptionIdMismatch",
                "data": None,
                "message": (
                    f"{table_id} 号桌的消费记录编号不一致："
                    f"外层编号是 {selected_consumption_id}，"
                    f"内部编号是 {internal_consumption_id}"
                )
            }

        consumption_type = found_consumption.get("类型")

        if not isinstance(consumption_type, str) or not consumption_type.strip():
            return {
                "status": "error",
                "error_type": "ConsumptionTypeFormatError",
                "data": None,
                "message": (
                    f"消费记录缺少有效的消费类型："
                    f"{selected_consumption_id}"
                )
            }

        # 台位费必须已经关台，才允许进入订单。
        # 不能只看金额是否存在，否则异常的未关台记录也可能被选入。
        if consumption_type == "台位费":
            if "关台时间" not in found_consumption:
                return {
                    "status": "error",
                    "error_type": "TableFeeEndTimeMissing",
                    "data": None,
                    "message": (
                        f"台位费记录缺少关台时间："
                        f"{selected_consumption_id}"
                    )
                }

            if found_consumption["关台时间"] is None:
                return {
                    "status": "error",
                    "error_type": "TableNotClosed",
                    "data": None,
                    "message": (
                        f"台位费尚未关台，无法结账："
                        f"{selected_consumption_id}"
                    )
                }

        if "金额" not in found_consumption:
            return {
                "status": "error",
                "error_type": "SelectedConsumptionAmountMissing",
                "data": None,
                "message": (
                    f"消费记录缺少金额，无法结账："
                    f"{selected_consumption_id}"
                )
            }

        amount = found_consumption["金额"]

        # None 表示金额尚未确定。
        # 金额 0 是有效金额，不能用 if not amount 判断。
        if amount is None:
            return {
                "status": "error",
                "error_type": "SelectedConsumptionAmountNotReady",
                "data": None,
                "message": (
                    f"消费记录金额尚未确定，无法结账："
                    f"{selected_consumption_id}"
                )
            }

        # 下一步需要累加金额，因此金额必须是整数或小数。
        # 这里也排除字符串、True 和 False。
        if type(amount) not in (int, float):
            return {
                "status": "error",
                "error_type": "SelectedConsumptionAmountFormatError",
                "data": None,
                "message": (
                    f"消费记录金额应该是数字："
                    f"{selected_consumption_id}"
                )
            }

        # 当前记录检查通过。
        # 复制一份放入已选消费列表，使已选消费
        # 与原待结算消费中的记录相互独立。
        selected_consumptions.append(
            copy.deepcopy(found_consumption)
        )

    # ==================== 第五步：返回检查通过的已选消费列表 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": selected_consumptions,
        "message": "已获取并检查前端选中的消费记录"
    }


def two_check_selected_consumptions_not_in_unpaid_orders(
    all_unpaid_orders: dict,
    selected_consumptions: list
) -> dict:
    # 再次确认待创建订单的消费没有进入已有未支付订单。
    #
    # 正常情况下，订单保存成功后会清扫待结算消费。
    # 此检查用于处理订单已保存、但后续清扫失败等边界情况，
    # 防止同一条消费被重复创建订单。

    if not isinstance(all_unpaid_orders, dict):
        return {
            "status": "error",
            "error_type": "UnpaidOrdersFormatError",
            "data": None,
            "message": "未支付订单总表格式错误，应该是字典"
        }

    selected_consumption_ids = {
        consumption["消费记录编号"]
        for consumption in selected_consumptions
    }

    for order_id, unpaid_bill in all_unpaid_orders.items():
        if not isinstance(unpaid_bill, dict):
            return {
                "status": "error",
                "error_type": "UnpaidOrderFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 格式错误"
            }

        consumption_details = unpaid_bill.get("消费明细")

        if not isinstance(consumption_details, list):
            return {
                "status": "error",
                "error_type": "UnpaidOrderConsumptionDetailsFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 的消费明细格式错误"
            }

        for consumption in consumption_details:
            if not isinstance(consumption, dict):
                return {
                    "status": "error",
                    "error_type": "UnpaidOrderConsumptionFormatError",
                    "data": None,
                    "message": f"未支付订单 {order_id} 存在格式错误的消费明细"
                }

            consumption_id = consumption.get("消费记录编号")

            if consumption_id in selected_consumption_ids:
                return {
                    "status": "error",
                    "error_type": "ConsumptionAlreadyInUnpaidOrder",
                    "data": None,
                    "message": (
                        f"消费记录 {consumption_id} 已存在于未支付订单 "
                        f"{order_id}，不能重复结账"
                    )
                }

    return {
        "status": "success",
        "error_type": None,
        "data": selected_consumptions,
        "message": "已确认选中消费尚未进入未支付订单"
    }


def two_get_frontend_unpaid_orders_info(all_unpaid_orders: dict) -> dict:
    # 整理“确认收款”前端选择和核对所需的未支付订单。
    #
    # 调用前，all_unpaid_orders 已由第三层从真实订单文件读取。
    # 本函数只提取展示字段，不修改订单、不确认收款，也不保存文件。
    # 订单编号内部使用“桌号_创建时间戳”规则，因此可从编号中取得
    # 前端展示所需的桌号和创建时间；订单编号本身不需要展示给用户。

    if not isinstance(all_unpaid_orders, dict):
        return {
            "status": "error",
            "error_type": "UnpaidOrdersFormatError",
            "data": None,
            "message": "未支付订单总表格式错误，应该是字典"
        }

    frontend_orders = []

    for order_id, unpaid_bill in all_unpaid_orders.items():
        # ==================== 第一步：检查订单编号和订单主体 ====================

        if not isinstance(order_id, str) or not order_id:
            return {
                "status": "error",
                "error_type": "UnpaidOrderIdFormatError",
                "data": None,
                "message": "未支付订单中存在格式错误的订单编号"
            }

        table_id, separator, created_at_text = order_id.rpartition("_")

        if (
            not separator
            or not table_id
            or not created_at_text.isdigit()
        ):
            return {
                "status": "error",
                "error_type": "UnpaidOrderIdFormatError",
                "data": None,
                "message": f"未支付订单编号格式错误：{order_id}"
            }

        if not isinstance(unpaid_bill, dict):
            return {
                "status": "error",
                "error_type": "UnpaidOrderFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 格式错误"
            }

        payment_status = unpaid_bill.get("支付状态")

        if not isinstance(payment_status, str):
            return {
                "status": "error",
                "error_type": "PaymentStatusFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 的支付状态格式错误"
            }

        # 已支付但尚未成功转存的订单不属于前台可收款订单，不返回前端。
        if payment_status == "已支付":
            continue

        if payment_status != "未支付":
            return {
                "status": "error",
                "error_type": "PaymentStatusFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 的支付状态无效"
            }

        consumption_details = unpaid_bill.get("消费明细")
        total_amount = unpaid_bill.get("总金额")

        if not isinstance(consumption_details, list):
            return {
                "status": "error",
                "error_type": "UnpaidOrderConsumptionDetailsFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 的消费明细格式错误"
            }

        if type(total_amount) not in (int, float):
            return {
                "status": "error",
                "error_type": "UnpaidOrderTotalAmountFormatError",
                "data": None,
                "message": f"未支付订单 {order_id} 的总金额格式错误"
            }

        # ==================== 第二步：整理每笔消费展示数据 ====================

        frontend_consumption_details = []

        for consumption in consumption_details:
            if not isinstance(consumption, dict):
                return {
                    "status": "error",
                    "error_type": "UnpaidOrderConsumptionFormatError",
                    "data": None,
                    "message": f"未支付订单 {order_id} 存在格式错误的消费明细"
                }

            consumption_type = consumption.get("类型")
            amount = consumption.get("金额")

            if not isinstance(consumption_type, str) or not consumption_type:
                return {
                    "status": "error",
                    "error_type": "ConsumptionTypeFormatError",
                    "data": None,
                    "message": f"未支付订单 {order_id} 的消费类型格式错误"
                }

            if type(amount) not in (int, float):
                return {
                    "status": "error",
                    "error_type": "ConsumptionAmountFormatError",
                    "data": None,
                    "message": f"未支付订单 {order_id} 的消费金额格式错误"
                }

            frontend_consumption = {
                "consumption_type": consumption_type,
                "amount": amount,
                "display_name": consumption_type
            }

            if consumption_type == "台位费":
                start_time = consumption.get("开台时间")
                close_time = consumption.get("关台时间")
                hour_price = consumption.get("每小时价格")

                if (
                    type(start_time) not in (int, float)
                    or type(close_time) not in (int, float)
                    or type(hour_price) not in (int, float)
                ):
                    return {
                        "status": "error",
                        "error_type": "TableFeeFormatError",
                        "data": None,
                        "message": f"未支付订单 {order_id} 的台费明细格式错误"
                    }

                frontend_consumption.update({
                    "display_name": "台费",
                    "start_time": start_time,
                    "close_time": close_time,
                    "hour_price": hour_price
                })

            elif consumption_type == "商品":
                product_name = consumption.get("商品名称")
                unit_price = consumption.get("单价")
                quantity = consumption.get("数量")

                if (
                    not isinstance(product_name, str)
                    or not product_name
                    or type(unit_price) not in (int, float)
                    or type(quantity) is not int
                    or quantity <= 0
                ):
                    return {
                        "status": "error",
                        "error_type": "ProductConsumptionFormatError",
                        "data": None,
                        "message": f"未支付订单 {order_id} 的商品明细格式错误"
                    }

                frontend_consumption.update({
                    "display_name": product_name,
                    "unit_price": unit_price,
                    "quantity": quantity
                })

            frontend_consumption_details.append(frontend_consumption)

        frontend_orders.append({
            "order_id": order_id,
            "table_id": table_id,
            "created_at": int(created_at_text),
            "total_amount": total_amount,
            "consumption_details": frontend_consumption_details
        })

    # 最新创建的订单优先展示，方便前台处理最近结账。
    frontend_orders.sort(
        key=lambda frontend_order: frontend_order["created_at"],
        reverse=True
    )

    return {
        "status": "success",
        "error_type": None,
        "data": {
            "未支付订单": frontend_orders
        },
        "message": "前端未支付订单数据获取成功"
    }


def two_create_unpaid_bill(table_id: str, selected_consumptions: list) -> dict:
    # 准备创建未支付订单需要落库的数据。
    #
    # 前一步已经完成：
    # 1. 找到客人本次选择的真实消费记录
    # 2. 确认每条消费都已经有确定金额
    #
    # 本函数负责：
    # 1. 计算本次结账总金额
    # 2. 取一次当前时间戳
    # 3. 生成订单编号（桌号_当前时间戳）
    # 4. 返回要落库的订单数据
    #
    # 本函数不负责：
    # 1. 不读取文件
    # 2. 不写入文件
    # 3. 不清扫待结算消费
    # 4. 不再组装 JSON 时代的“未支付订单对象”

    # 计算本次订单总金额。
    # 例如：台位费 30 元 + 可乐 6 元 = 36 元。
    total_amount = sum(
        consumption["金额"]
        for consumption in selected_consumptions
    )

    # 调用底层统一时间函数，获取当前秒级时间戳。
    # 时间戳只取一次，订单编号和“创建时间”共用同一个值。
    current_time = one_get_current_time()

    # 桌号 + 创建订单时间，组成订单编号。
    # 例如：1_1757688000
    order_id = f"{table_id}_{current_time}"

    # 组装要写入数据库的订单数据。
    # 不再组装 JSON 时代的“未支付订单对象”。
    order_data = {
        "桌子号码": table_id,
        "订单编号": order_id,
        "总金额": total_amount,
        "创建时间": current_time,
        "已选消费": selected_consumptions
    }

    return {
        "status": "success",
        "error_type": None,
        "data": order_data,
        "message": "订单数据准备成功"
    }

# ==================== 确认收款 ====================
def two_check_unpaid_order_for_payment(order_data: dict, payment_data: dict) -> dict:
    # 确认收款前的订单检查
    #
    # 这个函数负责：
    # 1. 检查前端传入的支付数据格式和订单编号
    # 2. 检查指定订单当前确实还是未支付
    #
    # 检查通过后只返回订单编号，不再装箱。
    #
    # 前端传入的数据例如：
    # {
    #     "订单编号": "1_1757688000"
    # }

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

    # 取出前端指定的订单编号。
    # 例如：order_id = "1_1757688000"
    order_id = payment_data["订单编号"]

    if not isinstance(order_id, str) or not order_id:
        return {
            "status": "error",
            "error_type": "PaymentOrderIdFormatError",
            "data": None,
            "message": "前端传入的订单编号格式错误"
        }

    # 支付状态是确认收款时必须使用的字段。
    if "支付状态" not in order_data:
        return {
            "status": "error",
            "error_type": "PaymentStatusMissing",
            "data": None,
            "message": f"订单缺少支付状态：{order_id}"
        }

    # 只有未支付订单才能确认收款。
    if order_data["支付状态"] != "未支付":
        return {
            "status": "error",
            "error_type": "OrderNotUnpaid",
            "data": None,
            "message": f"订单当前不是未支付状态，无法确认收款：{order_id}"
        }

    return {
        "status": "success",
        "error_type": None,
        "data": {
            "订单编号": order_id
        },
        "message": "订单检查通过，可以确认收款"
    }

# ==================== 商品消费流程 ====================
def two_check_products(products: dict) -> dict:
    # 检查商品总表
    # 作用：只做检查，不再切数据
    # （products 商品表已经由上一层从 MySQL 取好并传入）
    #
    # 这个函数负责：
    # 1. 检查 products 商品表是不是字典。
    # 2. 检查每个商品的资料是不是字典。
    # 3. 检查每个商品有没有 商品名称、单价、库存，
    #    并检查这三个字段的格式和取值范围。
    # 4. 检查通过后原样返回 products。
    #
    # 这个函数不负责：
    # 1. 不取得某一个具体商品
    # 2. 不检查购买数量，也不检查库存够不够卖
    # 3. 不创建商品消费
    # 4. 不修改数据，也不写文件
    #
    # 后续函数会在 products 中，
    # 根据前端指定的商品编号取得某一个具体商品。

    # ==================== 第一步：检查整体结构 ====================

    # 商品总表必须是字典。
    if not isinstance(products, dict):
        return {
            "status": "error",
            "error_type": "ProductsFormatError",
            "data": None,
            "message": "products 商品总表格式错误，应该是字典"
        }

    # ==================== 第二步：逐个检查商品 ====================

    for product_id, product_info in products.items():

        # 每个商品的资料必须是字典。
        if not isinstance(product_info, dict):
            return {
                "status": "error",
                "error_type": "ProductInfoFormatError",
                "data": None,
                "message": f"{product_id} 商品资料格式错误，应该是字典"
            }

        # 每个商品都必须带有 商品名称。
        if "商品名称" not in product_info:
            return {
                "status": "error",
                "error_type": "ProductNameMissing",
                "data": None,
                "message": f"{product_id} 商品缺少 商品名称 字段"
            }

        product_name = product_info["商品名称"]

        # 商品名称必须是非空字符串。
        if (
            not isinstance(product_name, str)
            or not product_name.strip()
        ):
            return {
                "status": "error",
                "error_type": "ProductNameFormatError",
                "data": None,
                "message": f"{product_id} 商品的 商品名称 格式错误，应该是非空字符串"
            }

        # 每个商品都必须带有 单价。
        if "单价" not in product_info:
            return {
                "status": "error",
                "error_type": "ProductUnitPriceMissing",
                "data": None,
                "message": f"{product_id} 商品缺少 单价 字段"
            }

        unit_price = product_info["单价"]

        # 单价必须是数字。
        # 这里用 type() 判断，None、字符串、True 和 False 都会被排除。
        if type(unit_price) not in (int, float):
            return {
                "status": "error",
                "error_type": "ProductUnitPriceFormatError",
                "data": None,
                "message": f"{product_id} 商品的 单价 格式错误，应该是数字"
            }

        # 单价必须大于 0。
        if unit_price <= 0:
            return {
                "status": "error",
                "error_type": "ProductUnitPriceInvalid",
                "data": None,
                "message": f"{product_id} 商品的 单价 必须大于 0"
            }

        # 每个商品都必须带有 库存。
        if "库存" not in product_info:
            return {
                "status": "error",
                "error_type": "ProductStockMissing",
                "data": None,
                "message": f"{product_id} 商品缺少 库存 字段"
            }

        product_stock = product_info["库存"]

        # 库存必须是整数。
        # 这里用 type() 判断，True 和 False 是布尔，会被排除。
        if type(product_stock) is not int:
            return {
                "status": "error",
                "error_type": "ProductStockFormatError",
                "data": None,
                "message": f"{product_id} 商品的 库存 格式错误，应该是整数"
            }

        # 库存不能小于 0。
        # 0 表示商品卖完了，是合法库存。
        if product_stock < 0:
            return {
                "status": "error",
                "error_type": "ProductStockInvalid",
                "data": None,
                "message": f"{product_id} 商品的 库存 不能小于 0"
            }

    # ==================== 第三步：返回检查通过的商品总表 ====================

    return {
        "status": "success",
        "error_type": None,
        "data": products,
        "message": "商品总表获取成功"
    }


def two_get_frontend_products_info(products: dict) -> dict:
    # 整理前端展示商品需要的字段。
    #
    # 调用前，products 已由 io 层从 MySQL 取回，并由 two_check_products() 检查。
    # 本函数只提取和整理字段，不读取文件、不修改库存，也不处理商品消费。

    frontend_products = {}

    for product_id, product_info in products.items():
        frontend_products[product_id] = {
            "product_id": product_id,
            "product_name": product_info["商品名称"],
            "price": product_info["单价"],
            "stock": product_info["库存"]
        }

    return {
        "status": "success",
        "error_type": None,
        "data": frontend_products,
        "message": "前端商品信息整理成功"
    }


def two_get_product_info(product_info: dict, product_id: str) -> dict:
    # 获取某一个商品的信息
    #
    # 这个函数负责：
    # 1. 接收 io 层已经取出的单个商品资料 product_info
    # 2. 检查这份商品资料是否具备后续业务需要的字段
    #
    # 前一步 io 层函数 one_load_product_by_id_from_mysql() 已经完成：
    # 商品资料已经按商品编号单独取出，
    # 本函数不再从商品总表大字典里切数据。
    #
    # 本函数不负责：
    # 1. 不检查购买数量
    # 2. 不检查库存是否足够
    # 3. 不创建商品消费
    # 4. 不修改库存，也不写文件

    # 后续检查库存、创建商品消费时，
    # 必须使用商品名称、单价和库存。
    required_keys = ["商品名称", "单价", "库存"]

    for key in required_keys:
        if key not in product_info:
            return {
                "status": "error",
                "error_type": "ProductInfoMissingField",
                "data": None,
                "message": f"{product_id} 商品资料缺少字段：{key}"
            }

    return {
        "status": "success",
        "error_type": None,
        "data": product_info,
        "message": f"成功获取商品：{product_info['商品名称']}"
    }
#需要前端信息，比对数目
def two_check_product_can_be_ordered(product_id: str, product_info: dict, product_count: int) -> dict:
    # 检查商品是否可以销售
    #
    # 前一步已经完成：
    # 1. 根据前端传来的商品编号，取得真实商品资料
    # 2. 已经确认商品编号存在
    #
    # 本函数负责：
    # 1. 检查前端购买数量是否正确
    # 2. 检查购买数量是否超过真实库存
    # 3. 创建商品销售流程使用的 flow_box
    #
    # 本函数不负责：
    # 1. 不创建商品消费记录
    # 2. 不扣减库存
    # 3. 不写入待结算消费
    # 4. 不写文件

    # 商品数量必须是整数。
    # 例如：product_count = 2
    if not isinstance(product_count, int):
        return {
            "status": "error",
            "error_type": "ProductCountFormatError",
            "data": None,
            "message": "商品数量格式错误，应该是整数"
        }

    # 购买数量必须大于 0。
    if product_count <= 0:
        return {
            "status": "error",
            "error_type": "ProductCountInvalid",
            "data": None,
            "message": "商品数量必须大于 0"
        }

    # 取出后端商品资料中的真实库存。
    product_stock = product_info["库存"]

    # 库存必须是整数。
    if not isinstance(product_stock, int):
        return {
            "status": "error",
            "error_type": "ProductStockFormatError",
            "data": None,
            "message": f"{product_info['商品名称']} 的库存格式错误，应该是整数"
        }

    # 前端要买的数量不能超过真实库存。
    if product_count > product_stock:
        return {
            "status": "error",
            "error_type": "ProductStockNotEnough",
            "data": None,
            "message": (
                f"{product_info['商品名称']} 库存不足，"
                f"当前库存 {product_stock}，客人想要 {product_count}"
            )
        }

    # 商品编号、真实商品资料、购买数量都已经确认可用。
    # 将它们放进商品销售流程使用的 flow_box。
    flow_box = {
        "商品编号": product_id,
        "商品信息": product_info,
        "购买数量": product_count
    }

    return {
        "status": "success",
        "error_type": None,
        "data": flow_box,
        "message": f"{product_info['商品名称']} 可以销售，数量 {product_count}"
    }
#准备要写进去的东西
def two_create_product_consumption(table_id: str, flow_box: dict, current_time: int) -> dict:
    # 创建一条商品待结算消费
    #
    # 前一步已经完成：
    # 1. 已根据商品编号取得真实商品资料
    # 2. 已确认购买数量正确
    # 3. 已确认商品库存足够
    #
    # 本函数负责：
    # 1. 取得商品编号、商品资料和购买数量
    # 2. 组装一条独立的商品消费记录
    # 3. 用 controller 传进来的时间戳生成商品消费记录编号
    # 4. 返回组装好的商品消费记录，交给 controller 的事务插库
    #
    # 本函数不负责：
    # 1. 不检查桌子是否存在
    # 2. 不写入任何文件
    # 3. 不扣减库存
    # 4. 不访问数据库
    #
    # current_time 由 controller 一次性取好再传进来，
    # 消费记录编号和数据库的“创建时间”共用同一个时间戳。

    # 取出经过前一步检查的商品编号。
    # 例如：product_id = "P_001"
    product_id = flow_box["商品编号"]

    # 取出经过前一步检查的真实商品资料。
    # 例如：
    # {
    #     "商品名称": "可乐",
    #     "单价": 3,
    #     "库存": 20
    # }
    product_info = flow_box["商品信息"]

    # 取出客人本次购买数量。
    # 例如：product_count = 2
    product_count = flow_box["购买数量"]

    # 从真实商品资料中取出商品名称和单价。
    product_name = product_info["商品名称"]
    product_price = product_info["单价"]

    # 商品消费金额 = 商品单价 × 购买数量。
    # 例如：3 元 × 2 瓶 = 6 元。
    total_amount = product_price * product_count

    # S 代表商品消费。
    # 桌子号码 + 创建商品消费的时间，组成消费记录编号。
    # 例如：S_1_1757688000
    consumption_id = f"S_{table_id}_{current_time}"

    # 组装一条完整商品消费记录。
    #
    # 桌子号码和消费记录字段放在同一层，
    # controller 的事务插库时可以直接按键取值。
    product_consumption = {
        "桌子号码": table_id,
        "消费记录编号": consumption_id,
        "类型": "商品",
        "商品编号": product_id,
        "商品名称": product_name,
        "单价": product_price,
        "数量": product_count,
        "金额": total_amount
    }

    return {
        "status": "success",
        "error_type": None,
        "data": product_consumption,
        "message": f"已创建商品消费：{product_name} x {product_count}"
    }
