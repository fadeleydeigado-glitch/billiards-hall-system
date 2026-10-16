# -*- coding: utf-8 -*-
"""
test_full_flow.py

（由 test_open_close.py 升级改名而来，旧文件已删除，不再保留。）

作用：五个写流程的完整测试 + 边界场景测试 + 事务回滚测试。

五个写流程（都在 controller_layer.py 里）：
    开台    three_open_table(table_id)
    关台    three_close_table(table_id)
    加商品  three_add_product_consumption(table_id, product_id, product_count)
    建订单  three_create_unpaid_order(selected_data)
    收款    three_confirm_payment(payment_data)

结构：
    第零步  ：打印桌号 / 商品编号 / 商品列表；预检 1 号桌空闲（不空闲就停下，不强行开台）。
    第一部分：正常流程（一条完整用户故事），6 个检查点；每步同时检查返回值与数据库状态。
    第二部分：边界场景（故意乱来），10 个检查点；要求全部被拦截（status='error'）。
    第三部分：事务回滚，1 个检查点。

做法：
    1. 用 pymysql 直连数据库（连接参数从 io_layer.DB_CONFIG 读取，不重复写密码）。
    2. 每一步自动断言：通过打 ✅（只一行），失败打 ❌ 并打印详细原因。
    3. 测试数据保留，不清理（测试库）。
    4. 不使用 pytest / unittest，只用 print + if。
    5. 不修改任何其他现有代码文件。

事务回滚测试的构造方式（满足三条约定）：
    1. 函数替换只发生在回滚测试函数 run_rollback_test() 内部；
    2. 测试结束（无论成败）用 finally 立即还原原函数；
    3. 还原后校验函数对象与原对象、与 io_layer 里的原函数完全一致。
    替换只发生在内存里，不修改磁盘上的任何文件。
"""

import sys
import os

# 保证无论从哪里启动，都能导入项目根目录下的模块。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 让中文输出在 Windows 控制台里正常显示。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pymysql

# 数据库连接参数统一从 io_layer 读取，避免重复写用户名 / 密码。
from io_layer import DB_CONFIG
import io_layer
import controller_layer
from controller_layer import (
    three_open_table,
    three_close_table,
    three_add_product_consumption,
    three_create_unpaid_order,
    three_confirm_payment,
)


# ==================== 测试参数（经用户确认） ====================

TABLE_ID = "1"          # 测试桌号：1 号桌（预检通过才会继续）
PRODUCT_ID = "P_001"    # 测试商品：可乐
ADD_COUNT = 2           # 加 2 个


# ==================== 检查点收集与打印 ====================

checks = []     # [(编号, 名称, 是否通过, 失败详情)]
skipped = []    # 跳过的检查点编号


def describe(result):
    # 把流程返回压成一行，用于失败详情。
    if not isinstance(result, dict):
        return f"返回值不是字典：{result!r}"
    return (
        f"status={result.get('status')}, error_type={result.get('error_type')}, "
        f"message={result.get('message')}"
    )


def summarise(conds):
    # conds: [(描述, 是否通过, 实际说明)] → (是否全部通过, 失败详情文本)
    fails = []
    for desc, ok, actual in conds:
        if not ok:
            fails.append(f"{desc}；实际：{actual}")
    return (not fails, "；".join(fails))


def add_check(code, name, passed, detail="", pass_suffix=""):
    # 记录一个检查点并立即打印：通过只打一行；失败补打详细原因。
    checks.append((code, name, bool(passed), detail))
    if passed:
        print(f"✅ [{code}] {name}{pass_suffix}")
    else:
        print(f"❌ [{code}] {name}")
        if detail:
            print(f"    失败详情：{detail}")


def add_error_check(code, name, result, expect_type=None):
    # 边界检查点专用：唯一判定标准 = 返回 status 是否为 'error'。
    ok = (result.get("status") == "error")
    error_type = result.get("error_type")
    suffix = ""
    detail = ""
    if ok:
        suffix = f" → 被拦截（error_type={error_type}）"
        if expect_type and error_type != expect_type:
            suffix += f"；注意：期望 {expect_type}"
    else:
        detail = describe(result)
    add_check(code, name, ok, detail, suffix)


# ==================== 数据库小工具 ====================

def db_query(sql, params=None):
    # 用 pymysql 执行一次查询，返回所有行（列表）。
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        connection.close()


def db_query_one(sql, params=None):
    # 只取一行，没有则返回 None。
    rows = db_query(sql, params)
    return rows[0] if rows else None


