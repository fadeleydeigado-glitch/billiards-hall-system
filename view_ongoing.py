#!/usr/bin/env python3
import mysql.connector, os
# 为了防止密码泄露：请在本地系统环境变量中设置 MYSQL_PASSWORD（或在本地 .env 中配置），切勿在代码中硬编码密码。
C = {"host":"localhost","port":3306,"user":"root","password":os.environ.get("MYSQL_PASSWORD", ""),"database":"billiards_hall","charset":"utf8mb4"}
def q(c): return chr(96) + c + chr(96)
conn = mysql.connector.connect(**C)
cur = conn.cursor()
print("=" * 50)
print("  [ongoing_consumptions] Pending")
print("=" * 50)
cur.execute("SELECT COUNT(*) FROM " + q("ongoing_consumptions"))
total = cur.fetchone()[0]
if total == 0:
    print("  (empty - no pending consumption)")
else:
    cur.execute("SELECT id, " + q("\u684c\u53f0\u7f16\u53f7") + ", type_code FROM " + q("ongoing_consumptions"))
    items = cur.fetchall()
    for cid, tid, ct in items:
        if ct == "T":
            cur.execute("SELECT " + q("\u5f00\u53f0\u65f6\u95f4") + ", " + q("\u5173\u53f0\u65f6\u95f4") + ", " + q("\u91d1\u989d") + " FROM " + q("ongoing_table_fees") + " WHERE consumption_id=%s", (cid,))
            r2 = cur.fetchone()
            if r2: print("  [ongoing_table_fees] " + str(cid) + " Table:" + str(tid) + " " + str(r2[0]) + " -> " + str(r2[1]) + " fee:" + str(r2[2]))
        elif ct == "S":
            cur.execute("SELECT " + q("\u5546\u54c1\u540d\u79f0") + ", " + q("\u6570\u91cf") + ", " + q("\u91d1\u989d") + " FROM " + q("ongoing_product_consumptions") + " WHERE consumption_id=%s", (cid,))
            r2 = cur.fetchone()
            if r2: print("  [ongoing_product_consumptions] " + str(cid) + " Table:" + str(tid) + " " + str(r2[0]) + " x" + str(r2[1]) + " " + str(r2[2]))
cur.close(); conn.close()
print()
os.system("pause")