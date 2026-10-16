# -*- coding: utf-8 -*-
"""
test_mysql_write.py

作用：10 个 MySQL 写函数的冒烟测试。
      全程手动开事务，逐步写入并即时 SELECT 校验，跑完统一 ROLLBACK，
      最后再查一次数据库，确认事务回滚后数据没有任何残留。
      本脚本不会向数据库留下任何数据。

注意：写函数的第一个参数是外部 cursor，事务由本脚本控制，写函数自己不 commit。
"""

import sys
import pymysql

# 让中文输出在 Windows 控制台里正常显示
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import io_layer


# ---------------- 测试用假数据 ----------------
TABLE_ID = "99"

T_ID = "T_99_test"                     # 待结算台位费消费编号
S_ID = "S_99_test"                     # 待结算商品消费编号
START_TIME = 1700000000
CLOSE_TIME = 1700003600
HOUR_PRICE = 30
AMOUNT = 30

PRODUCT_ID = "P_001"                   # 用数据库里真实存在的商品编号
PRODUCT_NAME = "可乐"
UNIT_PRICE = 3
QUANTITY = 3
PRODUCT_AMOUNT = 9

ORDER_ID = "99_test_order"
ORDER_TOTAL = 39
ORDER_CREATED = 1700005000
ORDER_T_ID = "T_99_order"
ORDER_S_ID = "S_99_order"
PAY_TIME = 1700009999

DEDUCT_QTY = 3


COUNT_TABLES = [
    "ongoing_consumptions",
    "ongoing_table_fees",
    "ongoing_product_consumptions",
    "orders",
    "order_consumptions",
    "order_table_fees",
    "order_product_consumptions",
    "core_backup",
]

RESULTS = []


def open_connection():
    return pymysql.connect(**io_layer.DB_CONFIG)


