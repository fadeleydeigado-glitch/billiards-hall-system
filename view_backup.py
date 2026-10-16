#!/usr/bin/env python3
import mysql.connector, os
# 为了防止密码泄露：请在本地系统环境变量中设置 MYSQL_PASSWORD（或在本地 .env 中配置），切勿在代码中硬编码密码。
C = {"host":"localhost","port":3306,"user":"root","password":os.environ.get("MYSQL_PASSWORD", ""),"database":"billiards_hall","charset":"utf8mb4"}
def q(c): return chr(96) + c + chr(96)
conn = mysql.connector.connect(**C)
cur = conn.cursor()
cur.execute("SELECT * FROM " + q("core_backup"))
rows = cur.fetchall()
print("=" * 50)
print("  [core_backup] Safety Log (" + str(len(rows)) + " records)")
print("=" * 50)
for cid, tid, ktsj, gtsj in rows:
    print("  " + str(cid) + "  Table:" + str(tid) + "  start:" + str(ktsj) + "  end:" + str(gtsj))
cur.close(); conn.close()
print()
os.system("pause")