#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台球厅管理系统 - 数据一致性校验脚本（JSON -> MySQL 迁移后自检）
================================================================
用途：
    连接 MySQL，对迁移后的数据做只读一致性校验，并打印报告。
    本脚本只检查、只打印，绝不修改任何数据（不修复、不回写）。

必查项目（对应需求 1~4）：
    1. 行数对齐：type_code='T' 的主表条数 == ongoing_table_fees 条数；
                 type_code='S' 的主表条数 == ongoing_product_consumptions 条数。
    2. 时间字段：查出所有 type_code='S' 的主表记录，检查其“创建时间”是否
                 被错误地写成了同桌台的“开台时间”（迁移脚本 bug 的特征）；
                 并对照“消费记录编号”里内嵌的下单时间戳。
    3. 孤儿记录：明细表里有、但主表里没有对应 id 的记录。
    4. 金额验算：台费 金额 = (关台时间 - 开台时间) / 3600 * 每小时价格；
                 商品 金额 = 单价 * 数量。

附加项目（可选，默认开启）：
    ongoing_* 三张表当前若无数据，上面 4 项会全部通过但没有实际意义。
    因此脚本额外对结构一致的订单表（orders / order_consumptions /
    order_table_fees / order_product_consumptions）跑同样的
    “关联对齐 / 孤儿 / 金额”校验，结果单独列在 [附加] 一节。
    不需要时把 RUN_EXTRA_ORDER_CHECKS 改成 False 即可。

运行方式：
    python check_data.py

依赖（二选一，脚本会自动探测）：
    pip install mysql-connector-python
    pip install pymysql
