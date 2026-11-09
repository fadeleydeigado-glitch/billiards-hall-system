#!/usr/bin/env python3
import mysql.connector, os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
# 为了防止密码泄露：请在本地系统环境变量中设置 MYSQL_PASSWORD（或在本地 .env 中配置），切勿在代码中硬编码密码。
# 本工具读取独立备份库，默认 billiards_backup；可用 MYSQL_BACKUP_* 环境变量覆盖。
C = {
    "host": os.environ.get("MYSQL_BACKUP_HOST", os.environ.get("MYSQL_HOST", "localhost")),
    "port": int(os.environ.get("MYSQL_BACKUP_PORT", os.environ.get("MYSQL_PORT", "3306"))),
    "user": os.environ.get("MYSQL_BACKUP_USER", os.environ.get("MYSQL_USER", "root")),
    "password": os.environ.get("MYSQL_BACKUP_PASSWORD", os.environ.get("MYSQL_PASSWORD", "")),
    "database": os.environ.get("MYSQL_BACKUP_DATABASE", "billiards_backup"),
    "charset": "utf8mb4",
}
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

cur.execute("SELECT * FROM " + q("core_backup_products"))
product_rows = cur.fetchall()
print("=" * 50)
print("  [core_backup_products] Product Safety Log (" + str(len(product_rows)) + " records)")
print("=" * 50)
for cid, tid, pid, pname, price, qty, amount, created in product_rows:
    print("  " + str(cid) + "  Table:" + str(tid) + "  Product:" + str(pid) + " " + str(pname) +
          "  price:" + str(price) + "  qty:" + str(qty) + "  amount:" + str(amount) +
          "  created:" + str(created))
cur.close(); conn.close()
print()
os.system("pause")
