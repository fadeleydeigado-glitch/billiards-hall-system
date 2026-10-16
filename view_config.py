#!/usr/bin/env python3
import mysql.connector, os
# 为了防止密码泄露：请在本地系统环境变量中设置 MYSQL_PASSWORD（或在本地 .env 中配置），切勿在代码中硬编码密码。
C = {"host":"localhost","port":3306,"user":"root","password":os.environ.get("MYSQL_PASSWORD", ""),"database":"billiards_hall","charset":"utf8mb4"}
def q(c): return chr(96) + c + chr(96)
conn = mysql.connector.connect(**C)
cur = conn.cursor()
print("=" * 50)
print("  [billiards_tables] Tables")
print("=" * 50)
cur.execute("SELECT * FROM " + q("billiards_tables"))
for r in cur.fetchall(): print("  Table " + str(r[0]) + ": " + str(r[1]) + " yuan/hour")
print()
print("=" * 50)
print("  [products] Products")
print("=" * 50)
cur.execute("SELECT * FROM " + q("products"))
for r in cur.fetchall(): print("  " + str(r[0]) + " " + str(r[1]) + "  price:" + str(r[2]) + "  stock:" + str(r[3]))
cur.close(); conn.close()
print()
os.system("pause")