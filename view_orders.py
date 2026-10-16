#!/usr/bin/env python3
import mysql.connector, os
# 为了防止密码泄露：请在本地系统环境变量中设置 MYSQL_PASSWORD（或在本地 .env 中配置），切勿在代码中硬编码密码。
C = {"host":"localhost","port":3306,"user":"root","password":os.environ.get("MYSQL_PASSWORD", ""),"database":"billiards_hall","charset":"utf8mb4"}
def q(c): return chr(96) + c + chr(96)
conn = mysql.connector.connect(**C)
cur = conn.cursor()

# summary
cur.execute("SELECT " + q("\u652f\u4ed8\u72b6\u6001") + ", COUNT(*) FROM " + q("orders") + " GROUP BY " + q("\u652f\u4ed8\u72b6\u6001"))
stats = cur.fetchall()
print("=" * 50)
print("  [orders] Summary")
print("=" * 50)
for r in stats: print("  " + str(r[0]) + ": " + str(r[1]))
print()

# all orders with details
cur.execute("SELECT order_id, " + q("\u684c\u53f0\u7f16\u53f7") + ", " + q("\u652f\u4ed8\u72b6\u6001") + ", " + q("\u603b\u91d1\u989d") + " FROM " + q("orders"))
orders = cur.fetchall()

for oid, tid, zt, zje in orders:
    print("=" * 50)
    print("  Order: " + str(oid) + "  Table: " + str(tid) + "  Status: " + str(zt) + "  Total: " + str(zje))
    print("  [order_consumptions] Details:")
    
    cur.execute("SELECT consumption_id, type_code FROM " + q("order_consumptions") + " WHERE order_id=%s", (oid,))
    items = cur.fetchall()
    
    for cid, ct in items:
        if ct == "T":
            cur.execute("SELECT " + q("\u5f00\u53f0\u65f6\u95f4") + ", " + q("\u5173\u53f0\u65f6\u95f4") + ", " + q("\u91d1\u989d") + " FROM " + q("order_table_fees") + " WHERE consumption_id=%s AND order_id=%s", (cid, oid))
            r2 = cur.fetchone()
            if r2: print("    [order_table_fees] " + str(cid) + ": " + str(r2[0]) + " -> " + str(r2[1]) + "  fee: " + str(r2[2]))
        elif ct == "S":
            cur.execute("SELECT " + q("\u5546\u54c1\u540d\u79f0") + ", " + q("\u6570\u91cf") + ", " + q("\u91d1\u989d") + " FROM " + q("order_product_consumptions") + " WHERE consumption_id=%s AND order_id=%s", (cid, oid))
            r2 = cur.fetchone()
            if r2: print("    [order_product_consumptions] " + str(cid) + ": " + str(r2[0]) + " x" + str(r2[1]) + "  " + str(r2[2]))
    print()

cur.close(); conn.close()
os.system("pause")