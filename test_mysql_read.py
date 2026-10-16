# -*- coding: utf-8 -*-
"""
test_mysql_read.py

作用：把 7 个 MySQL 读函数各调用一次，并把返回的 data 与对应 JSON 旧数据用 ==
      自动比对，直接给出“一致 / 不一致”。本脚本只读，不写任何文件。

对应关系：
  one_load_billiards_tables_from_mysql        → database.json 的 billiards_tables
  one_load_products_from_mysql                → database.json 的 products
  one_load_ongoing_by_table_from_mysql("1")   → Ongoing_order.json 的 "1"
  one_load_all_ongoing_from_mysql             → Ongoing_order.json 整体（去掉 _说明）
  one_load_unpaid_orders_from_mysql           → unpaid order.json
  one_load_order_by_id_from_mysql(订单编号)    → 该订单在 JSON 里的那一条
  one_load_product_by_id_from_mysql(商品编号)  → 该商品在 JSON 里的那一条
"""

import os
import sys
import json

# 让中文输出在 Windows 控制台里正常显示
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import io_layer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_json(file_name):
    path = os.path.join(BASE_DIR, file_name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def trunc(text, limit=160):
    text = repr(text)
    if len(text) <= limit:
        return text
    return text[:limit] + " ...(已截断)"


def describe(data, limit=160):
    """打印 data 的结构：dict 打印 key 和一层内容，list 打印长度和前两条。"""
    if isinstance(data, dict):
        keys = list(data.keys())
        lines = ["    data 结构: dict，共 %d 个键：%s" % (len(keys), keys)]
        for key in keys:
            lines.append("        %s -> %s" % (repr(key), trunc(data[key], limit)))
        return "\n".join(lines)
    if isinstance(data, list):
        lines = ["    data 结构: list，长度 %d" % len(data)]
        for item in data[:2]:
            lines.append("        - %s" % trunc(item, limit))
        return "\n".join(lines)
    return "    data 结构: %s = %s" % (type(data).__name__, trunc(data, limit))


def collect_diff(left, right, path="", found=None):
    """递归找出两边的差异，只返回差异描述（不返回全量数据）。"""
    if found is None:
        found = []
    if len(found) >= 40:
        return found

    if type(left) != type(right):
        found.append("%s: 类型不同（左=%s，右=%s）"
                     % (path or "<根>", type(left).__name__, type(right).__name__))
        return found

    if isinstance(left, dict):
        for key in left:
            if key not in right:
                found.append("%s.%s: 仅 MySQL 有 -> %s" % (path, key, trunc(left[key], 120)))
        for key in right:
            if key not in left:
                found.append("%s.%s: 仅 JSON 有 -> %s" % (path, key, trunc(right[key], 120)))
        for key in left:
            if key in right:
                collect_diff(left[key], right[key], "%s.%s" % (path, key), found)
    elif isinstance(left, list):
        if len(left) != len(right):
            found.append("%s: 列表长度不同（MySQL=%d，JSON=%d）"
                         % (path or "<根>", len(left), len(right)))
        for i in range(min(len(left), len(right))):
            collect_diff(left[i], right[i], "%s[%d]" % (path, i), found)
    else:
        if left != right:
            found.append("%s: 值不同（MySQL=%r，JSON=%r）" % (path or "<根>", left, right))

    return found


RESULT_LIST = []


def run_check(title, status, print_data, left, right, right_desc):
    """打印一个读函数的结果，并把 left 与 right 用 == 自动比对。"""
    print("=" * 68)
    print(title)
    print("    status: %s" % status)
    print(describe(print_data))
    print("    比对对象: %s" % right_desc)

    is_same = (status == "success" and left == right)
    print("    结果: %s" % ("一致" if is_same else "不一致"))

    if not is_same:
        if status != "success":
            print("    说明: status 不是 success，无法与 JSON 比对。")
        else:
            differences = collect_diff(left, right)
            if differences:
                print("    差异部分:")
                for line in differences:
                    print("      " + line)
            else:
                print("    差异部分: 未定位到结构差异（可能仅由类型或浮点误差导致）。")

    RESULT_LIST.append((title, is_same))


def main():
    database_json = load_json("database.json")
    ongoing_json = load_json("Ongoing_order.json")
    unpaid_json = load_json("unpaid order.json")
    history_json = load_json("history_orders.json")

    print("开始执行：7 个 MySQL 读函数与 JSON 旧数据自动比对")
    print("工作目录: %s" % BASE_DIR)

    # 1. 桌台配置
    r = io_layer.one_load_billiards_tables_from_mysql()
    run_check("[1] one_load_billiards_tables_from_mysql", r["status"], r["data"],
              r["data"], database_json["billiards_tables"],
              'database.json 的 "billiards_tables"')

    # 2. 商品总表
    r = io_layer.one_load_products_from_mysql()
    run_check("[2] one_load_products_from_mysql", r["status"], r["data"],
              r["data"], database_json["products"],
              'database.json 的 "products"')

    # 3. 单桌待结算消费
    table_id = "1"
    r = io_layer.one_load_ongoing_by_table_from_mysql(table_id)
    data = r["data"]
    left_box = data.get(table_id) if isinstance(data, dict) else None
    run_check('[3] one_load_ongoing_by_table_from_mysql("%s")' % table_id, r["status"], data,
              left_box, ongoing_json[table_id],
              'Ongoing_order.json 的 "%s"（该桌消费框；函数 data 外层已再套一层桌号）' % table_id)

    # 4. 全部待结算消费
    r = io_layer.one_load_all_ongoing_from_mysql()
    expected_ongoing = {k: v for k, v in ongoing_json.items() if k != "_说明"}
    run_check("[4] one_load_all_ongoing_from_mysql", r["status"], r["data"],
              r["data"], expected_ongoing,
              'Ongoing_order.json 整体（去掉 "_说明"）')

    # 5. 未支付订单
    r = io_layer.one_load_unpaid_orders_from_mysql()
    run_check("[5] one_load_unpaid_orders_from_mysql", r["status"], r["data"],
              r["data"], unpaid_json,
              'unpaid order.json 整体')

    # 6. 单张订单（取 history_orders.json 里的第一条存在订单）
    order_id = next(iter(history_json))
    r = io_layer.one_load_order_by_id_from_mysql(order_id)
    run_check('[6] one_load_order_by_id_from_mysql("%s")' % order_id, r["status"], r["data"],
              r["data"], history_json[order_id],
              'history_orders.json 的 "%s"' % order_id)

    # 7. 单个商品（取 database.json 里的第一条存在商品）
    product_id = next(iter(database_json["products"]))
    r = io_layer.one_load_product_by_id_from_mysql(product_id)
    run_check('[7] one_load_product_by_id_from_mysql("%s")' % product_id, r["status"], r["data"],
              r["data"], database_json["products"][product_id],
              'database.json 的 "products"."%s"' % product_id)

    # 总结论
    total = len(RESULT_LIST)
    mismatched = [title for title, ok in RESULT_LIST if not ok]
    print("=" * 68)
    if not mismatched:
        print("总结论：%d 个读函数全部一致。" % total)
    else:
        print("总结论：%d 个读函数中有 %d 个不一致。" % (total, len(mismatched)))
        for title in mismatched:
            print("    不一致：%s" % title)


if __name__ == "__main__":
    main()
