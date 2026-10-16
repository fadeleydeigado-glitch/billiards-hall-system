import json
import time
import os
import pymysql
from decimal import Decimal


# ==================== MySQL 数据库配置（新增） ====================
# 说明：以下配置与助手只服务于文件末尾新增的 MySQL 函数，
#       旧版 JSON 函数与新增函数完全独立，互不调用。
# 所有新增 MySQL 函数共用这一份连接参数，不要在每个函数里重复写连接参数。
#
# 【密码安全提醒】为了防止密码泄露：请确保在本地系统环境变量中设置 MYSQL_PASSWORD，
# 或在本地配置 .env 文件。切勿在代码中硬编码密码提交到 GitHub。
# host / user / database 也支持用环境变量 MYSQL_HOST、MYSQL_USER、MYSQL_DATABASE 覆盖。
DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "billiards_hall"),
    "charset": "utf8mb4"
}


def one_get_mysql_connection():
    # 作用：内部连接助手，按照 DB_CONFIG 建立并返回一个 pymysql 连接。
    # 接收：无。
    # 返回：pymysql 连接对象。
    # 说明：仅供新增的 MySQL 函数内部使用，连接用完必须由调用方 close。
    #       连接失败时本助手会抛出数据库异常，由调用方的 try 统一捕获并转换。
    return pymysql.connect(**DB_CONFIG)


def one_convert_mysql_error(error: Exception, action: str) -> dict:
    # 作用：内部异常转换助手，把数据库异常转换成统一的四键错误字典。
    # 接收：捕获到的数据库异常、本次操作的中文描述（例如 "读取商品总表"）。
    # 返回：四键结果字典（status="error"，error_type 为异常类型名）。
    # 说明：这里允许打印错误日志，方便排查故障。
    print(f"【MySQL 故障】{action}失败：{error}")
    return {
        "status": "error",
        "error_type": type(error).__name__,
        "data": None,
        "message": f"{action}失败：{error}"
    }


def one_convert_mysql_value(value):
    # 作用：内部类型转换助手，把 MySQL 读出来的值转换成 JSON 时代的类型习惯。
    # 接收：从数据库读出来的单个值。
    # 返回：DECIMAL 转成 int（没有小数）或 float（有小数）；BIGINT 等整数保持 int；None 保持 None。
    # 说明：business 层里写着"金额必须是 int 或 float"的检查，不转换会大面积误判。
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == int(value):
            return int(value)
        return float(value)
    return value


def one_load_json_file(file_name: str) -> dict:
    # 读取 JSON 文件，并返回统一格式的结果字典
    try:
        absolute_path = one_get_safe_path(file_name)  # 先获取安全的绝对路径

        print(f"【底层 I/O】:大掌柜正在执行物理读取 -> {file_name}")

        with open(absolute_path, 'r', encoding='utf-8') as file:  # 以只读模式打开 JSON 文件
            file_content = json.load(file)  # 把 JSON 文件内容加载成 Python 数据

        print(f"【底层 I/O】:成功将 {file_name} 加载到内存")
        return {
            "status": "success",
            "error_type": None,
            "data": file_content,
            "message": f"成功加载文件 {file_name}"
        }

    # 文件不存在
    except FileNotFoundError:
        print(f"【IO 故障】：物理坐标点未找到 {file_name} 文件。")
        return {
            "status": "error",
            "error_type": "FileNotFoundError",
            "data": None,
            "message": f"物理硬盘中确实找不到 {file_name} 文件，读取失败。"
        }

    # JSON 文件格式错误，比如空文件、半截文件、括号不匹配
    except json.JSONDecodeError:
        print(f"【IO 故障】：{file_name} 文件内容损毁、为空壳，或不是合法 JSON 格式。")
        return {
            "status": "error",
            "error_type": "JSONDecodeError",
            "data": None,
            "message": f"【致命故障】：{file_name} 损坏、为空或格式错误，大掌柜无法解析。"
        }

    # 系统不允许当前 Python 程序读取这个文件
    except PermissionError:
        print(f"【IO 故障】：没有权限读取 {file_name} 文件。")
        return {
            "status": "error",
            "error_type": "PermissionError",
            "data": None,
            "message": f"系统没有权限读取 {file_name} 文件，请检查文件权限。"
        }

    # 传进来的路径其实是一个文件夹，不是 JSON 文件
    except IsADirectoryError:
        print(f"【IO 故障】：{file_name} 是一个文件夹，不是 JSON 文件。")
        return {
            "status": "error",
            "error_type": "IsADirectoryError",
            "data": None,
            "message": f"{file_name} 是文件夹，不是可读取的 JSON 文件。"
        }

    # one_get_safe_path 主动拦截了不安全路径，比如 "../xxx.json"
    except ValueError as e:
        print(f"【IO 故障】：文件路径不安全：{e}")
        return {
            "status": "error",
            "error_type": "UnsafePathError",
            "data": None,
            "message": str(e)
        }

    # 其他没预料到的错误，统一兜底，避免程序直接崩掉
    except Exception as e:
        print(f"【IO 故障】：读取 {file_name} 时发生未知错误：{e}")
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "data": None,
            "message": f"读取 {file_name} 时发生未知错误：{e}"
        }