def snapshot(conn):
    """抓一份关键表的快照：各表行数 + 测试商品的库存。"""
    cur = conn.cursor()
    data = {}
    for table in COUNT_TABLES:
        cur.execute("SELECT COUNT(*) FROM " + table)
        data[table] = cur.fetchone()[0]
    cur.execute("SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,))
    row = cur.fetchone()
    data["products_stock"] = row[0] if row else None
    cur.close()
    return data


def count(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()[0]


def report(name, status, checks):
    """打印一个写函数的结果与各项校验；返回该步是否通过。"""
    print("=" * 68)
    print(name)
    print("    status: %s" % status)

    passed_all = (status == "success")
    for desc, passed in checks:
        print("    - %s: %s" % (desc, "一致" if passed else "不一致"))
        if not passed:
            passed_all = False

    print("    验证结果: %s" % ("一致" if passed_all else "不一致"))
    RESULTS.append((name, passed_all))
    return passed_all


def main():
    base_conn = open_connection()
    baseline = snapshot(base_conn)
    base_conn.close()

    conn = open_connection()
    conn.begin()
    cur = conn.cursor()

    print("开始执行：10 个 MySQL 写函数冒烟测试（全程同一事务，最后回滚）")
    print("数据库: %s   测试桌号: %s   测试商品: %s"
          % (io_layer.DB_CONFIG["database"], TABLE_ID, PRODUCT_ID))

    try:
        # ---------- 1. 插入待结算台位费 ----------
        before_main = count(cur, "SELECT COUNT(*) FROM ongoing_consumptions")
        before_otf = count(cur, "SELECT COUNT(*) FROM ongoing_table_fees")
        r = io_layer.one_insert_ongoing_table_fee_to_mysql(
            cur, TABLE_ID, T_ID, START_TIME, HOUR_PRICE)
        after_main = count(cur, "SELECT COUNT(*) FROM ongoing_consumptions")
        after_otf = count(cur, "SELECT COUNT(*) FROM ongoing_table_fees")
        report("[1] one_insert_ongoing_table_fee_to_mysql", r["status"], [
            ("主表 ongoing_consumptions %d -> %d（期望 +1）" % (before_main, after_main),
             after_main == before_main + 1),
            ("明细表 ongoing_table_fees %d -> %d（期望 +1）" % (before_otf, after_otf),
             after_otf == before_otf + 1),
        ])

        # ---------- 2. 更新台位费（关台时间 + 金额） ----------
        r = io_layer.one_update_ongoing_table_fee_in_mysql(
            cur, T_ID, CLOSE_TIME, AMOUNT)
        cur.execute("SELECT `关台时间`, `金额` FROM ongoing_table_fees WHERE consumption_id = %s",
                    (T_ID,))
        row = cur.fetchone()
        got_close, got_amount = (row[0], row[1]) if row else (None, None)
        report("[2] one_update_ongoing_table_fee_in_mysql", r["status"], [
            ("关台时间 期望 %s 实际 %s" % (CLOSE_TIME, got_close), got_close == CLOSE_TIME),
            ("金额 期望 %s 实际 %s" % (AMOUNT, got_amount), got_amount == AMOUNT),
        ])

        # ---------- 3. 插入待结算商品消费 ----------
        before_main = count(cur, "SELECT COUNT(*) FROM ongoing_consumptions")
        before_opc = count(cur, "SELECT COUNT(*) FROM ongoing_product_consumptions")
        r = io_layer.one_insert_ongoing_product_consumption_to_mysql(
            cur, TABLE_ID, S_ID, PRODUCT_ID, PRODUCT_NAME, UNIT_PRICE, QUANTITY,
            PRODUCT_AMOUNT, ORDER_CREATED)
        after_main = count(cur, "SELECT COUNT(*) FROM ongoing_consumptions")
        after_opc = count(cur, "SELECT COUNT(*) FROM ongoing_product_consumptions")
        report("[3] one_insert_ongoing_product_consumption_to_mysql", r["status"], [
            ("主表 ongoing_consumptions %d -> %d（期望 +1）" % (before_main, after_main),
             after_main == before_main + 1),
            ("明细表 ongoing_product_consumptions %d -> %d（期望 +1）" % (before_opc, after_opc),
             after_opc == before_opc + 1),
        ])

        # ---------- 4. 删除上面两条待结算消费 ----------
        r_t = io_layer.one_delete_ongoing_consumption_from_mysql(cur, T_ID)
        r_s = io_layer.one_delete_ongoing_consumption_from_mysql(cur, S_ID)
        delete_ok = (r_t["status"] == "success" and r_s["status"] == "success")
        left_main = count(cur, "SELECT COUNT(*) FROM ongoing_consumptions WHERE id IN (%s, %s)",
                          (T_ID, S_ID))
        left_otf = count(cur, "SELECT COUNT(*) FROM ongoing_table_fees WHERE consumption_id = %s",
                         (T_ID,))
        left_opc = count(cur, "SELECT COUNT(*) FROM ongoing_product_consumptions WHERE consumption_id = %s",
                         (S_ID,))
        report("[4] one_delete_ongoing_consumption_from_mysql",
               "success" if delete_ok else "error", [
            ("台位费删除 status=%s，商品消费删除 status=%s（期望均 success）"
             % (r_t["status"], r_s["status"]), delete_ok),
            ("主表剩余（期望 0）", left_main == 0),
            ("ongoing_table_fees 剩余（期望 0）", left_otf == 0),
            ("ongoing_product_consumptions 剩余（期望 0）", left_opc == 0),
        ])

        # ---------- 5. 插入订单主记录 ----------
        before_orders = count(cur, "SELECT COUNT(*) FROM orders")
        r = io_layer.one_insert_order_to_mysql(
            cur, ORDER_ID, TABLE_ID, ORDER_TOTAL, ORDER_CREATED)
        after_orders = count(cur, "SELECT COUNT(*) FROM orders")
        cur.execute("SELECT `支付状态` FROM orders WHERE order_id = %s", (ORDER_ID,))
        row = cur.fetchone()
        got_status = row[0] if row else None
        report("[5] one_insert_order_to_mysql", r["status"], [
            ("orders 行数 %d -> %d（期望 +1）" % (before_orders, after_orders),
             after_orders == before_orders + 1),
            ("支付状态 期望 未支付 实际 %s" % got_status, got_status == "未支付"),
        ])

        # ---------- 6. 插入订单台费明细 ----------
        before_oc = count(cur, "SELECT COUNT(*) FROM order_consumptions WHERE order_id = %s",
                          (ORDER_ID,))
        before_otf2 = count(cur, "SELECT COUNT(*) FROM order_table_fees WHERE order_id = %s",
                            (ORDER_ID,))
        r = io_layer.one_insert_order_table_fee_to_mysql(
            cur, ORDER_ID, ORDER_T_ID, START_TIME, CLOSE_TIME, HOUR_PRICE, AMOUNT)
        after_oc = count(cur, "SELECT COUNT(*) FROM order_consumptions WHERE order_id = %s",
                         (ORDER_ID,))
        after_otf2 = count(cur, "SELECT COUNT(*) FROM order_table_fees WHERE order_id = %s",
                           (ORDER_ID,))
        report("[6] one_insert_order_table_fee_to_mysql", r["status"], [
            ("order_consumptions %d -> %d（期望 +1）" % (before_oc, after_oc),
             after_oc == before_oc + 1),
            ("order_table_fees %d -> %d（期望 +1）" % (before_otf2, after_otf2),
             after_otf2 == before_otf2 + 1),
        ])

        # ---------- 7. 插入订单商品明细 ----------
        before_oc = count(cur, "SELECT COUNT(*) FROM order_consumptions WHERE order_id = %s",
                          (ORDER_ID,))
        before_opc2 = count(cur, "SELECT COUNT(*) FROM order_product_consumptions WHERE order_id = %s",
                            (ORDER_ID,))
        r = io_layer.one_insert_order_product_consumption_to_mysql(
            cur, ORDER_ID, ORDER_S_ID, PRODUCT_ID, PRODUCT_NAME, UNIT_PRICE, QUANTITY,
            PRODUCT_AMOUNT)
        after_oc = count(cur, "SELECT COUNT(*) FROM order_consumptions WHERE order_id = %s",
                         (ORDER_ID,))
        after_opc2 = count(cur, "SELECT COUNT(*) FROM order_product_consumptions WHERE order_id = %s",
                           (ORDER_ID,))
        report("[7] one_insert_order_product_consumption_to_mysql", r["status"], [
            ("order_consumptions %d -> %d（期望 +1）" % (before_oc, after_oc),
             after_oc == before_oc + 1),
            ("order_product_consumptions %d -> %d（期望 +1）" % (before_opc2, after_opc2),
             after_opc2 == before_opc2 + 1),
        ])

        # ---------- 8. 更新订单为已支付 ----------
        r = io_layer.one_update_order_payment_status_in_mysql(cur, ORDER_ID, PAY_TIME)
        cur.execute("SELECT `支付状态`, `支付时间` FROM orders WHERE order_id = %s", (ORDER_ID,))
        row = cur.fetchone()
        got_status, got_pay_time = (row[0], row[1]) if row else (None, None)
        report("[8] one_update_order_payment_status_in_mysql", r["status"], [
            ("支付状态 期望 已支付 实际 %s" % got_status, got_status == "已支付"),
            ("支付时间 期望 %s 实际 %s" % (PAY_TIME, got_pay_time), got_pay_time == PAY_TIME),
        ])

        # ---------- 9. 扣减商品库存 ----------
        cur.execute("SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,))
        stock_before = cur.fetchone()[0]
        r = io_layer.one_deduct_product_stock_in_mysql(cur, PRODUCT_ID, DEDUCT_QTY)
        cur.execute("SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,))
        stock_after = cur.fetchone()[0]
        report("[9] one_deduct_product_stock_in_mysql", r["status"], [
            ("库存 %s -> %s（期望 -%d）" % (stock_before, stock_after, DEDUCT_QTY),
             stock_after == stock_before - DEDUCT_QTY),
        ])

        # ---------- 10. 插入核心备份 ----------
        before_cb = count(cur, "SELECT COUNT(*) FROM core_backup")
        r = io_layer.one_insert_core_backup_to_mysql(
            cur, T_ID, TABLE_ID, START_TIME, CLOSE_TIME)
        after_cb = count(cur, "SELECT COUNT(*) FROM core_backup")
        report("[10] one_insert_core_backup_to_mysql", r["status"], [
            ("core_backup 行数 %d -> %d（期望 +1）" % (before_cb, after_cb),
             after_cb == before_cb + 1),
        ])

    finally:
        # ---------- 统一回滚，不留下任何数据 ----------
        conn.rollback()
        cur.close()
        conn.close()

    # 回滚后，用一条全新连接再查一次数据库
    verify_conn = open_connection()
    after_rollback = snapshot(verify_conn)
    verify_conn.close()

    changed = [k for k in baseline if baseline[k] != after_rollback.get(k)]
    rollback_ok = (not changed)

    # ---------- 总结论 ----------
    total = len(RESULTS)
    failed = [name for name, ok in RESULTS if not ok]
    print("=" * 68)
    if not failed:
        print("总结论：%d 个写函数全部通过。" % total)
    else:
        print("总结论：%d 个写函数中有 %d 个失败。" % (total, len(failed)))
        for name in failed:
            print("    失败：%s" % name)

    print("回滚后数据库状态未改变：%s" % ("是" if rollback_ok else "否"))
    if not rollback_ok:
        for key in changed:
            print("    变化项：%s 基线=%s 回滚后=%s"
                  % (key, baseline[key], after_rollback.get(key)))


if __name__ == "__main__":
    main()