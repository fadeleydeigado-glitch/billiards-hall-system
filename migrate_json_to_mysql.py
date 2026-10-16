#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台球厅管理系统 — JSON 到 MySQL 完整数据迁移脚本
====================================================
本脚本完成：
  1. 删除旧表（如果存在）
  2. 创建 11 张新表
  3. 从 5 个 JSON 文件迁移全部数据
  4. 验证各表记录数

对应关系:
  database.json        → billiards_tables, products
  Ongoing_order.json   → ongoing_consumptions, ongoing_table_fees, ongoing_product_consumptions
  unpaid order.json    → orders, order_consumptions, order_table_fees, order_product_consumptions
  history_orders.json  → orders (同上，合并)
  core_backup.json     → core_backup

运行方式:
  python migrate_json_to_mysql.py

前提条件:
  - MySQL 8.4 已启动
  - root 密码: 通过本地环境变量 MYSQL_PASSWORD（或 .env 文件）提供，切勿在代码中硬编码
  - mysql-connector-python 已安装 (pip install mysql-connector-python)
  - 所有 JSON 文件与本脚本在同一目录
"""

import json
import os
import mysql.connector

# ============================================================
# 配置区
# ============================================================
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    # 【密码安全提醒】为了防止密码泄露：请确保在本地系统环境变量中设置 MYSQL_PASSWORD，
    # 或在本地配置 .env 文件。切勿在代码中硬编码密码提交到 GitHub。
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": "billiards_hall",   # 目标数据库
    "charset": "utf8mb4",
}

# ============================================================
# 工具函数
# ============================================================

def load_json(filename):
    """读取 JSON 文件，返回 Python 字典"""
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)


def bt(col):
    """给列名加反引号（MySQL 中文字段名需要）"""
    return chr(96) + col + chr(96)  # chr(96) = `


# ============================================================
# 中文字段名（使用 Python unicode 转义，确保文件是纯 ASCII，
# 避免任何编码问题）
# ============================================================
FN = {
    "msjg": "\u6bcf\u5c0f\u65f6\u4ef7\u683c",   # 每小时价格
    "spmc": "\u5546\u54c1\u540d\u79f0",         # 商品名称
    "dj":   "\u5355\u4ef7",                     # 单价
    "kc":   "\u5e93\u5b58",                     # 库存
    "ztbh": "\u684c\u53f0\u7f16\u53f7",         # 桌台编号
    "ktsj": "\u5f00\u53f0\u65f6\u95f4",         # 开台时间
    "gtsj": "\u5173\u53f0\u65f6\u95f4",         # 关台时间
    "je":   "\u91d1\u989d",                     # 金额
    "spbh": "\u5546\u54c1\u7f16\u53f7",         # 商品编号
    "sl":   "\u6570\u91cf",                     # 数量
    "cjsj": "\u521b\u5efa\u65f6\u95f4",         # 创建时间
    "zfzt": "\u652f\u4ed8\u72b6\u6001",         # 支付状态
    "zje":  "\u603b\u91d1\u989d",               # 总金额
    "zfsj": "\u652f\u4ed8\u65f6\u95f4",         # 支付时间
    "djzxf":"\u5f85\u7ed3\u7b97\u6d88\u8d39",   # 待结算消费
    "xfmx": "\u6d88\u8d39\u660e\u7ec6",         # 消费明细
    "xfjlbh":"\u6d88\u8d39\u8bb0\u5f55\u7f16\u53f7", # 消费记录编号
    "zzhm": "\u684c\u5b50\u53f7\u7801",         # 桌子号码
    "wzf":  "\u672a\u652f\u4ed8",               # 未支付
    "twf":  "\u53f0\u4f4d\u8d39",               # 台位费
    "sp":   "\u5546\u54c1",                     # 商品
}


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("台球厅 JSON → MySQL 数据迁移")
    print("=" * 60)

    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cur = conn.cursor()

    # ---------- 第一步：删除旧表 ----------
    print("\n>>> 删除旧表...")
    drop_order = [
        "order_product_consumptions", "order_table_fees",
        "order_consumptions", "orders",
        "ongoing_product_consumptions", "ongoing_table_fees",
        "ongoing_consumptions", "core_backup",
        "products", "billiards_tables", "consumption_types"
    ]
    for t in drop_order:
        cur.execute("DROP TABLE IF EXISTS " + bt(t))
    conn.commit()
    print("旧表已清除")

    # ---------- 第二步：建表 ----------
    print("\n>>> 创建 11 张表...")

    cur.execute("""CREATE TABLE consumption_types (
        type_code CHAR(1) NOT NULL PRIMARY KEY,
        type_name VARCHAR(20) NOT NULL
    ) COMMENT='消费类型参照表'""")

    cur.execute(f"""CREATE TABLE billiards_tables (
        id VARCHAR(10) NOT NULL PRIMARY KEY,
        {bt(FN["msjg"])} DECIMAL(10,2) NOT NULL
    ) COMMENT='桌台配置表'""")

    cur.execute(f"""CREATE TABLE products (
        id VARCHAR(10) NOT NULL PRIMARY KEY,
        {bt(FN["spmc"])} VARCHAR(50) NOT NULL,
        {bt(FN["dj"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["kc"])} INT NOT NULL DEFAULT 0
    ) COMMENT='商品配置表'""")

    cur.execute(f"""CREATE TABLE ongoing_consumptions (
        id VARCHAR(30) NOT NULL PRIMARY KEY,
        {bt(FN["ztbh"])} VARCHAR(10) NOT NULL,
        type_code CHAR(1) NOT NULL,
        {bt(FN["cjsj"])} BIGINT NOT NULL,
        INDEX idx_ogc_t ({bt(FN["ztbh"])}),
        INDEX idx_ogc_ty (type_code),
        CONSTRAINT fk_ogc_ty FOREIGN KEY (type_code)
            REFERENCES consumption_types(type_code)
    ) COMMENT='待结算消费主表'""")

    cur.execute(f"""CREATE TABLE ongoing_table_fees (
        consumption_id VARCHAR(30) NOT NULL PRIMARY KEY,
        {bt(FN["ztbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["ktsj"])} BIGINT NOT NULL,
        {bt(FN["gtsj"])} BIGINT NULL,
        {bt(FN["msjg"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["je"])} DECIMAL(10,2) NULL,
        INDEX idx_otf_t ({bt(FN["ztbh"])}),
        CONSTRAINT fk_otf_c FOREIGN KEY (consumption_id)
            REFERENCES ongoing_consumptions(id)
    ) COMMENT='待结算台位费明细'""")

    cur.execute(f"""CREATE TABLE ongoing_product_consumptions (
        consumption_id VARCHAR(30) NOT NULL PRIMARY KEY,
        {bt(FN["ztbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["spbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["spmc"])} VARCHAR(50) NOT NULL,
        {bt(FN["dj"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["sl"])} INT NOT NULL,
        {bt(FN["je"])} DECIMAL(10,2) NOT NULL,
        INDEX idx_opc_t ({bt(FN["ztbh"])}),
        CONSTRAINT fk_opc_c FOREIGN KEY (consumption_id)
            REFERENCES ongoing_consumptions(id)
    ) COMMENT='待结算商品消费明细'""")

    cur.execute(f"""CREATE TABLE orders (
        order_id VARCHAR(30) NOT NULL PRIMARY KEY,
        {bt(FN["ztbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["zfzt"])} VARCHAR(10) NOT NULL DEFAULT '{FN["wzf"]}',
        {bt(FN["zje"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["cjsj"])} BIGINT NOT NULL,
        {bt(FN["zfsj"])} BIGINT NULL,
        INDEX idx_o_t ({bt(FN["ztbh"])}),
        INDEX idx_o_s ({bt(FN["zfzt"])}),
        INDEX idx_o_tm ({bt(FN["cjsj"])})
    ) COMMENT='订单主表'""")

    cur.execute("""CREATE TABLE order_consumptions (
        consumption_id VARCHAR(30) NOT NULL,
        order_id VARCHAR(30) NOT NULL,
        type_code CHAR(1) NOT NULL,
        PRIMARY KEY (consumption_id, order_id),
        INDEX idx_occ_o (order_id),
        INDEX idx_occ_ty (type_code),
        CONSTRAINT fk_occ_o FOREIGN KEY (order_id)
            REFERENCES orders(order_id),
        CONSTRAINT fk_occ_ty FOREIGN KEY (type_code)
            REFERENCES consumption_types(type_code)
    ) COMMENT='订单消费关联表'""")

    cur.execute(f"""CREATE TABLE order_table_fees (
        consumption_id VARCHAR(30) NOT NULL,
        order_id VARCHAR(30) NOT NULL,
        {bt(FN["ktsj"])} BIGINT NOT NULL,
        {bt(FN["gtsj"])} BIGINT NOT NULL,
        {bt(FN["msjg"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["je"])} DECIMAL(10,2) NOT NULL,
        PRIMARY KEY (consumption_id, order_id),
        INDEX idx_otfo_o (order_id),
        CONSTRAINT fk_otfo_o FOREIGN KEY (order_id)
            REFERENCES orders(order_id)
    ) COMMENT='订单台位费明细'""")

    cur.execute(f"""CREATE TABLE order_product_consumptions (
        consumption_id VARCHAR(30) NOT NULL,
        order_id VARCHAR(30) NOT NULL,
        {bt(FN["spbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["spmc"])} VARCHAR(50) NOT NULL,
        {bt(FN["dj"])} DECIMAL(10,2) NOT NULL,
        {bt(FN["sl"])} INT NOT NULL,
        {bt(FN["je"])} DECIMAL(10,2) NOT NULL,
        PRIMARY KEY (consumption_id, order_id),
        INDEX idx_opco_o (order_id),
        CONSTRAINT fk_opco_o FOREIGN KEY (order_id)
            REFERENCES orders(order_id)
    ) COMMENT='订单商品明细'""")

    cur.execute(f"""CREATE TABLE core_backup (
        consumption_id VARCHAR(30) NOT NULL PRIMARY KEY,
        {bt(FN["ztbh"])} VARCHAR(10) NOT NULL,
        {bt(FN["ktsj"])} BIGINT NOT NULL,
        {bt(FN["gtsj"])} BIGINT NOT NULL,
        INDEX idx_cb_t ({bt(FN["ztbh"])})
    ) COMMENT='核心备份表'""")

    cur.execute(f"INSERT INTO consumption_types (type_code, type_name) VALUES ('T','{FN['twf']}'),('S','{FN['sp']}')")

    conn.commit()
    print("11 张表创建完成")

    # ---------- 第三步：迁移数据 ----------
    print("\n>>> 开始数据迁移...")

    # --- database.json ---
    print("--- database.json ---")
    db = load_json("database.json")
    for tid, info in db.get("billiards_tables", {}).items():
        cur.execute(
            f"INSERT INTO billiards_tables (id, {bt(FN['msjg'])}) VALUES (%s,%s)",
            (str(tid), info[FN["msjg"]])
        )
    print(f"  billiards_tables: {len(db['billiards_tables'])} 条")

    for pid, info in db.get("products", {}).items():
        cur.execute(
            f"INSERT INTO products (id, {bt(FN['spmc'])}, {bt(FN['dj'])}, {bt(FN['kc'])}) VALUES (%s,%s,%s,%s)",
            (pid, info[FN["spmc"]], info[FN["dj"]], info[FN["kc"]])
        )
    print(f"  products: {len(db['products'])} 条")

    conn.commit()

    # --- Ongoing_order.json ---
    print("--- Ongoing_order.json ---")
    ongoing = load_json("Ongoing_order.json")
    oc = tf = pc = 0
    for k, v in ongoing.items():
        if k.startswith("_"):
            continue
        tid = str(k)
        for cid, d in v.get(FN["djzxf"], {}).items():
            ct = cid[0]
            cur.execute(
                f"INSERT INTO ongoing_consumptions (id, {bt(FN['ztbh'])}, type_code, {bt(FN['cjsj'])}) VALUES (%s,%s,%s,%s)",
                (cid, tid, ct, d.get(FN["ktsj"], 0))
            )
            oc += 1
            if ct == "T":
                cur.execute(
                    f"INSERT INTO ongoing_table_fees (consumption_id, {bt(FN['ztbh'])}, {bt(FN['ktsj'])}, {bt(FN['gtsj'])}, {bt(FN['msjg'])}, {bt(FN['je'])}) VALUES (%s,%s,%s,%s,%s,%s)",
                    (cid, tid, d.get(FN["ktsj"], 0), d.get(FN["gtsj"]),
                     d.get(FN["msjg"], 0), d.get(FN["je"]))
                )
                tf += 1
            elif ct == "S":
                cur.execute(
                    f"INSERT INTO ongoing_product_consumptions (consumption_id, {bt(FN['ztbh'])}, {bt(FN['spbh'])}, {bt(FN['spmc'])}, {bt(FN['dj'])}, {bt(FN['sl'])}, {bt(FN['je'])}) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (cid, tid, d.get(FN["spbh"], ""), d.get(FN["spmc"], ""),
                     d.get(FN["dj"], 0), d.get(FN["sl"], 0), d.get(FN["je"], 0))
                )
                pc += 1
    print(f"  ongoing_consumptions: {oc} 条")
    print(f"  ongoing_table_fees: {tf} 条")
    print(f"  ongoing_product_consumptions: {pc} 条")

    conn.commit()

    # --- Orders (unpaid + history) ---
    def migrate_orders(filename, label):
        data = load_json(filename)
        o = oc2 = otf = opc = 0
        for oid, oi in data.items():
            if not isinstance(oi, dict) or FN["zfzt"] not in oi:
                continue
            parts = oid.split("_")
            tid = parts[0]
            ctime = int(parts[1]) if len(parts) > 1 else 0
            cur.execute(
                f"INSERT INTO orders (order_id, {bt(FN['ztbh'])}, {bt(FN['zfzt'])}, {bt(FN['zje'])}, {bt(FN['cjsj'])}, {bt(FN['zfsj'])}) VALUES (%s,%s,%s,%s,%s,%s)",
                (oid, tid, oi[FN["zfzt"]], oi[FN["zje"]], ctime,
                 oi.get(FN["zfsj"]))
            )
            o += 1
            for item in oi.get(FN["xfmx"], []):
                cid = item[FN["xfjlbh"]]
                ct = cid[0]
                cur.execute(
                    "INSERT INTO order_consumptions (consumption_id, order_id, type_code) VALUES (%s,%s,%s)",
                    (cid, oid, ct)
                )
                oc2 += 1
                if ct == "T":
                    cur.execute(
                        f"INSERT INTO order_table_fees (consumption_id, order_id, {bt(FN['ktsj'])}, {bt(FN['gtsj'])}, {bt(FN['msjg'])}, {bt(FN['je'])}) VALUES (%s,%s,%s,%s,%s,%s)",
                        (cid, oid, item.get(FN["ktsj"], 0),
                         item.get(FN["gtsj"], 0), item.get(FN["msjg"], 0),
                         item.get(FN["je"], 0))
                    )
                    otf += 1
                elif ct == "S":
                    cur.execute(
                        f"INSERT INTO order_product_consumptions (consumption_id, order_id, {bt(FN['spbh'])}, {bt(FN['spmc'])}, {bt(FN['dj'])}, {bt(FN['sl'])}, {bt(FN['je'])}) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (cid, oid, item.get(FN["spbh"], ""),
                         item.get(FN["spmc"], ""), item.get(FN["dj"], 0),
                         item.get(FN["sl"], 0), item.get(FN["je"], 0))
                    )
                    opc += 1
        print(f"  [{label}] orders={o}, oc={oc2}, table_fees={otf}, products={opc}")

    print("--- unpaid order.json ---")
    migrate_orders("unpaid order.json", "unpaid")

    print("--- history_orders.json ---")
    migrate_orders("history_orders.json", "history")

    # --- core_backup.json ---
    print("--- core_backup.json ---")
    backup = load_json("core_backup.json")
    cb = 0
    for cid, info in backup.items():
        cur.execute(
            f"INSERT INTO core_backup (consumption_id, {bt(FN['ztbh'])}, {bt(FN['ktsj'])}, {bt(FN['gtsj'])}) VALUES (%s,%s,%s,%s)",
            (cid, str(info[FN["zzhm"]]), info[FN["ktsj"]], info[FN["gtsj"]])
        )
        cb += 1
    print(f"  core_backup: {cb} 条")

    conn.commit()
    print("\n>>> 事务已提交")

    # ---------- 第四步：验证 ----------
    print("\n" + "=" * 60)
    print("验证 — 各表记录数")
    print("=" * 60)

    tables = [
        "consumption_types", "billiards_tables", "products",
        "ongoing_consumptions", "ongoing_table_fees",
        "ongoing_product_consumptions",
        "orders", "order_consumptions", "order_table_fees",
        "order_product_consumptions", "core_backup"
    ]

    grand_total = 0
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {bt(t)}")
        count = cur.fetchone()[0]
        grand_total += count
        print(f"  {t:35s} {count:>5} 条")

    print(f"  {'─' * 40}")
    print(f"  {'合计':35s} {grand_total:>5} 条")

    # 抽查数据
    print("\n--- 抽查：products 表前 2 行 ---")
    cur.execute(f"SELECT id, {bt(FN['spmc'])}, {bt(FN['dj'])}, {bt(FN['kc'])} FROM products LIMIT 2")
    for row in cur.fetchall():
        print(f"  {row}")

    print("\n--- 抽查：orders 表（按状态）---")
    cur.execute(f"SELECT {bt(FN['zfzt'])}, COUNT(*) FROM orders GROUP BY {bt(FN['zfzt'])}")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} 单")

    cur.close()
    conn.close()

    print("\n" + "=" * 60)
    print("迁移完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()