def one_save_json_file(file_name: str, file_content: dict) -> dict:
#正式文件：先不动
#临时文件：先试着写
#临时文件：再试着读回来验证
#验证成功：最后才替换正式文件,失败：正式文件保持原样,再清理临时文件
    temp_path = None  # 临时文件路径，先设为 None，防止前面出错后 finally 又报错

    try:
        absolute_path = one_get_safe_path(file_name)  # 获取安全的正式文件路径
        temp_path = absolute_path + ".tmp"  # 临时文件路径，比如 data.json.tmp

        print(f"【底层 I/O】:大掌柜正在执行安全写入 -> {file_name}")

        # 第一步：先写入临时文件，不直接碰原文件
        # 这样即使 json.dump 失败，坏的也是 .tmp 文件，不是正式文件
        with open(temp_path, 'w', encoding='utf-8') as file:
            json.dump(file_content, file, ensure_ascii=False, indent=4)

        # 第二步：把刚写出来的临时文件再读一遍
        # 目的是确认它确实是合法 JSON，不是空壳，也不是半截文件
        with open(temp_path, 'r', encoding='utf-8') as file:
            json.load(file)

        # 第三步：验证成功后，用临时文件替换正式文件
        # os.replace 可以理解成：把草稿纸确认没问题后，再替换正式文件
        os.replace(temp_path, absolute_path)

        print(f"【底层 I/O】:成功将 {file_name} 安全写入文件")
        return {
            "status": "success",
            "error_type": None,
            "data": file_content,
            "message": f"成功将 {file_name} 写入文件"
        }

    # file_content 里面有不能转换成 JSON 的东西，比如函数、特殊对象等
    except TypeError as e:
        return {
            "status": "error",
            "error_type": "JSONSerializeError",
            "data": None,
            "message": f"写入失败：传入的数据不能转换成 JSON:{e}"
        }

    # 系统不允许当前 Python 程序写入这个文件或文件夹
    except PermissionError as e:
        return {
            "status": "error",
            "error_type": "PermissionError",
            "data": None,
            "message": f"写入失败：没有权限写入 {file_name}：{e}"
        }

    # 传进来的路径是文件夹，不是文件
    except IsADirectoryError as e:
        return {
            "status": "error",
            "error_type": "IsADirectoryError",
            "data": None,
            "message": f"写入失败：{file_name} 是文件夹，不是文件：{e}"
        }

    # one_get_safe_path 主动拦截了不安全路径，比如 "../xxx.json"
    except ValueError as e:
        return {
            "status": "error",
            "error_type": "UnsafePathError",
            "data": None,
            "message": str(e)
        }

    # 操作系统层面的文件错误，比如磁盘、路径、替换文件失败等
    except OSError as e:
        return {
            "status": "error",
            "error_type": "OSError",
            "data": None,
            "message": f"写入失败：系统文件操作错误：{e}"
        }

    # 其他没预料到的错误，统一兜底，避免程序直接崩掉
    except Exception as e:
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "data": None,
            "message": f"写入文件失败：{e}"
        }

    finally:
        # 如果写入失败，可能会留下 .tmp 临时文件
        # 这里负责把残留的临时文件清理掉
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
def one_get_safe_path(file_name: str) -> str:
    # 根据当前 io_layer.py 所在的文件夹，拼出目标文件的安全绝对路径
    # 默认前提：JSON 数据文件和 io_layer.py 在同一个项目文件夹里，比如都在 180 文件夹里

    current_code_path = os.path.abspath(__file__)  # abspath = absolute path，获取当前代码文件的绝对路径
    base_dir = os.path.dirname(current_code_path)  # dirname = directory name，获取当前代码文件所在的文件夹

    final_path = os.path.abspath(os.path.join(base_dir, file_name))
    # join = 拼接路径
    # abspath = 再转成绝对路径
    # 例如：base_dir 是 C:\Users\Lenovo\Desktop\180
    # file_name 是 data.json
    # final_path 就是 C:\Users\Lenovo\Desktop\180\data.json

    # 防止 "../xxx.json" 这种路径逃出当前项目文件夹
    # ".." 表示上一级目录，可能让程序跑到 180 文件夹外面
    if os.path.commonpath([base_dir, final_path]) != base_dir:
        raise ValueError(f"非法文件路径：{file_name}")

    return final_path

def one_get_current_time() -> int:
    # 获取当前时间戳，返回 int 类型
    # time.time() 原本是小数，这里转成整数，方便后面保存到 JSON
    return int(time.time())
    
def one_change_time_format(timestamp: int) -> str:
    # 把时间戳转换成人类容易阅读的时间格式
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


# ==================== 新增：MySQL 读函数（共 7 个） ====================