# ============================================================
print("=" * 72)
print("test_full_flow.py —— 五个写流程完整测试")
print("（开台 / 关台 / 加商品 / 创建订单 / 确认收款）")
print("=" * 72)


# ============================================================
print()
print("【第零步：准备查询与空闲预检】")

print(f"  测试桌号：{TABLE_ID}")

# 预检：1 号桌必须没有"未关台的台位费"，否则停下，不强行开台。
unclosed_count = db_query_one(
    "SELECT COUNT(*) FROM ongoing_table_fees "
    "WHERE `桌台编号` = %s AND `关台时间` IS NULL",
    (TABLE_ID,),
)[0]

if unclosed_count > 0:
    print(f"❌ {TABLE_ID} 号桌当前不空闲（存在 {unclosed_count} 条未关台台位费），"
          f"脚本停止，不执行任何写操作。")
    sys.exit(1)

print(f"  ✅ 空闲预检通过：{TABLE_ID} 号桌没有未关台的台位费，可以开台")

# 1 号桌历史遗留的"已关台、待结账"台位费：按约定结账时一并选中。
leftover_fees = db_query(
    "SELECT consumption_id, `金额` FROM ongoing_table_fees "
    "WHERE `桌台编号` = %s AND `关台时间` IS NOT NULL ORDER BY `开台时间`",
    (TABLE_ID,),
)
leftover_ids_text = "、".join(row[0] for row in leftover_fees) if leftover_fees else "无"
print(f"  备注：{TABLE_ID} 号桌另有 {len(leftover_fees)} 条历史已关台台位费"
      f"（{leftover_ids_text}），结账时将一并选中。")

# 商品列表（实时读库）。
products_rows = db_query(
    "SELECT id, `商品名称`, `单价`, `库存` FROM products ORDER BY id"
)
print(f"  测试商品编号：{PRODUCT_ID}")
print("  商品列表：")
for row in products_rows:
    print(f"    {row[0]}  {row[1]}  单价 {row[2]}  库存 {row[3]}")

product_row = db_query_one(
    "SELECT `商品名称`, `单价`, `库存` FROM products WHERE id = %s",
    (PRODUCT_ID,),
)
if product_row is None:
    print(f"❌ 商品 {PRODUCT_ID} 不存在，脚本停止。")
    sys.exit(1)

product_name = product_row[0]
product_price = float(product_row[1])


# ============================================================
print()
print("【第一部分：正常流程】")

# 先记录快照，供后面的对比使用。
stock_before = db_query_one(
    "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
)[0]
paid_before = db_query_one(
    "SELECT COUNT(*) FROM orders WHERE `支付状态` = %s", ("已支付",)
)[0]

# ---------- 1-1 开台 ----------
open_result = three_open_table(TABLE_ID)
open_data = open_result.get("data") or {}
fee_id = open_data.get("消费记录编号")

conds = [
    ("three_open_table 返回 status=success",
     open_result.get("status") == "success", describe(open_result)),
    ("返回数据里带消费记录编号", bool(fee_id), f"消费记录编号={fee_id}"),
]

if fee_id:
    fee_row = db_query_one(
        "SELECT `桌台编号`, `开台时间`, `关台时间`, `每小时价格`, `金额` "
        "FROM ongoing_table_fees WHERE consumption_id = %s",
        (fee_id,),
    )
    main_row = db_query_one(
        "SELECT `桌台编号`, type_code FROM ongoing_consumptions WHERE id = %s",
        (fee_id,),
    )
    table_price_row = db_query_one(
        "SELECT `每小时价格` FROM billiards_tables WHERE id = %s", (TABLE_ID,)
    )
    table_price = table_price_row[0] if table_price_row else None
else:
    fee_row = None
    main_row = None
    table_price = None

conds.append(("ongoing_table_fees 新增记录存在", fee_row is not None, f"行={fee_row}"))
if fee_row is not None:
    conds.append((f"新增台位费 桌台编号={TABLE_ID}",
                  str(fee_row[0]) == TABLE_ID, f"实际={fee_row[0]}"))
    conds.append(("新增台位费 关台时间为空", fee_row[2] is None, f"关台时间={fee_row[2]}"))
    conds.append(("新增台位费 金额为空", fee_row[4] is None, f"金额={fee_row[4]}"))
    conds.append(
        ("新增台位费 每小时价格与桌台配置一致",
         table_price is not None and float(fee_row[3]) == float(table_price),
         f"明细={fee_row[3]}, 配置={table_price}")
    )
