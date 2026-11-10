# -*- coding: utf-8 -*-
"""HTTP 请求模型：只检查请求形状和字段边界，不检查业务规则。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .id_utils import MAX_BUSINESS_ID_LENGTH, MAX_TABLE_ID_LENGTH


# products.id 在数据库里是 VARCHAR(10)，与桌号分开命名，
# 避免以后其中一方变长时把两个限制混在一起。
MAX_PRODUCT_ID_LENGTH = 10


TableId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_TABLE_ID_LENGTH,
    ),
]

ProductId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PRODUCT_ID_LENGTH,
    ),
]

# 业务编号既可能是新的毫秒格式，也可能是旧编号；
# 这里只限制“非空字符串 + 数据库列宽”，不解析具体格式。
BusinessId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_BUSINESS_ID_LENGTH,
    ),
]


class _StrictRequestModel(BaseModel):
    # 中文键名继续兼容现有前端；英文键名也接受，方便以后 AI 或新客户端。
    # extra="forbid" 让字段拼错或多传字段直接失败，而不是静默忽略。
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )


class CreateUnpaidOrderRequest(_StrictRequestModel):
    """POST /orders/unpaid 的请求体。"""

    table_id: TableId = Field(alias="桌子号码")
    consumption_ids: list[BusinessId] = Field(
        alias="消费记录编号列表",
        min_length=1,
    )


class ConfirmPaymentRequest(_StrictRequestModel):
    """POST /payments/confirm 的请求体。"""

    order_id: BusinessId = Field(alias="订单编号")