def one_load_billiards_tables_from_mysql() -> dict:
    # 作用：读取整张 billiards_tables 表（桌台配置，表很小，直接全量读）。
    # 接收：无。
    # 返回：四键结果字典；data 为 {桌号: {"每小时价格": 数字}}；空表时 data 为 {}，status 仍为 "success"。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, `每小时价格` FROM billiards_tables")
            rows = cursor.fetchall()

        billiards_tables = {}
        for table_id, hour_price in rows:
            billiards_tables[str(table_id)] = {
                "每小时价格": one_convert_mysql_value(hour_price)
            }

        return {
            "status": "success",
            "error_type": None,
            "data": billiards_tables,
            "message": "桌台配置表读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取桌台配置表")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_products_from_mysql() -> dict:
    # 作用：读取整张 products 表（商品配置，表很小，直接全量读）。
    # 接收：无。
    # 返回：四键结果字典；data 为 {商品编号: {"商品名称": ..., "单价": 数字, "库存": 整数}}；空表时 data 为 {}，status 仍为 "success"。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, `商品名称`, `单价`, `库存` FROM products")
            rows = cursor.fetchall()

        products = {}
        for product_id, product_name, unit_price, stock in rows:
            products[str(product_id)] = {
                "商品名称": product_name,
                "单价": one_convert_mysql_value(unit_price),
                "库存": one_convert_mysql_value(stock)
            }

        return {
            "status": "success",
            "error_type": None,
            "data": products,
            "message": "商品总表读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取商品总表")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_product_by_id_from_mysql(product_id) -> dict:
    # 作用：按商品编号读取单个商品。
    # 接收：商品编号。
    # 返回：四键结果字典；data 为 {"商品名称": ..., "单价": 数字, "库存": 整数}；
    #       找不到时 status="error"，error_type="ProductNotFound"，data=None。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT `商品名称`, `单价`, `库存` FROM products WHERE id = %s",
                (product_id,)
            )
            row = cursor.fetchone()

        if row is None:
            return {
                "status": "error",
                "error_type": "ProductNotFound",
                "data": None,
                "message": f"商品总表中找不到商品编号：{product_id}"
            }

        product_info = {
            "商品名称": row[0],
            "单价": one_convert_mysql_value(row[1]),
            "库存": one_convert_mysql_value(row[2])
        }

        return {
            "status": "success",
            "error_type": None,
            "data": product_info,
            "message": f"商品 {product_id} 读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取单个商品")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_ongoing_by_table_from_mysql(table_id) -> dict:
    # 作用：读取指定桌子的待结算消费，三张表用 LEFT JOIN 拼接还原成 JSON 时代结构。
    # 接收：桌号。
    # 返回：四键结果字典；data 为 {桌号: {"待结算消费": {消费记录编号: 消费记录}}}；
    #       台位费记录包含：消费记录编号、类型、开台时间、关台时间、每小时价格、金额；
    #       商品记录包含：消费记录编号、类型、商品编号、商品名称、单价、数量、金额；
    #       该桌没有任何消费时"待结算消费"为 {}，status 仍为 "success"。
    #       说明：外层再套一层 桌号 -> 待结算消费，与 business 层 two_get_table_consumption_box 的入参结构保持一致。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT oc.id, oc.type_code, "
                "otf.`开台时间`, otf.`关台时间`, otf.`每小时价格`, otf.`金额`, "
                "opc.`商品编号`, opc.`商品名称`, opc.`单价`, opc.`数量`, opc.`金额` "
                "FROM ongoing_consumptions AS oc "
                "LEFT JOIN ongoing_table_fees AS otf "
                "ON otf.consumption_id = oc.id "
                "LEFT JOIN ongoing_product_consumptions AS opc "
                "ON opc.consumption_id = oc.id "
                "WHERE oc.`桌台编号` = %s "
                "ORDER BY oc.id",
                (table_id,)
            )
            rows = cursor.fetchall()

        pending_consumptions = {}
        for row in rows:
            consumption_id = row[0]
            type_code = row[1]

            if type_code == "T":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "台位费",
                    "开台时间": one_convert_mysql_value(row[2]),
                    "关台时间": one_convert_mysql_value(row[3]),
                    "每小时价格": one_convert_mysql_value(row[4]),
                    "金额": one_convert_mysql_value(row[5])
                }
            elif type_code == "S":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "商品",
                    "商品编号": row[6],
                    "商品名称": row[7],
                    "单价": one_convert_mysql_value(row[8]),
                    "数量": one_convert_mysql_value(row[9]),
                    "金额": one_convert_mysql_value(row[10])
                }
            else:
                return {
                    "status": "error",
                    "error_type": "UnknownConsumptionType",
                    "data": None,
                    "message": f"无法识别的消费类型代码：{type_code}"
                }

            pending_consumptions[consumption_id] = record

        return {
            "status": "success",
            "error_type": None,
            "data": {
                str(table_id): {
                    "待结算消费": pending_consumptions
                }
            },
            "message": f"{table_id} 号桌待结算消费读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取单桌待结算消费")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_all_ongoing_from_mysql() -> dict:
    # 作用：读取所有桌子的待结算消费，还原成 JSON 时代 Ongoing_order.json 的整体结构。
    # 接收：无。
    # 返回：四键结果字典；data 为 {桌号: {"待结算消费": {消费记录编号: 消费记录}}}；
    #       没有消费的桌子也要出现，其"待结算消费"为空字典；不再输出 "_说明"。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT bt.id, oc.id, oc.type_code, "
                "otf.`开台时间`, otf.`关台时间`, otf.`每小时价格`, otf.`金额`, "
                "opc.`商品编号`, opc.`商品名称`, opc.`单价`, opc.`数量`, opc.`金额` "
                "FROM billiards_tables AS bt "
                "LEFT JOIN ongoing_consumptions AS oc "
                "ON oc.`桌台编号` = bt.id "
                "LEFT JOIN ongoing_table_fees AS otf "
                "ON otf.consumption_id = oc.id "
                "LEFT JOIN ongoing_product_consumptions AS opc "
                "ON opc.consumption_id = oc.id "
                "ORDER BY bt.id, oc.id"
            )
            rows = cursor.fetchall()

        table_consumption_boxes = {}
        for row in rows:
            table_id = str(row[0])
            consumption_id = row[1]

            if table_id not in table_consumption_boxes:
                table_consumption_boxes[table_id] = {"待结算消费": {}}

            if consumption_id is None:
                continue

            type_code = row[2]

            if type_code == "T":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "台位费",
                    "开台时间": one_convert_mysql_value(row[3]),
                    "关台时间": one_convert_mysql_value(row[4]),
                    "每小时价格": one_convert_mysql_value(row[5]),
                    "金额": one_convert_mysql_value(row[6])
                }
            elif type_code == "S":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "商品",
                    "商品编号": row[7],
                    "商品名称": row[8],
                    "单价": one_convert_mysql_value(row[9]),
                    "数量": one_convert_mysql_value(row[10]),
                    "金额": one_convert_mysql_value(row[11])
                }
            else:
                return {
                    "status": "error",
                    "error_type": "UnknownConsumptionType",
                    "data": None,
                    "message": f"无法识别的消费类型代码：{type_code}"
                }

            table_consumption_boxes[table_id]["待结算消费"][consumption_id] = record

        return {
            "status": "success",
            "error_type": None,
            "data": table_consumption_boxes,
            "message": "全部待结算消费读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取全部待结算消费")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_unpaid_orders_from_mysql() -> dict:
    # 作用：读取全部"未支付"订单（已支付的不查），还原成 JSON 时代 unpaid order.json 的整体结构。
    # 接收：无。
    # 返回：四键结果字典；data 为 {订单编号: {"支付状态": "未支付", "消费明细": [...], "总金额": 数字}}；
    #       消费明细是列表，不是字典；空表时 data 为 {}，status 仍为 "success"。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT o.order_id, o.`支付状态`, o.`总金额`, "
                "oo.consumption_id, oo.type_code, "
                "otf.`开台时间`, otf.`关台时间`, otf.`每小时价格`, otf.`金额`, "
                "opc.`商品编号`, opc.`商品名称`, opc.`单价`, opc.`数量`, opc.`金额` "
                "FROM orders AS o "
                "LEFT JOIN order_consumptions AS oo "
                "ON oo.order_id = o.order_id "
                "LEFT JOIN order_table_fees AS otf "
                "ON otf.consumption_id = oo.consumption_id AND otf.order_id = oo.order_id "
                "LEFT JOIN order_product_consumptions AS opc "
                "ON opc.consumption_id = oo.consumption_id AND opc.order_id = oo.order_id "
                "WHERE o.`支付状态` = %s "
                "ORDER BY o.order_id, oo.consumption_id",
                ("未支付",)
            )
            rows = cursor.fetchall()

        unpaid_orders = {}
        for row in rows:
            order_id = row[0]

            if order_id not in unpaid_orders:
                unpaid_orders[order_id] = {
                    "支付状态": row[1],
                    "消费明细": [],
                    "总金额": one_convert_mysql_value(row[2])
                }

            consumption_id = row[3]
            if consumption_id is None:
                continue

            type_code = row[4]

            if type_code == "T":
                detail = {
                    "消费记录编号": consumption_id,
                    "类型": "台位费",
                    "开台时间": one_convert_mysql_value(row[5]),
                    "关台时间": one_convert_mysql_value(row[6]),
                    "每小时价格": one_convert_mysql_value(row[7]),
                    "金额": one_convert_mysql_value(row[8])
                }
            elif type_code == "S":
                detail = {
                    "消费记录编号": consumption_id,
                    "类型": "商品",
                    "商品编号": row[9],
                    "商品名称": row[10],
                    "单价": one_convert_mysql_value(row[11]),
                    "数量": one_convert_mysql_value(row[12]),
                    "金额": one_convert_mysql_value(row[13])
                }
            else:
                return {
                    "status": "error",
                    "error_type": "UnknownConsumptionType",
                    "data": None,
                    "message": f"无法识别的消费类型代码：{type_code}"
                }

            unpaid_orders[order_id]["消费明细"].append(detail)

        return {
            "status": "success",
            "error_type": None,
            "data": unpaid_orders,
            "message": "未支付订单读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取未支付订单")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_load_order_by_id_from_mysql(order_id) -> dict:
    # 作用：按订单编号读取单张订单（不区分支付状态）。
    # 接收：订单编号。
    # 返回：四键结果字典；data 为 {"支付状态": ..., "消费明细": [...], "总金额": 数字, "支付时间": 数字或 None}；
    #       找不到时 status="error"，error_type="OrderNotFound"，data=None。
    connection = None
    try:
        connection = one_get_mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT `支付状态`, `总金额`, `支付时间` FROM orders WHERE order_id = %s",
                (order_id,)
            )
            order_row = cursor.fetchone()

            if order_row is None:
                return {
                    "status": "error",
                    "error_type": "OrderNotFound",
                    "data": None,
                    "message": f"订单表中找不到订单编号：{order_id}"
                }

            cursor.execute(
                "SELECT oo.consumption_id, oo.type_code, "
                "otf.`开台时间`, otf.`关台时间`, otf.`每小时价格`, otf.`金额`, "
                "opc.`商品编号`, opc.`商品名称`, opc.`单价`, opc.`数量`, opc.`金额` "
                "FROM order_consumptions AS oo "
                "LEFT JOIN order_table_fees AS otf "
                "ON otf.consumption_id = oo.consumption_id AND otf.order_id = oo.order_id "
                "LEFT JOIN order_product_consumptions AS opc "
                "ON opc.consumption_id = oo.consumption_id AND opc.order_id = oo.order_id "
                "WHERE oo.order_id = %s "
                "ORDER BY oo.consumption_id",
                (order_id,)
            )
            detail_rows = cursor.fetchall()

        consumption_details = []
        for row in detail_rows:
            consumption_id = row[0]
            type_code = row[1]

            if type_code == "T":
                detail = {
                    "消费记录编号": consumption_id,
                    "类型": "台位费",
                    "开台时间": one_convert_mysql_value(row[2]),
                    "关台时间": one_convert_mysql_value(row[3]),
                    "每小时价格": one_convert_mysql_value(row[4]),
                    "金额": one_convert_mysql_value(row[5])
                }
            elif type_code == "S":
                detail = {
                    "消费记录编号": consumption_id,
                    "类型": "商品",
                    "商品编号": row[6],
                    "商品名称": row[7],
                    "单价": one_convert_mysql_value(row[8]),
                    "数量": one_convert_mysql_value(row[9]),
                    "金额": one_convert_mysql_value(row[10])
                }
            else:
                return {
                    "status": "error",
                    "error_type": "UnknownConsumptionType",
                    "data": None,
                    "message": f"无法识别的消费类型代码：{type_code}"
                }

            consumption_details.append(detail)

        order = {
            "支付状态": order_row[0],
            "消费明细": consumption_details,
            "总金额": one_convert_mysql_value(order_row[1]),
            "支付时间": one_convert_mysql_value(order_row[2])
        }

        return {
            "status": "success",
            "error_type": None,
            "data": order,
            "message": f"订单 {order_id} 读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取单张订单")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