conds.append(("ongoing_consumptions 新增记录且类型为 T",
              main_row is not None and main_row[1] == "T", f"主表行={main_row}"))

ok, detail = summarise(conds)
add_check("1-1", "给 1 号桌开台：流程成功，且新增 1 条未关台台位费", ok, detail)


# ---------- 1-2 加商品 ----------
add_result = three_add_product_consumption(TABLE_ID, PRODUCT_ID, ADD_COUNT)
add_data = add_result.get("data") or {}
product_consumption_id = add_data.get("消费记录编号")

conds = [
    ("three_add_product_consumption 返回 status=success",
     add_result.get("status") == "success", describe(add_result)),
    ("返回数据里带消费记录编号", bool(product_consumption_id),
     f"消费记录编号={product_consumption_id}"),
]

if product_consumption_id:
    opc_row = db_query_one(
        "SELECT `桌台编号`, `商品编号`, `商品名称`, `单价`, `数量`, `金额` "
        "FROM ongoing_product_consumptions WHERE consumption_id = %s",
        (product_consumption_id,),
    )
    opc_main_row = db_query_one(
        "SELECT `桌台编号`, type_code FROM ongoing_consumptions WHERE id = %s",
        (product_consumption_id,),
    )
else:
    opc_row = None
    opc_main_row = None

conds.append(("ongoing_product_consumptions 新增记录存在", opc_row is not None, f"行={opc_row}"))
if opc_row is not None:
    conds.append((f"明细 商品编号={PRODUCT_ID}", opc_row[1] == PRODUCT_ID, f"实际={opc_row[1]}"))
    conds.append((f"明细 商品名称={product_name}", opc_row[2] == product_name, f"实际={opc_row[2]}"))
    conds.append((f"明细 数量={ADD_COUNT}", opc_row[4] == ADD_COUNT, f"实际={opc_row[4]}"))
    conds.append(
        (f"明细 金额=单价×数量={product_price * ADD_COUNT:.2f}",
         float(opc_row[5]) == product_price * ADD_COUNT, f"实际={opc_row[5]}")
    )
conds.append(("ongoing_consumptions 新增记录且类型为 S",
              opc_main_row is not None and opc_main_row[1] == "S", f"主表行={opc_main_row}"))

stock_after_add = db_query_one(
    "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
)[0]
conds.append((f"库存 {stock_before} → {stock_before - ADD_COUNT}",
              stock_after_add == stock_before - ADD_COUNT, f"实际={stock_after_add}"))

ok, detail = summarise(conds)
add_check("1-2", f"给 1 号桌加 {ADD_COUNT} 个 {PRODUCT_ID}（{product_name}）："
                 f"流程成功、消费写入、库存扣减 {ADD_COUNT}", ok, detail)


# ---------- 1-3 关台 ----------
close_result = three_close_table(TABLE_ID)
close_data = close_result.get("data") or {}
closed_fee = close_data.get("台位消费") or {}
closed_fee_id = closed_fee.get("消费记录编号")

conds = [
    ("three_close_table 返回 status=success",
     close_result.get("status") == "success", describe(close_result)),
]
if fee_id:
    conds.append(("关的是本次开台生成的消费记录", closed_fee_id == fee_id,
                  f"关台记录={closed_fee_id}, 开台记录={fee_id}"))
    fee_row = db_query_one(
        "SELECT `开台时间`, `关台时间`, `每小时价格`, `金额` "
        "FROM ongoing_table_fees WHERE consumption_id = %s",
        (fee_id,),
    )
    if fee_row is None:
        conds.append(("关台后台位费记录存在", False, "查不到该记录"))
    else:
        start_time, close_time, hour_price, amount = fee_row
        conds.append(("关台后 关台时间已写入", close_time is not None, f"关台时间={close_time}"))
        conds.append(("关台后 金额已写入", amount is not None, f"金额={amount}"))
        if close_time is not None and amount is not None:
            expected_amount = int((close_time - start_time) / 3600 * float(hour_price))
            conds.append(("金额与 (关台-开台)/3600×每小时价格 取整一致",
                          int(amount) == expected_amount,
                          f"实际={amount}, 计算={expected_amount}"))
        else:
            conds.append(("金额与时长公式一致", False, "关台时间或金额缺失"))
    backup_row = db_query_one(
        "SELECT consumption_id, `开台时间`, `关台时间` FROM core_backup "
        "WHERE consumption_id = %s",
        (fee_id,),
    )
    conds.append(("core_backup 已生成该消费记录备份", backup_row is not None, f"备份行={backup_row}"))
