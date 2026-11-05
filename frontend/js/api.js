// 这一层只负责把 FastAPI 的四键结果带回页面。
// 业务金额和业务状态都以后端返回为准。
function queryString(values) {
    return new URLSearchParams(values).toString();
}

function buildError(errorType, message, data = null) {
    return {
        status: "error",
        error_type: errorType,
        data,
        message
    };
}

export async function requestJson(url, options = {}) {
    try {
        const response = await fetch(url, options);
        const text = await response.text();
        let payload;

        try {
            payload = JSON.parse(text);
        } catch (error) {
            payload = buildError(
                "InvalidJsonResponse",
                "后端返回的内容不是合法 JSON",
                text || null
            );
        }

        if (!response.ok && payload.status !== "error") {
            return buildError(
                `Http${response.status}`,
                `请求失败（HTTP ${response.status}）`
            );
        }

        return payload;
    } catch (error) {
        return buildError("NetworkError", error.message || "网络请求失败");
    }
}

export function getRefreshData() {
    return requestJson("/frontend/refresh");
}

export function getProducts() {
    return requestJson("/frontend/products");
}

export function getUnpaidOrders() {
    return requestJson("/frontend/unpaid-orders");
}

export function getCheckoutOptions(tableId) {
    const params = queryString({ table_id: tableId });
    return requestJson(`/tables/checkout-options?${params}`);
}

export function openTable(tableId) {
    const params = queryString({ table_id: tableId });
    return requestJson(`/tables/open?${params}`, { method: "POST" });
}

export function closeTable(tableId) {
    const params = queryString({ table_id: tableId });
    return requestJson(`/tables/close?${params}`, { method: "POST" });
}

export function addProduct(tableId, productId, productCount) {
    const params = queryString({
        table_id: tableId,
        product_id: productId,
        product_count: productCount
    });
    return requestJson(`/products/consumption?${params}`, { method: "POST" });
}

export function createUnpaidOrder(tableId, consumptionIds) {
    return requestJson("/orders/unpaid", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            "桌子号码": tableId,
            "消费记录编号列表": consumptionIds
        })
    });
}

export function confirmPayment(orderId) {
    return requestJson("/payments/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            "订单编号": orderId
        })
    });
}