# ==================== 新增：MySQL 写函数（共 10 个） ====================
# 说明：写函数的第一个参数统一是外部传入的 cursor；事务由上层控制，
#       本层只执行单次操作，不 commit、不回滚。

def one_insert_ongoing_table_fee_to_mysql(cursor, table_id, consumption_id, start_time, hour_price) -> dict:
    # 作用：新增一条待结算台位费（开台），主表 1 行 + 明细表 1 行，上层只调一次。
    # 接收：cursor、桌号、消费记录编号、开台时间、每小时价格。
    # 返回：四键结果字典；data 为写入成功的台位费消费记录。
    try:
        cursor.execute(
            "INSERT INTO ongoing_consumptions (id, `桌台编号`, type_code, `创建时间`) "
            "VALUES (%s, %s, %s, %s)",
            (consumption_id, table_id, "T", start_time)
        )
        cursor.execute(
            "INSERT INTO ongoing_table_fees "
            "(consumption_id, `桌台编号`, `开台时间`, `关台时间`, `每小时价格`, `金额`) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (consumption_id, table_id, start_time, None, hour_price, None)
        )

        table_consumption = {
            "消费记录编号": consumption_id,
            "类型": "台位费",
            "开台时间": start_time,
            "关台时间": None,
            "每小时价格": hour_price,
            "金额": None
        }

        return {
            "status": "success",
            "error_type": None,
            "data": table_consumption,
            "message": f"{table_id} 号桌台位费写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增待结算台位费")