"""

import os
import sys
from datetime import datetime

# ============================================================
# 连接库：优先 mysql-connector-python，回退 pymysql
# ============================================================
try:
    import mysql.connector as _drv
    DRIVER_NAME = "mysql-connector-python"

    def _connect(cfg):
        return _drv.connect(**cfg)
except ImportError:
    try:
        import pymysql as _drv
        DRIVER_NAME = "pymysql"

        def _connect(cfg):
            return _drv.connect(**cfg)
    except ImportError:
        print("[致命错误] 未找到 MySQL 驱动，请先安装其中之一：")
        print("    pip install mysql-connector-python")
        print("    pip install pymysql")
        sys.exit(1)


# ============================================================
# 配置区
# ============================================================

# 数据库连接（可用环境变量覆盖）
# 【密码安全提醒】为了防止密码泄露：请确保在本地系统环境变量中设置 MYSQL_PASSWORD，
# 或在本地配置 .env 文件。切勿在代码中硬编码密码提交到 GitHub。
MYSQL_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "billiards_hall"),
    "charset": "utf8mb4",
}

# 金额允许误差（元）。台费另有“向下取整”业务规则，见规则 4。
TOLERANCE = 0.01

# 是否对订单表执行附加校验（ongoing_* 三张表为空时尤其有用）
RUN_EXTRA_ORDER_CHECKS = True


# ============================================================
# 表名 / 字段名常量（MySQL 中文字段名需要反引号包裹）
# ============================================================
T_MAIN = "ongoing_consumptions"           # 待结算消费主表
T_FEE = "ongoing_table_fees"              # 待结算台位费明细
T_PROD = "ongoing_product_consumptions"   # 待结算商品明细

T_ORDERS = "orders"                       # 订单主表
T_ODR_REL = "order_consumptions"          # 订单消费关联表
T_ODR_FEE = "order_table_fees"            # 订单台位费明细
T_ODR_PROD = "order_product_consumptions" # 订单商品明细

C_ID = "id"
C_OID = "order_id"
C_CID = "consumption_id"
C_TYPE = "type_code"
C_TABLE = "桌台编号"
C_CREATE = "创建时间"
C_OPEN = "开台时间"
C_CLOSE = "关台时间"
C_PRICE = "每小时价格"
C_UPRICE = "单价"
C_QTY = "数量"
C_AMT = "金额"


def q(name):
    """用反引号包裹列名 / 表名，兼容中文标识符。"""
    return "`" + name + "`"


# ============================================================
# 报告输出小工具
# ============================================================
def hr(ch="=", n=64):
    print(ch * n)


def fmt_money(v):
    return "NULL" if v is None else "%.2f" % float(v)


def parse_ts_from_id(consumption_id):
    """
    从消费记录编号解析时间戳。
    编号规则：T_<桌台编号>_<开台时间戳>  或  S_<桌台编号>_<下单时间戳>
    例：S_1_1757688000 -> 1757688000。解析失败返回 None。
    """
    parts = str(consumption_id).split("_")
    if len(parts) >= 3:
        try:
            return int(parts[-1])
        except ValueError:
            return None
    return None


def print_ids(ids, per_line=6):
    """打印 id 列表，每行若干，便于排查。"""
    line = "     "
    for i, cid in enumerate(ids, 1):
        line += "%-26s" % cid
        if i % per_line == 0:
            print(line)
            line = "     "
    if line.strip():
        print(line)# ============================================================
# 通用查询 / 校验函数
# ============================================================
def count_rows(cur, table, where=None):
    sql = "SELECT COUNT(*) FROM %s" % q(table)
    if where:
        sql += " WHERE " + where
    cur.execute(sql)
    return cur.fetchone()[0]


def find_orphans(cur, child_table, child_col, parent_table, parent_col):
    """返回 child_table.child_col 中、在 parent_table.parent_col 里找不到的记录。"""
    cur.execute(
        "SELECT d.%s FROM %s d "
        "LEFT JOIN %s p ON p.%s = d.%s "
        "WHERE p.%s IS NULL"
        % (q(child_col), q(child_table), q(parent_table), q(parent_col),
           q(child_col), q(parent_col))
    )
    return [r[0] for r in cur.fetchall()]


def check_fee_amounts(cur, table):
    """
    台费金额校验：金额 = (关台时间 - 开台时间) / 3600 * 每小时价格。
    业务规则为“向下取整”，故金额等于原值或等于取整值都算通过。
    返回 (总条数, 跳过数, 异常列表)。
    """
    cur.execute(
        "SELECT %s, %s, %s, %s, %s FROM %s"
        % (q(C_CID), q(C_OPEN), q(C_CLOSE), q(C_PRICE), q(C_AMT), q(table))
    )
    total = skipped = 0
    bad = []
    for cid, open_t, close_t, price, amt in cur.fetchall():
        total += 1
        if close_t is None or amt is None:      # 未关台 / 未计算
            skipped += 1
            continue
        raw = (int(close_t) - int(open_t)) / 3600.0 * float(price)
        trunc = int(raw)                        # 业务规则：向下取整
        ok = abs(float(amt) - raw) <= TOLERANCE or float(amt) == trunc
        if not ok or int(close_t) < int(open_t):
            bad.append((cid, open_t, close_t, price, amt, raw, trunc,
                        int(close_t) < int(open_t)))
    return total, skipped, bad


def print_fee_amount_table(bad):
    print("    %-24s %-11s %-11s %-8s %-9s %-10s %-10s"
          % ("消费记录编号", "开台时间", "关台时间", "单价", "记录金额",
             "应收(原值)", "应收(取整)"))
    for cid, o, c, pr, amt, raw, trunc, neg in bad:
        flag = "  [开台>关台!]" if neg else ""
        print("    %-24s %-11s %-11s %-8s %-9s %-10.2f %-10d%s"
              % (cid, o, c, pr, fmt_money(amt), raw, trunc, flag))


def check_prod_amounts(cur, table):
    """商品金额校验：金额 = 单价 * 数量。返回 (总条数, 异常列表)。"""
    cur.execute(
        "SELECT %s, %s, %s, %s FROM %s"
        % (q(C_CID), q(C_UPRICE), q(C_QTY), q(C_AMT), q(table))
    )
    rows = cur.fetchall()
    bad = []
    for cid, uprice, qty, amt in rows:
        expected = float(uprice) * int(qty)
        if abs(float(amt) - expected) > TOLERANCE:
            bad.append((cid, uprice, qty, amt, expected))
    return len(rows), bad


def print_prod_amount_table(bad):
    print("    %-24s %-8s %-6s %-10s %-10s"
          % ("消费记录编号", "单价", "数量", "记录金额", "应收金额"))
    for cid, uprice, qty, amt, expected in bad:
        print("    %-24s %-8s %-6s %-10s %-10.2f"
              % (cid, uprice, qty, fmt_money(amt), expected))


def print_data_overview(cur):
    print("\n[0] 各表记录数总览")
    print("-" * 64)
    for t in (T_MAIN, T_FEE, T_PROD, T_ORDERS, T_ODR_REL, T_ODR_FEE,
              T_ODR_PROD):
        print("  %-32s : %d" % (t, count_rows(cur, t)))# ============================================================
# 主流程
# ============================================================
def main():
    hr()
    print("  台球厅 MySQL 数据一致性校验报告")
    print("  数据库: %s   驱动: %s" % (MYSQL_CONFIG["database"], DRIVER_NAME))
    print("  生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    hr()

    try:
        conn = _connect(MYSQL_CONFIG)
    except Exception as e:
        print("\n[致命错误] 数据库连接失败：%s" % e)
        print("请确认 MySQL 已启动、配置正确，或设置环境变量 MYSQL_PASSWORD 等。")
        sys.exit(1)

    cur = conn.cursor()
    issues = {}
    try:
        print_data_overview(cur)
        check_row_alignment(cur, issues)
        check_s_create_time(cur, issues)
        check_orphans(cur, issues)
        check_amounts(cur, issues)
        if RUN_EXTRA_ORDER_CHECKS:
            check_orders_extra(cur, issues)
        print_summary(issues)
    finally:
        cur.close()
        conn.close()


# ---------- 规则 1：行数对齐 ----------
def check_row_alignment(cur, issues):
    print("\n[1] 行数对齐检查（待结算消费主表 vs 明细表）")
    print("-" * 64)

    cnt_t = count_rows(cur, T_MAIN, "%s = 'T'" % q(C_TYPE))
    cnt_fee = count_rows(cur, T_FEE)
    cnt_s = count_rows(cur, T_MAIN, "%s = 'S'" % q(C_TYPE))
    cnt_prod = count_rows(cur, T_PROD)

    print("  主表 type_code='T'            : %d" % cnt_t)
    print("  %-30s : %d" % (T_FEE, cnt_fee))
    print("  主表 type_code='S'            : %d" % cnt_s)
    print("  %-30s : %d" % (T_PROD, cnt_prod))

    n = 0
    if cnt_t == cnt_fee:
        print("  -> 台位费(T)行数对齐: 通过")
    else:
        print("  -> 台位费(T)行数对齐: 不通过 (相差 %d)" % (cnt_t - cnt_fee))
        n += 1
    if cnt_s == cnt_prod:
        print("  -> 商品(S)行数对齐: 通过")
    else:
        print("  -> 商品(S)行数对齐: 不通过 (相差 %d)" % (cnt_s - cnt_prod))
        n += 1

    # 定位具体记录：主表标记 T/S 但明细缺失
    cur.execute(
        "SELECT c.%s FROM %s c LEFT JOIN %s d ON d.%s = c.%s "
        "WHERE c.%s = 'T' AND d.%s IS NULL"
        % (q(C_ID), q(T_MAIN), q(T_FEE), q(C_CID), q(C_ID), q(C_TYPE),
           q(C_CID))
    )
    miss_fee = [r[0] for r in cur.fetchall()]
    if miss_fee:
        print("     主表标记 T、但台位费明细缺失的 id (%d 个):" % len(miss_fee))
        print_ids(miss_fee)

    cur.execute(
        "SELECT c.%s FROM %s c LEFT JOIN %s d ON d.%s = c.%s "
        "WHERE c.%s = 'S' AND d.%s IS NULL"
        % (q(C_ID), q(T_MAIN), q(T_PROD), q(C_CID), q(C_ID), q(C_TYPE),
           q(C_CID))
    )
    miss_prod = [r[0] for r in cur.fetchall()]
    if miss_prod:
        print("     主表标记 S、但商品明细缺失的 id (%d 个):" % len(miss_prod))
        print_ids(miss_prod)

    issues["rule1"] = n


# ---------- 规则 2：S 记录的创建时间 ----------
def check_s_create_time(cur, issues):
    print("\n[2] 时间字段检查（type_code='S' 的创建时间）")
    print("-" * 64)

    n = 0

    # 2a. 创建时间 误等于 同桌台的 开台时间（迁移 bug 特征）
    cur.execute(
        "SELECT c.%s, c.%s, c.%s FROM %s c "
        "WHERE c.%s = 'S' AND EXISTS ("
        "  SELECT 1 FROM %s t "
        "  WHERE t.%s = c.%s AND t.%s = c.%s)"
        % (q(C_ID), q(C_TABLE), q(C_CREATE), q(T_MAIN), q(C_TYPE),
           q(T_FEE), q(C_TABLE), q(C_TABLE), q(C_OPEN), q(C_CREATE))
    )
    bad = cur.fetchall()
    if bad:
        n += len(bad)
        print("  发现“创建时间”误等于“开台时间”的 S 记录 (%d 条)：" % len(bad))
        print("    %-24s %-8s %-14s" % ("消费记录编号", "桌台", "创建时间"))
        for cid, tid, ctime in bad:
            print("    %-24s %-8s %-14s" % (cid, tid, ctime))
        print("    >> 这些记录的创建时间疑似被错误地写成了开台时间。")
    else:
        print("  未发现“创建时间”误等于“开台时间”的 S 记录: 通过")

    # 2b. 附加证据：创建时间 vs 记录编号内嵌的下单时间戳
    cur.execute(
        "SELECT %s, %s, %s FROM %s WHERE %s = 'S'"
        % (q(C_ID), q(C_TABLE), q(C_CREATE), q(T_MAIN), q(C_TYPE))
    )
    mismatch = []
    for cid, tid, ctime in cur.fetchall():
        id_ts = parse_ts_from_id(cid)
        if id_ts is not None and int(ctime) != id_ts:
            mismatch.append((cid, tid, ctime, id_ts))
    if mismatch:
        n += len(mismatch)
        print("\n  附加：S 记录“创建时间”与记录编号内嵌时间戳不一致 (%d 条)："
              % len(mismatch))
        print("    %-24s %-8s %-12s %-12s"
              % ("消费记录编号", "桌台", "创建时间", "编号内时间"))
        for cid, tid, ctime, id_ts in mismatch:
            print("    %-24s %-8s %-12s %-12s" % (cid, tid, ctime, id_ts))
        print("    >> 编号规则 S_<桌台>_<下单时间戳>，编号内时间戳才是正确下单时间。")

    if n == 0:
        print("  时间字段检查: 通过")
    issues["rule2"] = n


# ---------- 规则 3：孤儿记录 ----------
def check_orphans(cur, issues):
    print("\n[3] 孤儿记录检查（明细有、主表无）")
    print("-" * 64)

    n = 0
    for detail in (T_FEE, T_PROD):
        orphans = find_orphans(cur, detail, C_CID, T_MAIN, C_ID)
        if orphans:
            n += len(orphans)
            print("  %s 中孤儿记录 (%d 条):" % (detail, len(orphans)))
            print_ids(orphans)
        else:
            print("  %s: 无孤儿记录 (通过)" % detail)

    issues["rule3"] = n# ---------- 规则 4：金额验算 ----------
def check_amounts(cur, issues):
    print("\n[4] 金额验算")
    print("-" * 64)

    n = 0

    total, skipped, bad_fee = check_fee_amounts(cur, T_FEE)
    print("  台费: 共 %d 条, 实校验 %d 条, 跳过(未关台/未计算) %d 条"
          % (total, total - skipped, skipped))
    if bad_fee:
        n += len(bad_fee)
        print("  台费金额异常 (%d 条)：" % len(bad_fee))
        print_fee_amount_table(bad_fee)
    else:
        print("  台费金额: 通过")

    total_p, bad_prod = check_prod_amounts(cur, T_PROD)
    print("\n  商品: 共 %d 条" % total_p)
    if bad_prod:
        n += len(bad_prod)
        print("  商品金额异常 (%d 条)：" % len(bad_prod))
        print_prod_amount_table(bad_prod)
    else:
        print("  商品金额: 通过")

    issues["rule4"] = n
    issues["rule4_skipped"] = skipped


# ---------- 附加：订单表同规则校验 ----------
def check_orders_extra(cur, issues):
    print("\n[附加] 订单表同规则校验（关联对齐 / 孤儿 / 金额）")
    print("-" * 64)

    n = 0

    rel_t = count_rows(cur, T_ODR_REL, "%s = 'T'" % q(C_TYPE))
    rel_s = count_rows(cur, T_ODR_REL, "%s = 'S'" % q(C_TYPE))
    fee_cnt = count_rows(cur, T_ODR_FEE)
    prod_cnt = count_rows(cur, T_ODR_PROD)
    print("  order_consumptions 'T': %d  vs  %s: %d  -> %s"
          % (rel_t, T_ODR_FEE, fee_cnt,
             "通过" if rel_t == fee_cnt else "不通过"))
    print("  order_consumptions 'S': %d  vs  %s: %d  -> %s"
          % (rel_s, T_ODR_PROD, prod_cnt,
             "通过" if rel_s == prod_cnt else "不通过"))
    if rel_t != fee_cnt or rel_s != prod_cnt:
        n += 1

    for detail in (T_ODR_FEE, T_ODR_PROD):
        orphans = find_orphans(cur, detail, C_OID, T_ORDERS, C_OID)
        if orphans:
            n += len(orphans)
            print("  %s 中 order_id 无对应订单 (%d 条):" % (detail, len(orphans)))
            print_ids(sorted(set(orphans)))
        else:
            print("  %s: 无孤儿记录 (通过)" % detail)

    total, skipped, bad_fee = check_fee_amounts(cur, T_ODR_FEE)
    print("  订单台费: 共 %d 条, 实校验 %d 条, 跳过 %d 条"
          % (total, total - skipped, skipped))
    if bad_fee:
        n += len(bad_fee)
        print("  订单台费金额异常 (%d 条)：" % len(bad_fee))
        print_fee_amount_table(bad_fee)
    else:
        print("  订单台费金额: 通过")

    total_p, bad_prod = check_prod_amounts(cur, T_ODR_PROD)
    print("  订单商品: 共 %d 条" % total_p)
    if bad_prod:
        n += len(bad_prod)
        print("  订单商品金额异常 (%d 条)：" % len(bad_prod))
        print_prod_amount_table(bad_prod)
    else:
        print("  订单商品金额: 通过")

    issues["extra"] = n


# ---------- 汇总 ----------
def print_summary(issues):
    total = sum(issues.get(k, 0)
                for k in ("rule1", "rule2", "rule3", "rule4", "extra"))
    print("\n" + "=" * 64)
    print("  校验汇总")
    print("=" * 64)
    print("  规则1 行数对齐 : %s (异常 %d)"
          % (ok_text(issues.get("rule1", 0)), issues.get("rule1", 0)))
    print("  规则2 时间字段 : %s (异常 %d)"
          % (ok_text(issues.get("rule2", 0)), issues.get("rule2", 0)))
    print("  规则3 孤儿记录 : %s (异常 %d)"
          % (ok_text(issues.get("rule3", 0)), issues.get("rule3", 0)))
    print("  规则4 金额验算 : %s (异常 %d, 跳过未计算 %d)"
          % (ok_text(issues.get("rule4", 0)), issues.get("rule4", 0),
             issues.get("rule4_skipped", 0)))
    if RUN_EXTRA_ORDER_CHECKS:
        print("  附加  订单表   : %s (异常 %d)"
              % (ok_text(issues.get("extra", 0)), issues.get("extra", 0)))
    hr("-")
    if total == 0:
        print("  结论: 全部通过，未发现数据一致性问题。")
    else:
        print("  结论: 共发现 %d 处异常，详见上方各类明细（本脚本不自动修复）。"
              % total)
    hr()


def ok_text(n):
    return "通过" if n == 0 else "不通过"


if __name__ == "__main__":
    main()