else:
    conds.append(("关台目标确定", False, "开台步骤没有拿到消费记录编号"))

ok, detail = summarise(conds)
add_check("1-3", "关 1 号桌：流程成功，关台时间与金额已写入，核心备份已生成", ok, detail)


# ---------- 1-4 创建未支付订单 ----------
closed_fees = db_query(
    "SELECT consumption_id, `金额` FROM ongoing_table_fees "
    "WHERE `桌台编号` = %s AND `关台时间` IS NOT NULL ORDER BY `开台时间`",
    (TABLE_ID,),
)
selected_ids = [row[0] for row in closed_fees]
if product_consumption_id:
    selected_ids.append(product_consumption_id)

if not closed_fees or not product_consumption_id:
    add_check("1-4", "创建未支付订单", False,
              "前提不足（没有已关台台费或没有商品消费），未执行创建，避免误动数据")
    order_id = None
else:
    product_amount = product_price * ADD_COUNT
    expected_total = sum(float(row[1]) for row in closed_fees) + product_amount

    order_result = three_create_unpaid_order({
        "桌子号码": TABLE_ID,
        "消费记录编号列表": selected_ids,
    })
    order_data = order_result.get("data") or {}
    order_id = order_data.get("订单编号")
    selected_back = order_data.get("已选消费") or []

    conds = [
        ("three_create_unpaid_order 返回 status=success",
         order_result.get("status") == "success", describe(order_result)),
        ("返回数据里带订单编号", bool(order_id), f"订单编号={order_id}"),
        (f"共选中 {len(selected_ids)} 条消费（台费 {len(closed_fees)} 条 + 商品 1 条）",
         len(selected_back) == len(selected_ids), f"返回已选消费数={len(selected_back)}"),
    ]
    order_row = db_query_one(
        "SELECT `支付状态`, `总金额` FROM orders WHERE order_id = %s", (order_id,)
    ) if order_id else None
    if order_row is None:
        conds.append(("orders 新增订单主记录", False, "按订单编号查不到"))
    else:
        conds.append(("orders 新增订单主记录（未支付）",
                      order_row[0] == "未支付", f"支付状态={order_row[0]}"))
        conds.append((f"订单总金额={expected_total:.2f}",
                      float(order_row[1]) == float(expected_total),
                      f"实际={order_row[1]}"))

    ok, detail = summarise(conds)
    add_check("1-4", "创建未支付订单：流程成功（1 号桌已关台台费与商品一起选中）", ok, detail)


# ---------- 1-5 确认收款 ----------
if not order_id:
    add_check("1-5", "确认收款", False, "上一步没有生成订单编号，无法确认收款")
else:
    pay_result = three_confirm_payment({"订单编号": order_id})
    conds = [
        ("three_confirm_payment 返回 status=success",
         pay_result.get("status") == "success", describe(pay_result)),
    ]
    pay_row = db_query_one(
        "SELECT `支付状态`, `支付时间` FROM orders WHERE order_id = %s", (order_id,)
    )
    if pay_row is None:
        conds.append(("订单记录存在", False, "查不到"))
    else:
        conds.append(("订单支付状态=已支付", pay_row[0] == "已支付", f"实际={pay_row[0]}"))
        conds.append(("支付时间已写入", pay_row[1] is not None, f"支付时间={pay_row[1]}"))
    ok, detail = summarise(conds)
    add_check("1-5", "确认收款：流程成功，订单转为已支付", ok, detail)


# ---------- 1-6 最终检查 ----------
conds = []
for table_name in ("ongoing_consumptions", "ongoing_table_fees", "ongoing_product_consumptions"):
    count_now = db_query_one(
        f"SELECT COUNT(*) FROM {table_name} WHERE `桌台编号` = %s", (TABLE_ID,)
    )[0]
    conds.append((f"{table_name} 中 1 号桌记录数=0", count_now == 0, f"实际={count_now}"))

paid_after = db_query_one(
    "SELECT COUNT(*) FROM orders WHERE `支付状态` = %s", ("已支付",)
)[0]
conds.append((f"已支付订单数 {paid_before} → {paid_before + 1}",
              paid_after == paid_before + 1, f"实际={paid_after}"))

final_order_row = db_query_one(
    "SELECT `支付状态` FROM orders WHERE order_id = %s", (order_id,)
) if order_id else None
conds.append(("新订单已支付",
              final_order_row is not None and final_order_row[0] == "已支付",
              f"实际={final_order_row}"))

