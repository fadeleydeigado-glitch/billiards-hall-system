"""业务编号的生成与解析。

新编号格式：
- 台位费：T_<桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>
- 商品：  S_<桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>
- 订单：  <桌号>_t<毫秒时间戳的36进制>_<6位36进制随机>

旧编号格式继续兼容：
- T_<桌号>_<秒级时间戳>
- S_<桌号>_<秒级时间戳>
- <桌号>_<秒级时间戳>

毫秒时间戳用36进制编码，是为了在现有 VARCHAR(30) 内同时容纳
“毫秒精度 + 随机后缀”，避免为了编号去改多张带外键的表。
"""

import secrets


BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
RANDOM_PART_LENGTH = 6
MAX_BUSINESS_ID_LENGTH = 30
MAX_TABLE_ID_LENGTH = 10
KNOWN_PREFIXES = frozenset({"T", "S"})


def encode_base36(value: int) -> str:
    """把非负整数编码成小写36进制字符串。"""
    if type(value) is not int or value < 0:
        raise ValueError("value must be a non-negative integer")
    if value == 0:
        return "0"

    chars = []
    while value:
        value, remainder = divmod(value, 36)
        chars.append(BASE36_ALPHABET[remainder])
    return "".join(reversed(chars))


def decode_base36(text: str) -> int:
    """把小写36进制字符串解码成整数。"""
    if not isinstance(text, str) or not text:
        raise ValueError("base36 text must be a non-empty string")

    value = 0
    for char in text.lower():
        index = BASE36_ALPHABET.find(char)
        if index < 0:
            raise ValueError(f"invalid base36 character: {char!r}")
        value = value * 36 + index
    return value


def generate_random_part() -> str:
    """生成固定长度的随机后缀，供编号和测试注入使用。"""
    return "".join(
        secrets.choice(BASE36_ALPHABET)
        for _ in range(RANDOM_PART_LENGTH)
    )


def resolve_id_timestamp_ms(
    current_time: int,
    current_time_ms: int | None = None
) -> int:
    """统一决定编号使用哪个毫秒时间。

    正常情况下控制层传入毫秒时间；旧测试或旧调用只传秒级时间时，
    这里统一乘以 1000，避免三个业务流程各写一遍兼容逻辑。
    """
    if current_time_ms is None:
        if type(current_time) is not int or current_time <= 0:
            raise ValueError("current_time must be a positive integer")
        current_time_ms = current_time * 1000

    if type(current_time_ms) is not int or current_time_ms <= 0:
        raise ValueError("current_time_ms must be a positive integer")
    return current_time_ms


def build_business_id(
    prefix: str,
    table_id: str,
    timestamp_ms: int,
    random_part: str | None = None
) -> str:
    """生成统一格式的业务编号。

    prefix 为空表示订单编号；T/S 分别表示台位费和商品消费。
    random_part 只在测试或需要固定结果时传入，正常调用自动生成。
    """
    if not isinstance(table_id, str) or not table_id.strip():
        raise ValueError("table_id must be a non-empty string")
    if len(table_id.strip()) > MAX_TABLE_ID_LENGTH:
        raise ValueError(
            f"table_id exceeds {MAX_TABLE_ID_LENGTH} characters"
        )
    if type(timestamp_ms) is not int or timestamp_ms <= 0:
        raise ValueError("timestamp_ms must be a positive integer")

    if random_part is None:
        random_part = generate_random_part()
    if not isinstance(random_part, str) or len(random_part) != RANDOM_PART_LENGTH:
        raise ValueError(
            f"random_part must be {RANDOM_PART_LENGTH} characters"
        )
    random_part = random_part.lower()
    if any(char not in BASE36_ALPHABET for char in random_part):
        raise ValueError("random_part must be base36")

    parts = []
    if prefix:
        normalized_prefix = str(prefix).strip("_").upper()
        if normalized_prefix:
            if normalized_prefix not in KNOWN_PREFIXES:
                raise ValueError("prefix must be T, S, or empty")
            parts.append(normalized_prefix)
    parts.extend([
        table_id.strip(),
        f"t{encode_base36(timestamp_ms)}",
        random_part,
    ])
    business_id = "_".join(parts)
    if len(business_id) > MAX_BUSINESS_ID_LENGTH:
        raise ValueError(
            f"business id exceeds {MAX_BUSINESS_ID_LENGTH} characters"
        )
    return business_id


def parse_business_id(value: str) -> dict | None:
    """解析新旧业务编号，成功返回字段字典，失败返回 None。"""
    if not isinstance(value, str) or not value:
        return None

    parts = value.split("_")
    if len(parts) < 2:
        return None

    prefix = ""
    if parts[0] in KNOWN_PREFIXES:
        prefix = parts.pop(0)
    if len(parts) < 2:
        return None

    # 新格式：最后两段分别是 t<36进制毫秒> 和随机后缀。
    if len(parts) >= 3 and len(parts[-2]) > 1 and parts[-2].startswith("t"):
        timestamp_text = parts[-2][1:]
        random_part = parts[-1].lower()
        timestamp_ms = None
        if (
            timestamp_text
            and len(random_part) == RANDOM_PART_LENGTH
            and all(char in BASE36_ALPHABET for char in random_part)
        ):
            try:
                timestamp_ms = decode_base36(timestamp_text)
            except ValueError:
                timestamp_ms = None
        if timestamp_ms is not None:
            table_id = "_".join(parts[:-2])
            if not table_id:
                return None
            return {
                "prefix": prefix,
                "table_id": table_id,
                "timestamp_ms": timestamp_ms,
                "random_part": random_part,
                "format": "millisecond_base36",
            }

    # 旧格式：最后一段是秒级十进制时间戳。
    if parts[-1].isdigit():
        table_id = "_".join(parts[:-1])
        if not table_id:
            return None
        return {
            "prefix": prefix,
            "table_id": table_id,
            "timestamp_ms": int(parts[-1]) * 1000,
            "random_part": None,
            "format": "legacy_seconds",
        }

    return None


def parse_business_id_time(value: str) -> int | None:
    """返回编号对应的 Unix 秒，兼容新旧格式；解析失败返回 None。"""
    parsed = parse_business_id(value)
    if parsed is None:
        return None
    return parsed["timestamp_ms"] // 1000