def one_update_ongoing_table_fee_in_mysql(cursor, consumption_id, close_time, amount) -> dict:
    # 作用：更新一条待结算台位费的关台时间和金额（关台），只改这两列。
    # 接收：cursor、消费记录编号、关台时间、金额。
    # 返回：四键结果字典；data 为关键编号（消费记录编号）；
    #       不存在时返回 error，error_type="TableFeeNotFound"；
    #       存在但已经被关过时返回 error，error_type="TableFeeAlreadyClosed"。
    try:
        # WHERE 里的 `关台时间 IS NULL`：让数据库自己判断"这条还没关过才允许关"。
        # 并发下两个请求同时关台时，只有一个能命中这一行，另一个 rowcount 会是 0，
        # 从而防止后写的把先写的关台结果覆盖掉（防止并发覆盖 / 防重复关台）。
        cursor.execute(
            "UPDATE ongoing_table_fees SET `关台时间` = %s, `金额` = %s "
            "WHERE consumption_id = %s AND `关台时间` IS NULL",
            (close_time, amount, consumption_id)
        )

        if cursor.rowcount == 0:
            # 更新没命中任何行：要么记录不存在，要么已经被关过。
            # 用同一个 cursor 先查一下这条记录是否存在，区分两种情况，返回不同错误。
            cursor.execute(
                "SELECT consumption_id FROM ongoing_table_fees WHERE consumption_id = %s",
                (consumption_id,)
            )
            row = cursor.fetchone()

            if row is None:
                return {
                    "status": "error",
                    "error_type": "TableFeeNotFound",
                    "data": None,
                    "message": f"待结算台位费中找不到消费记录：{consumption_id}"
                }

            return {
                "status": "error",
                "error_type": "TableFeeAlreadyClosed",
                "data": None,
                "message": f"该台位费已经被关过，不能重复关台：{consumption_id}"
            }

        return {
            "status": "success",
            "error_type": None,
            "data": consumption_id,
            "message": f"{consumption_id} 关台时间与金额更新成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "更新待结算台位费")


def one_insert_ongoing_product_consumption_to_mysql(cursor, table_id, consumption_id, product_id, product_name, unit_price, quantity, amount, created_time) -> dict:
    # 作用：新增一条待结算商品消费（挂账），主表 1 行 + 明细表 1 行。
    # 接收：cursor、桌号、消费记录编号、商品编号、商品名称、单价、数量、金额、创建时间。
    # 返回：四键结果字典；data 为写入成功的商品消费记录。
    # 注意：created_time 由上层传入，和生成消费记录编号的时间戳一致，本函数不自己取时间。
    try:
        cursor.execute(
            "INSERT INTO ongoing_consumptions (id, `桌台编号`, type_code, `创建时间`) "
            "VALUES (%s, %s, %s, %s)",
            (consumption_id, table_id, "S", created_time)
        )
        cursor.execute(
            "INSERT INTO ongoing_product_consumptions "
            "(consumption_id, `桌台编号`, `商品编号`, `商品名称`, `单价`, `数量`, `金额`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (consumption_id, table_id, product_id, product_name, unit_price, quantity, amount)
        )

        product_consumption = {
            "消费记录编号": consumption_id,
            "类型": "商品",
            "商品编号": product_id,
            "商品名称": product_name,
            "单价": unit_price,
            "数量": quantity,
            "金额": amount
        }

        return {
            "status": "success",
            "error_type": None,
            "data": product_consumption,
            "message": f"{table_id} 号桌商品消费写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增待结算商品消费")


def one_delete_ongoing_consumption_from_mysql(cursor, consumption_id) -> dict:
    # 作用：删除一条待结算消费（结账后清扫）。
    # 接收：cursor、消费记录编号。
    # 删除顺序：T_ 开头先删 ongoing_table_fees，再删 ongoing_consumptions；
    #           S_ 开头先删 ongoing_product_consumptions，再删 ongoing_consumptions。
    # 返回：四键结果字典；data 为关键编号（消费记录编号）；
    #       找不到主表记录时返回 error，error_type="ConsumptionNotFound"。
    try:
        if consumption_id.startswith("T_"):
            cursor.execute(
                "DELETE FROM ongoing_table_fees WHERE consumption_id = %s",
                (consumption_id,)
            )
            cursor.execute(
                "DELETE FROM ongoing_consumptions WHERE id = %s",
                (consumption_id,)
            )
        elif consumption_id.startswith("S_"):
            cursor.execute(
                "DELETE FROM ongoing_product_consumptions WHERE consumption_id = %s",
                (consumption_id,)
            )
            cursor.execute(
                "DELETE FROM ongoing_consumptions WHERE id = %s",
                (consumption_id,)
            )
        else:
            return {
                "status": "error",
                "error_type": "UnknownConsumptionType",
                "data": None,
                "message": f"无法识别的消费记录编号：{consumption_id}"
            }

        if cursor.rowcount == 0:
            return {
                "status": "error",
                "error_type": "ConsumptionNotFound",
                "data": None,
                "message": f"待结算消费中找不到消费记录：{consumption_id}"
            }

        return {
            "status": "success",
            "error_type": None,
            "data": consumption_id,
            "message": f"消费记录 {consumption_id} 删除成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "删除待结算消费")


def one_insert_order_to_mysql(cursor, order_id, table_id, total_amount, created_time) -> dict:
    # 作用：新增一张订单主记录（创建未支付订单）。
    # 接收：cursor、订单编号、桌号、总金额、创建时间。
    # 返回：四键结果字典；data 为关键编号（订单编号）。
    # 写入：orders 表插 1 行，支付状态固定为"未支付"，支付时间为 NULL。
    try:
        cursor.execute(
            "INSERT INTO orders "
            "(order_id, `桌台编号`, `支付状态`, `总金额`, `创建时间`, `支付时间`) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (order_id, table_id, "未支付", total_amount, created_time, None)
        )

        return {
            "status": "success",
            "error_type": None,
            "data": order_id,
            "message": f"订单 {order_id} 主记录写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增订单主记录")


def one_insert_order_table_fee_to_mysql(cursor, order_id, consumption_id, start_time, close_time, hour_price, amount) -> dict:
    # 作用：新增一条订单台费明细（创建订单时逐条写入），关联表 1 行 + 明细表 1 行。
    # 接收：cursor、订单编号、消费记录编号、开台时间、关台时间、每小时价格、金额。
    # 返回：四键结果字典；data 为写入成功的台费明细记录。
    try:
        cursor.execute(
            "INSERT INTO order_consumptions (consumption_id, order_id, type_code) "
            "VALUES (%s, %s, %s)",
            (consumption_id, order_id, "T")
        )
        cursor.execute(
            "INSERT INTO order_table_fees "
            "(consumption_id, order_id, `开台时间`, `关台时间`, `每小时价格`, `金额`) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (consumption_id, order_id, start_time, close_time, hour_price, amount)
        )

        table_fee = {
            "消费记录编号": consumption_id,
            "类型": "台位费",
            "开台时间": start_time,
            "关台时间": close_time,
            "每小时价格": hour_price,
            "金额": amount
        }

        return {
            "status": "success",
            "error_type": None,
            "data": table_fee,
            "message": f"订单 {order_id} 台费明细写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增订单台费明细")


def one_insert_order_product_consumption_to_mysql(cursor, order_id, consumption_id, product_id, product_name, unit_price, quantity, amount) -> dict:
    # 作用：新增一条订单商品明细（创建订单时逐条写入），关联表 1 行 + 明细表 1 行。
    # 接收：cursor、订单编号、消费记录编号、商品编号、商品名称、单价、数量、金额。
    # 返回：四键结果字典；data 为写入成功的商品明细记录。
    try:
        cursor.execute(
            "INSERT INTO order_consumptions (consumption_id, order_id, type_code) "
            "VALUES (%s, %s, %s)",
            (consumption_id, order_id, "S")
        )
        cursor.execute(
            "INSERT INTO order_product_consumptions "
            "(consumption_id, order_id, `商品编号`, `商品名称`, `单价`, `数量`, `金额`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (consumption_id, order_id, product_id, product_name, unit_price, quantity, amount)
        )

        product_consumption = {
            "消费记录编号": consumption_id,
            "类型": "商品",
            "商品编号": product_id,
            "商品名称": product_name,
            "单价": unit_price,
            "数量": quantity,
            "金额": amount
        }

        return {
            "status": "success",
            "error_type": None,
            "data": product_consumption,
            "message": f"订单 {order_id} 商品明细写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增订单商品明细")


def one_update_order_payment_status_in_mysql(cursor, order_id, payment_time) -> dict:
    # 作用：把订单改为已支付（确认收款）。
    # 接收：cursor、订单编号、支付时间。
    # 返回：四键结果字典；data 为关键编号（订单编号）；
    #       找不到订单时返回 error，error_type="OrderNotFound"；
    #       订单当前不是"未支付"时返回 error，error_type="OrderNotUnpaid"。
    # 说明：这就是原来"改状态 + 转历史 + 删原单"三步合并成的一次更新。
    try:
        # WHERE 里的 `支付状态 = %s`（值为"未支付"）：让数据库自己判断"还是未支付才允许收款"。
        # 并发下同一张订单被重复确认收款时，只有第一次能命中这一行，后到的 rowcount 会是 0，
        # 从而防止同一张订单被重复收款（防止重复收款 / 防并发覆盖）。
        cursor.execute(
            "UPDATE orders SET `支付状态` = %s, `支付时间` = %s "
            "WHERE order_id = %s AND `支付状态` = %s",
            ("已支付", payment_time, order_id, "未支付")
        )

        if cursor.rowcount == 0:
            # 更新没命中任何行：要么订单不存在，要么已经不是"未支付"了。
            # 用同一个 cursor 先查一下这张订单是否存在，区分两种情况，返回不同错误。
            cursor.execute(
                "SELECT order_id FROM orders WHERE order_id = %s",
                (order_id,)
            )
            row = cursor.fetchone()

            if row is None:
                return {
                    "status": "error",
                    "error_type": "OrderNotFound",
                    "data": None,
                    "message": f"订单表中找不到订单编号：{order_id}"
                }

            return {
                "status": "error",
                "error_type": "OrderNotUnpaid",
                "data": None,
                "message": f"订单当前不是未支付状态：{order_id}"
            }

        return {
            "status": "success",
            "error_type": None,
            "data": order_id,
            "message": f"订单 {order_id} 已标记为已支付"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "更新订单支付状态")


def one_deduct_product_stock_in_mysql(cursor, product_id, quantity) -> dict:
    # 作用：扣减商品库存（在数据库里直接做减法，不是先读出来再算）。
    # 接收：cursor、商品编号、扣减数量。
    # 返回：四键结果字典；data 为关键编号（商品编号）；
    #       找不到商品时返回 error，error_type="ProductNotFound"；
    #       商品存在但库存不够时返回 error，error_type="ProductStockNotEnough"。
    # 更新：products 表该商品的一行：库存 = 库存 - quantity。
    try:
        # WHERE 里的 `库存 >= %s`：让数据库自己判断"库存够才允许扣"。
        # 并发下两个请求同时扣库存时，库存不够的那次 rowcount 会是 0，
        # 从而防止把库存扣成负数（防止超卖 / 防并发覆盖）。
        cursor.execute(
            "UPDATE products SET `库存` = `库存` - %s WHERE id = %s AND `库存` >= %s",
            (quantity, product_id, quantity)
        )

        if cursor.rowcount == 0:
            # 更新没命中任何行：要么商品不存在，要么库存不够。
            # 用同一个 cursor 先查一下这个商品是否存在，区分两种情况，返回不同错误。
            cursor.execute(
                "SELECT `库存` FROM products WHERE id = %s",
                (product_id,)
            )
            row = cursor.fetchone()

            if row is None:
                return {
                    "status": "error",
                    "error_type": "ProductNotFound",
                    "data": None,
                    "message": f"商品总表中找不到商品编号：{product_id}"
                }

            return {
                "status": "error",
                "error_type": "ProductStockNotEnough",
                "data": None,
                "message": f"商品 {product_id} 库存不足，当前库存 {row[0]}，需要 {quantity}"
            }

        return {
            "status": "success",
            "error_type": None,
            "data": product_id,
            "message": f"商品 {product_id} 库存扣减成功，扣减数量 {quantity}"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "扣减商品库存")


def one_insert_core_backup_to_mysql(cursor, consumption_id, table_id, start_time, close_time) -> dict:
    # 作用：新增一条核心备份（关台后备份台位费核心数据）。
    # 接收：cursor、消费记录编号、桌号、开台时间、关台时间。
    # 返回：四键结果字典；data 为关键编号（消费记录编号）。
    # 写入：core_backup 表插 1 行；没有商品分支，旧函数里的商品备份分支是遗留代码，本次不实现。
    try:
        cursor.execute(
            "INSERT INTO core_backup (consumption_id, `桌台编号`, `开台时间`, `关台时间`) "
            "VALUES (%s, %s, %s, %s)",
            (consumption_id, table_id, start_time, close_time)
        )

        return {
            "status": "success",
            "error_type": None,
            "data": consumption_id,
            "message": f"核心备份 {consumption_id} 写入成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "新增核心备份")


def one_execute_in_transaction(operation_function) -> dict:
    # 作用：把一组写操作放进一个数据库事务里，要么全成功，要么全回滚。
    # 接收：operation_function，是一个函数对象。
    #       它会接收一个 cursor 参数，内部可以调多次写函数。
    #       它必须返回四键字典。
    # 返回：四键字典。
    #       - operation_function 返回 success → 提交事务，返回该结果
    #       - operation_function 返回 error → 回滚事务，返回该结果
    #       - operation_function 内部抛异常 → 回滚事务，返回错误字典
    # 说明：
    # - 内部用 one_get_mysql_connection() 开连接。
    # - 内部用 connection.begin() 开事务。
    # - 执行完必须 close 连接（无论成功失败）。
    # - 不要在函数里 print。
    # - 不要用 with 语法写 cursor，直接用 connection.cursor() 手动管理，
    #   因为异常处理要控制回滚时机。

    connection = None

    try:
        connection = one_get_mysql_connection()
        connection.begin()
        cursor = connection.cursor()

        result = operation_function(cursor)

        if result["status"] == "success":
            connection.commit()
            return result
        else:
            connection.rollback()
            return result

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return one_convert_mysql_error(error, "事务执行")

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def one_write_business_log(source, flow_name, params, result) -> None:
    # 作用：写一条业务日志到 business.log。
    #
    # 接收：
    # - source: 来源，"人" 或 "AI"。
    #           人从网页/终端调用时传 "人"（默认）；
    #           AI 调用时由 AI 入口显式传 "AI"。
    # - flow_name: 流程名，比如 "开台"、"关台"、"加商品"、"创建订单"、"确认收款"。
    # - params: 参数说明字符串。
    # - result: 流程返回的四键字典（含 status、message）。
    #
    # 动作：
    # - 用 one_get_current_time() 和 one_change_time_format() 生成时间字符串。
    # - 按 result["status"] 判断成功还是失败。
    # - 成功时格式：[时间] [来源] [流程名] [参数] [成功]
    # - 失败时格式：[时间] [来源] [流程名] [参数] [失败：{result["message"]}]
    # - 追加写入 business.log（和 io_layer.py 同目录，编码 utf-8）。
    # - 每次一行，行尾换行。
    #
    # 三层保险（重要）：
    # 1. 检查 result 是不是字典、有没有 status 字段。不对就写一条"日志参数异常"，不抛异常。
    # 2. 写文件用 try/except 包住。失败就 print 一行提示，不抛异常。
    # 3. 整个函数不返回任何东西，绝不阻断主流程。

    # ==================== 生成时间字符串 ====================

    time_text = one_change_time_format(one_get_current_time())

    # ==================== 三层保险第 1 层：检查 result ====================

    # result 必须是字典，并且带 status 字段；不对就记"日志参数异常"。
    if not isinstance(result, dict) or "status" not in result:
        status_text = "失败：日志参数异常"
    elif result["status"] == "success":
        status_text = "成功"
    else:
        status_text = f"失败：{result.get('message', '未知错误')}"

    log_line = f"[{time_text}] [{source}] [{flow_name}] [{params}] [{status_text}]"

    # ==================== 三层保险第 2 层：追加写文件 ====================

    # 写文件失败只 print 一行提示，不抛异常，绝不影响主流程。
    try:
        log_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(log_dir, "business.log")
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(log_line + "\n")
    except Exception as error:
        print(f"【业务日志】写入 business.log 失败：{error}")

    # 三层保险第 3 层：本函数不返回任何东西，到这里自然结束。


def one_lock_billiards_table(cursor, table_id) -> dict:
    # 作用：在事务里锁住 billiards_tables 表中指定桌子的那一行。
    # 用于开台流程：防止两个人同时开同一桌。
    #
    # 接收：cursor（外部事务传入）、桌号。
    # 返回：四键结构。
    #   - 成功：data 为桌子信息（每小时价格）
    #   - 桌子不存在：error_type = "TableNotFound"
    #
    # 动作：
    #   执行 SELECT id, `每小时价格` FROM billiards_tables WHERE id = %s FOR UPDATE
    #   - 查到了：返回 success + 价格
    #   - 查不到：返回 error，error_type = "TableNotFound"
    #
    # 说明：
    #   - 这个 SELECT 加了 FOR UPDATE，会锁住这一行，直到事务提交或回滚。
    #   - 别人如果也要锁这行，会卡住，必须等。
    #   - cursor 是外部传入的，事务由上层控制。
    try:
        cursor.execute(
            "SELECT id, `每小时价格` FROM billiards_tables WHERE id = %s FOR UPDATE",
            (table_id,)
        )
        row = cursor.fetchone()

        if row is None:
            return {
                "status": "error",
                "error_type": "TableNotFound",
                "data": None,
                "message": f"桌台配置表中找不到 {table_id} 号桌"
            }

        table_info = {
            "每小时价格": one_convert_mysql_value(row[1])
        }

        return {
            "status": "success",
            "error_type": None,
            "data": table_info,
            "message": f"{table_id} 号桌锁定成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "锁定桌台")


def one_load_ongoing_by_table_with_cursor(cursor, table_id) -> dict:
    # 作用：用外部传入的 cursor 读取指定桌子的待结算消费。
    #       专供事务内流程使用，返回结构和 one_load_ongoing_by_table_from_mysql 完全一致。
    # 接收：cursor（外部事务传入）、桌号。
    # 返回：四键结果字典；data 为 {桌号: {"待结算消费": {消费记录编号: 消费记录}}}；
    #       台位费记录包含：消费记录编号、类型、开台时间、关台时间、每小时价格、金额；
    #       商品记录包含：消费记录编号、类型、商品编号、商品名称、单价、数量、金额；
    #       该桌没有任何消费时"待结算消费"为 {}，status 仍为 "success"。
    #
    # 说明：
    #   - 不开新连接，直接用外部传入的 cursor 读，事务由上层控制。
    #   - 和 one_load_ongoing_by_table_from_mysql 的区别只有一个：
    #     那个函数自己开新连接，这个函数用事务里的 cursor。
    try:
        cursor.execute(
            "SELECT oc.id, oc.type_code, "
            "otf.`开台时间`, otf.`关台时间`, otf.`每小时价格`, otf.`金额`, "
            "opc.`商品编号`, opc.`商品名称`, opc.`单价`, opc.`数量`, opc.`金额` "
            "FROM ongoing_consumptions AS oc "
            "LEFT JOIN ongoing_table_fees AS otf "
            "ON otf.consumption_id = oc.id "
            "LEFT JOIN ongoing_product_consumptions AS opc "
            "ON opc.consumption_id = oc.id "
            "WHERE oc.`桌台编号` = %s "
            "ORDER BY oc.id",
            (table_id,)
        )
        rows = cursor.fetchall()

        pending_consumptions = {}
        for row in rows:
            consumption_id = row[0]
            type_code = row[1]

            if type_code == "T":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "台位费",
                    "开台时间": one_convert_mysql_value(row[2]),
                    "关台时间": one_convert_mysql_value(row[3]),
                    "每小时价格": one_convert_mysql_value(row[4]),
                    "金额": one_convert_mysql_value(row[5])
                }
            elif type_code == "S":
                record = {
                    "消费记录编号": consumption_id,
                    "类型": "商品",
                    "商品编号": row[6],
                    "商品名称": row[7],
                    "单价": one_convert_mysql_value(row[8]),
                    "数量": one_convert_mysql_value(row[9]),
                    "金额": one_convert_mysql_value(row[10])
                }
            else:
                return {
                    "status": "error",
                    "error_type": "UnknownConsumptionType",
                    "data": None,
                    "message": f"无法识别的消费类型代码：{type_code}"
                }

            pending_consumptions[consumption_id] = record

        return {
            "status": "success",
            "error_type": None,
            "data": {
                str(table_id): {
                    "待结算消费": pending_consumptions
                }
            },
            "message": f"{table_id} 号桌待结算消费读取成功"
        }
    except Exception as error:
        return one_convert_mysql_error(error, "读取单桌待结算消费")
