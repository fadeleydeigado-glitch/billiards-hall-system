from decimal import Decimal


def decimal_to_json(value):
    # 把 API 返回值里的 Decimal 递归转换成字符串。
    #
    # Python 标准库 json.dumps 不认识 Decimal，直接序列化会抛 TypeError。
    # 金额用字符串输出，可以保留数据库中的精确十进制值和小数位，
    # 避免在 HTTP 边界又退回二进制浮点表示。
    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, dict):
        return {
            key: decimal_to_json(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [decimal_to_json(item) for item in value]

    return value