oc_count = db_query_one(
    "SELECT COUNT(*) FROM order_consumptions WHERE order_id = %s", (order_id,)
)[0] if order_id else 0
otf_count = db_query_one(
    "SELECT COUNT(*) FROM order_table_fees WHERE order_id = %s", (order_id,)
)[0] if order_id else 0
opc_count = db_query_one(
    "SELECT COUNT(*) FROM order_product_consumptions WHERE order_id = %s", (order_id,)
)[0] if order_id else 0
conds.append((f"order_consumptions 明细 {len(selected_ids)} 条",
              oc_count == len(selected_ids), f"实际={oc_count}"))
conds.append((f"order_table_fees 明细 {len(closed_fees)} 条",
              otf_count == len(closed_fees), f"实际={otf_count}"))
conds.append(("order_product_consumptions 明细 1 条", opc_count == 1, f"实际={opc_count}"))

stock_final = db_query_one(
    "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
)[0]
conds.append((f"库存 {stock_before} → {stock_before - ADD_COUNT}",
              stock_final == stock_before - ADD_COUNT, f"实际={stock_final}"))

ok, detail = summarise(conds)
add_check("1-6", "最终检查：1 号桌待结算清空、订单与明细齐全、库存已减 2", ok, detail)


# ============================================================
print()
print("【第二部分：边界场景】")

# 2-1：对已经开着的桌再开一次（先用真流程把 1 号桌开起来，制造"已开台"状态）。
prep_result = three_open_table(TABLE_ID)
second_open_result = three_open_table(TABLE_ID)
if prep_result.get("status") == "success":
    add_error_check("2-1", "对已开台桌重复开台", second_open_result, expect_type="TableOccupied")
else:
    add_check("2-1", "对已开台桌重复开台", False,
              f"准备开台失败，无法构造已开台状态：{describe(prep_result)}")

# 2-2：开一个不存在的桌。
add_error_check("2-2", "开不存在的桌（999）", three_open_table("999"))

# 2-3：加一个不存在的商品。
add_error_check("2-3", "加不存在的商品（P_999）",
                three_add_product_consumption(TABLE_ID, "P_999", 1))

# 2-4：加超过库存的商品数量。
stock_now = db_query_one(
    "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
)[0]
add_error_check("2-4", f"加超过库存的数量（{stock_now + 1} > {stock_now}）",
                three_add_product_consumption(TABLE_ID, PRODUCT_ID, stock_now + 1))

# 2-5：关一个没开的桌。
add_error_check("2-5", "关未开台的 2 号桌", three_close_table("2"))

# 2-6：用不存在的订单号确认收款。
add_error_check("2-6", "不存在的订单号确认收款",
                three_confirm_payment({"订单编号": "999_0000000000"}))

# 2-7：对已经支付过的订单再确认收款。
if order_id:
    add_error_check("2-7", "对已支付订单再次确认收款",
                    three_confirm_payment({"订单编号": order_id}))
else:
    add_check("2-7", "对已支付订单再次确认收款", False,
              "正常流程没有生成订单，无法测试")

# 2-8：创建订单时传一个不存在的消费编号。
add_error_check("2-8", "创建订单传不存在的消费编号",
                three_create_unpaid_order({
                    "桌子号码": TABLE_ID,
                    "消费记录编号列表": ["T_1_9999999999"],
                }))

# 2-9：创建订单时传空消费编号列表。
add_error_check("2-9", "创建订单传空消费编号列表",
                three_create_unpaid_order({
                    "桌子号码": TABLE_ID,
                    "消费记录编号列表": [],
                }))

# 2-10：确认收款时传空字典 {}。
add_error_check("2-10", "确认收款传空字典 {}", three_confirm_payment({}))


# ============================================================
print()
print("【第三部分：事务回滚】")


def run_rollback_test():
    # 事务回滚测试：
    # 构造"商品消费插入成功、但扣库存失败"的场景，验证商品消费有没有被回滚。
    #
    # 三条约定（全部满足）：
    # 1. 函数替换只发生在本函数内部；
    # 2. 无论成败，用 finally 在测试结束（返回前）立刻还原；
    # 3. 还原后校验函数对象与原对象完全一致，不影响后面的任何测试
    #    （本测试位于整个脚本的最后，后面已无测试，仍然照约校验）。
    #
    # 只做内存替换，不修改磁盘上的任何文件。

    code = "3-1"
    name = "事务回滚：扣库存失败时，商品消费被回滚（临时替换函数构造）"
    rollback_table = "2"   # 用干净的 2 号桌做构造，不占用 1 号桌的上下文

    original_deduct = getattr(controller_layer, "one_deduct_product_stock_in_mysql", None)
    if not callable(original_deduct):
        skipped.append(code)
        print(f"（跳过）[{code}] {name} —— 找不到可替换的扣库存函数，无法构造场景")
        return

    def fake_deduct(cursor, product_id, quantity):
        # 模拟扣库存失败：不执行任何真实 SQL，直接返回错误结果。
        return {
            "status": "error",
            "error_type": "SimulatedStockDeductFailure",
            "data": None,
            "message": "模拟扣库存失败（测试构造，未执行真实扣减）",
        }

    # 替换前记录基线。
    main_before = db_query_one(
        "SELECT COUNT(*) FROM ongoing_consumptions WHERE `桌台编号` = %s",
        (rollback_table,),
    )[0]
    opc_before = db_query_one(
        "SELECT COUNT(*) FROM ongoing_product_consumptions WHERE `桌台编号` = %s",
        (rollback_table,),
    )[0]
    stock_before_rollback = db_query_one(
        "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
    )[0]

    result = None
    try:
        # —— 替换开始（只在本函数内部，只作用于 controller_layer 模块对象）——
        controller_layer.one_deduct_product_stock_in_mysql = fake_deduct
        # 调用真实流程：商品消费插入会真实执行，扣库存走到模拟函数后失败。
        result = three_add_product_consumption(rollback_table, PRODUCT_ID, 1)
    except Exception as error:
        result = {
            "status": "exception",
            "error_type": type(error).__name__,
            "data": None,
            "message": f"流程抛出异常：{error}",
        }
    finally:
        # —— 立刻还原（无论成败）——
        controller_layer.one_deduct_product_stock_in_mysql = original_deduct

    # 还原校验：必须是原来的函数对象（同时与 io_layer 里的原函数一致）。
    restored_ok = (
        controller_layer.one_deduct_product_stock_in_mysql is original_deduct
        and controller_layer.one_deduct_product_stock_in_mysql is io_layer.one_deduct_product_stock_in_mysql
    )

    main_after = db_query_one(
        "SELECT COUNT(*) FROM ongoing_consumptions WHERE `桌台编号` = %s",
        (rollback_table,),
    )[0]
    opc_after = db_query_one(
        "SELECT COUNT(*) FROM ongoing_product_consumptions WHERE `桌台编号` = %s",
        (rollback_table,),
    )[0]
    stock_after_rollback = db_query_one(
        "SELECT `库存` FROM products WHERE id = %s", (PRODUCT_ID,)
    )[0]

    conds = [
        ("流程在扣库存步骤失败（error_type=SimulatedStockDeductFailure）",
         result is not None and result.get("error_type") == "SimulatedStockDeductFailure",
         describe(result)),
        ("流程返回 status=error",
         result is not None and result.get("status") == "error",
         describe(result)),
        (f"ongoing_consumptions（2 号桌）无残留：{main_before} → {main_after}",
         main_after == main_before, f"实际={main_after}"),
        (f"ongoing_product_consumptions（2 号桌）无残留：{opc_before} → {opc_after}",
         opc_after == opc_before, f"实际={opc_after}"),
        (f"商品库存未被扣减：{stock_before_rollback} → {stock_after_rollback}",
         stock_after_rollback == stock_before_rollback, f"实际={stock_after_rollback}"),
        ("替换函数已还原为原函数对象", restored_ok, "还原校验未通过"),
    ]

    ok, detail = summarise(conds)
    checks.append((code, name, ok, detail))
    if ok:
        print(f"✅ [{code}] {name} → 商品消费已回滚，替换函数已还原")
    else:
        print(f"❌ [{code}] {name}")
        if detail:
            print(f"    失败详情：{detail}")


run_rollback_test()


# ============================================================
print()
print("=" * 72)
passed_count = sum(1 for _, _, ok, _ in checks if ok)
failed_items = [item for item in checks if not item[2]]
summary_line = f"总结：{passed_count} 项通过 / {len(failed_items)} 项失败"
if skipped:
    summary_line += f"，{len(skipped)} 项跳过"
print(summary_line)
if failed_items:
    print("失败检查点：" + "、".join(f"[{item[0]}] {item[1]}" for item in failed_items))
print("=" * 72)

sys.exit(0 if not failed_items else 1)