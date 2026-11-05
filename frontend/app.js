import * as api from "./js/api.js";
import {
    elapsedSeconds,
    formatClockTime,
    formatDateTime,
    formatDuration,
    formatMoney
} from "./js/format.js";
import { TableCard } from "./js/components/TableCard.js";
import { useTables } from "./js/useTables.js";

const { computed, onMounted, reactive, ref } = window.Vue;

function normalizeProduct(productId, productData) {
    return {
        product_id: String(productData.product_id ?? productId),
        product_name: String(productData.product_name ?? ""),
        price: Number(productData.price),
        stock: Number(productData.stock)
    };
}

const App = {
    setup() {
        const {
            tableList,
            tablesById,
            nowSeconds,
            replaceTables
        } = useTables();

        const productsById = reactive({});
        // 商品列表从字典派生，选项变化后由 Vue 自动重绘。
        const productList = computed(() => {
            const collator = new Intl.Collator("zh-CN", {
                numeric: true,
                sensitivity: "base"
            });
            return Object.values(productsById).sort((left, right) => (
                collator.compare(left.product_id, right.product_id)
            ));
        });

        const isRefreshing = ref(false);
        const notice = reactive({ text: "", tone: "info" });
        const resultText = ref("尚未调用接口。");
        const resultTone = ref("idle");
        const actionBusy = reactive({
            open: false,
            close: false,
            addProduct: false
        });

        const selectedTableId = ref(null);
        const dialogElement = ref(null);

        const openCount = computed(() => (
            tableList.value.filter((table) => table.isOpen).length
        ));
        const idleCount = computed(() => tableList.value.length - openCount.value);
        const selectedTable = computed(() => (
            selectedTableId.value === null
                ? null
                : tablesById[selectedTableId.value] || null
        ));
        const dialogTitle = computed(() => (
            selectedTable.value
                ? `${selectedTable.value.tableId}号桌`
                : "桌台操作"
        ));
        const selectedTableStatus = computed(() => {
            if (!selectedTable.value) {
                return "未选择桌台";
            }
            return selectedTable.value.isOpen ? "使用中" : "空闲";
        });

        function setNotice(text, tone = "info") {
            notice.text = text;
            notice.tone = tone;
        }

        function publishOperation(result, options = {}) {
            const payload = options.hideData
                ? {
                    status: result.status,
                    error_type: result.error_type,
                    data: options.visibleData ?? null,
                    message: result.message
                }
                : result;

            resultText.value = JSON.stringify(payload, null, 2);
            resultTone.value = result.status === "success" ? "success" : "error";
            setNotice(
                result.message || (result.status === "success" ? "操作成功" : "操作失败"),
                result.status === "success" ? "success" : "error"
            );
        }

        function setLocalError(errorType, message) {
            publishOperation({
                status: "error",
                error_type: errorType,
                data: null,
                message
            });
        }

        function replaceProducts(productPayload) {
            const nextProducts = {};

            Object.entries(productPayload || {}).forEach(([productId, productData]) => {
                nextProducts[String(productId)] = normalizeProduct(productId, productData);
            });

            Object.keys(productsById).forEach((productId) => {
                delete productsById[productId];
            });
            Object.assign(productsById, nextProducts);
        }

        async function refreshAll(options = {}) {
            const {
                announce = true,
                updateResult = announce
            } = options;

            if (isRefreshing.value) {
                return false;
            }

            isRefreshing.value = true;

            try {
                const result = await api.getRefreshData();

                if (result.status !== "success") {
                    publishOperation(result);
                    return false;
                }

                replaceTables(result.data?.["桌台基础信息"] || {});
                replaceProducts(result.data?.["商品信息"] || {});

                if (updateResult) {
                    resultText.value = JSON.stringify({
                        status: "success",
                        error_type: null,
                        data: null,
                        message: "桌台与商品信息已刷新"
                    }, null, 2);
                    resultTone.value = "success";
                }

                if (announce) {
                    setNotice("桌台与商品信息已刷新", "success");
                }

                return true;
            } finally {
                isRefreshing.value = false;
            }
        }

        async function refreshAfterMutation() {
            const refreshed = await refreshAll({
                announce: false,
                updateResult: false
            });

            if (!refreshed && notice.tone !== "error") {
                setNotice("操作已完成，但桌台数据刷新失败，请手动刷新。", "warning");
            }
        }

        async function submitOpenTable() {
            if (!selectedTableId.value || actionBusy.open) {
                return;
            }

            actionBusy.open = true;

            try {
                const result = await api.openTable(selectedTableId.value);
                publishOperation(result);

                if (result.status === "success") {
                    await refreshAfterMutation();
                }
            } finally {
                actionBusy.open = false;
            }
        }

        async function submitCloseTable() {
            if (!selectedTableId.value || actionBusy.close) {
                return;
            }

            actionBusy.close = true;

            try {
                const result = await api.closeTable(selectedTableId.value);
                publishOperation(result);

                if (result.status === "success") {
                    await refreshAfterMutation();
                    await loadCheckoutOptions(selectedTableId.value);
                }
            } finally {
                actionBusy.close = false;
            }
        }

        const productPickerOpen = ref(false);
        const productLoading = ref(false);
        const selectedProductId = ref(null);
        const productCount = ref(1);

        const selectedProduct = computed(() => (
            selectedProductId.value === null
                ? null
                : productsById[selectedProductId.value] || null
        ));

        const productSelectionLabel = computed(() => {
            const product = selectedProduct.value;
            if (!product) {
                return "请选择商品";
            }
            return `${product.product_name} · ${formatMoney(product.price)} · 库存 ${product.stock}`;
        });

        const canDecreaseProduct = computed(() => (
            Boolean(selectedProduct.value)
            && selectedProduct.value.stock > 0
            && productCount.value > 1
        ));

        const canIncreaseProduct = computed(() => (
            Boolean(selectedProduct.value)
            && selectedProduct.value.stock > 0
            && productCount.value < selectedProduct.value.stock
        ));

        function normalizeProductCount() {
            const stock = Number(selectedProduct.value?.stock ?? 1);
            const parsedCount = Number(productCount.value);

            if (!Number.isInteger(parsedCount) || stock <= 0) {
                productCount.value = 1;
                return;
            }

            productCount.value = Math.min(stock, Math.max(1, parsedCount));
        }

        function resetProductState() {
            selectedProductId.value = null;
            productCount.value = 1;
            productPickerOpen.value = false;
        }

        async function loadProducts() {
            const result = await api.getProducts();

            if (result.status !== "success") {
                publishOperation(result);
                return false;
            }

            replaceProducts(result.data || {});
            return true;
        }

        async function toggleProductPicker() {
            if (productPickerOpen.value) {
                productPickerOpen.value = false;
                return;
            }

            productLoading.value = true;

            try {
                const loaded = await loadProducts();
                if (loaded) {
                    productPickerOpen.value = true;
                }
            } finally {
                productLoading.value = false;
            }
        }

        function chooseProduct(productId) {
            const product = productsById[productId];
            if (!product || product.stock <= 0) {
                return;
            }

            selectedProductId.value = productId;
            productCount.value = 1;
            productPickerOpen.value = false;
        }

        function changeProductCount(change) {
            productCount.value = Number(productCount.value) + change;
            normalizeProductCount();
        }

        async function submitAddProduct() {
            if (!selectedTableId.value || !selectedProduct.value || actionBusy.addProduct) {
                return;
            }

            normalizeProductCount();
            actionBusy.addProduct = true;

            try {
                const result = await api.addProduct(
                    selectedTableId.value,
                    selectedProduct.value.product_id,
                    productCount.value
                );
                publishOperation(result);

                if (result.status === "success") {
                    resetProductState();
                    await refreshAfterMutation();
                }
            } finally {
                actionBusy.addProduct = false;
            }
        }

        const checkout = reactive({
            visible: false,
            loading: false,
            submitting: false,
            options: [],
            selectedIds: []
        });
        let checkoutRequestVersion = 0;

        // 这里只统计勾选条数，不算金额；正式合计由后端创建订单时计算。
        const checkoutSelectedCount = computed(() => checkout.selectedIds.length);

        function resetCheckoutState() {
            checkoutRequestVersion += 1;
            checkout.visible = false;
            checkout.loading = false;
            checkout.submitting = false;
            checkout.options = [];
            checkout.selectedIds = [];
        }

        async function loadCheckoutOptions(tableId) {
            const requestTableId = String(tableId || "");
            if (!requestTableId) {
                return false;
            }

            const requestVersion = ++checkoutRequestVersion;
            checkout.loading = true;

            try {
                const result = await api.getCheckoutOptions(requestTableId);

                if (
                    requestVersion !== checkoutRequestVersion
                    || selectedTableId.value !== requestTableId
                ) {
                    return false;
                }

                if (result.status !== "success") {
                    publishOperation(result);
                    return false;
                }

                checkout.options = result.data?.["消费明细"] || [];
                checkout.selectedIds = [];
                checkout.visible = true;
                return true;
            } finally {
                if (requestVersion === checkoutRequestVersion) {
                    checkout.loading = false;
                }
            }
        }

        function checkoutAmount(option) {
            return formatMoney(option.amount);
        }

        function checkoutDescription(option) {
            if (option.consumption_type === "台位费") {
                let description = `开台：${formatDateTime(option.start_time)}`;

                if (option.close_time !== null && option.close_time !== undefined) {
                    const seconds = elapsedSeconds(option.start_time, option.close_time);
                    description += ` · 用时 ${formatDuration(seconds)}`;
                } else {
                    description += " · 尚未关台";
                }

                return description;
            }

            if (option.consumption_type === "商品") {
                return `数量：${option.quantity} · 单价：${formatMoney(option.unit_price)}`;
            }

            return "消费明细";
        }

        async function submitCheckout() {
            if (checkout.submitting || !selectedTableId.value) {
                return;
            }

            const consumptionIds = checkout.options
                .filter((option) => (
                    option.selectable === true
                    && checkout.selectedIds.includes(option.consumption_id)
                ))
                .map((option) => option.consumption_id);

            if (consumptionIds.length === 0) {
                setLocalError("CheckoutSelectionMissing", "请先勾选至少一项可结账的消费");
                return;
            }

            checkout.submitting = true;
            const currentTableId = selectedTableId.value;

            try {
                const result = await api.createUnpaidOrder(currentTableId, consumptionIds);
                const totalAmount = result.status === "success"
                    ? result.data?.["总金额"]
                    : null;

                publishOperation(result, {
                    hideData: true,
                    visibleData: totalAmount === null ? null : {
                        "总金额": totalAmount
                    }
                });

                if (result.status === "success" && totalAmount !== null) {
                    setNotice(
                        `订单已创建，后端确认应收 ${formatMoney(totalAmount)}`,
                        "success"
                    );
                }

                await loadCheckoutOptions(currentTableId);
                await refreshAll({ announce: false, updateResult: false });
            } finally {
                checkout.submitting = false;
            }
        }

        const payment = reactive({
            pickerOpen: false,
            loading: false,
            submitting: false,
            orders: []
        });
        const selectedOrderId = ref(null);
        let paymentRequestVersion = 0;

        // 收款预览直接引用后端订单快照中的 total_amount。
        const selectedOrder = computed(() => (
            payment.orders.find((order) => order.order_id === selectedOrderId.value) || null
        ));

        const paymentSelectionLabel = computed(() => {
            if (!selectedOrder.value) {
                return "选择待收款订单";
            }

            return `${selectedOrder.value.table_id}号桌 · ${formatMoney(selectedOrder.value.total_amount)} · ${selectedOrder.value.consumption_details.length} 项`;
        });

        function resetPaymentState() {
            paymentRequestVersion += 1;
            payment.pickerOpen = false;
            payment.loading = false;
            payment.submitting = false;
            payment.orders = [];
            selectedOrderId.value = null;
        }

        async function loadUnpaidOrders() {
            const requestVersion = ++paymentRequestVersion;
            payment.loading = true;

            try {
                const result = await api.getUnpaidOrders();

                if (requestVersion !== paymentRequestVersion) {
                    return false;
                }

                if (result.status !== "success") {
                    publishOperation(result);
                    return false;
                }

                payment.orders = result.data?.["未支付订单"] || [];

                if (
                    selectedOrderId.value !== null
                    && !selectedOrder.value
                ) {
                    selectedOrderId.value = null;
                }

                return true;
            } finally {
                if (requestVersion === paymentRequestVersion) {
                    payment.loading = false;
                }
            }
        }

        async function togglePaymentPicker() {
            if (payment.pickerOpen) {
                payment.pickerOpen = false;
                return;
            }

            const loaded = await loadUnpaidOrders();
            if (loaded) {
                payment.pickerOpen = true;
            }
        }

        function chooseUnpaidOrder(orderId) {
            if (!payment.orders.some((order) => order.order_id === orderId)) {
                return;
            }

            selectedOrderId.value = orderId;
            payment.pickerOpen = false;
        }

        function paymentDescription(detail) {
            if (detail.consumption_type === "台位费") {
                const seconds = elapsedSeconds(detail.start_time, detail.close_time);
                return `开台：${formatDateTime(detail.start_time)} · 关台：${formatDateTime(detail.close_time)} · 时长：${formatDuration(seconds)} · ${formatMoney(detail.hour_price)}/小时`;
            }

            if (detail.consumption_type === "商品") {
                return `数量：${detail.quantity} · 单价：${formatMoney(detail.unit_price)}`;
            }

            return "消费明细";
        }

        async function submitPayment() {
            if (!selectedOrder.value || payment.submitting) {
                return;
            }

            payment.submitting = true;
            const orderId = selectedOrder.value.order_id;

            try {
                const result = await api.confirmPayment(orderId);
                publishOperation(result, { hideData: true });

                if (result.status === "success") {
                    selectedOrderId.value = null;
                    await loadUnpaidOrders();
                }
            } finally {
                payment.submitting = false;
            }
        }

        function openTableDialog(tableId) {
            const nextTableId = String(tableId);

            if (selectedTableId.value !== nextTableId) {
                resetProductState();
                resetCheckoutState();
                resetPaymentState();
            }

            selectedTableId.value = nextTableId;

            if (dialogElement.value && !dialogElement.value.open) {
                dialogElement.value.showModal();
            }
        }

        function closeTableDialog() {
            if (dialogElement.value?.open) {
                dialogElement.value.close();
            }
        }

        function handleDialogClosed() {
            selectedTableId.value = null;
            resetProductState();
            resetCheckoutState();
            resetPaymentState();
        }

        function handleDialogBackdrop(event) {
            if (event.target === event.currentTarget) {
                closeTableDialog();
            }
        }

        onMounted(() => {
            refreshAll({ announce: false });
        });

        return {
            actionBusy,
            canDecreaseProduct,
            canIncreaseProduct,
            changeProductCount,
            checkout,
            checkoutAmount,
            checkoutDescription,
            checkoutSelectedCount,
            chooseProduct,
            chooseUnpaidOrder,
            closeTableDialog,
            dialogElement,
            dialogTitle,
            formatClockTime,
            formatDateTime,
            formatDuration,
            formatMoney,
            handleDialogBackdrop,
            handleDialogClosed,
            idleCount,
            isRefreshing,
            loadCheckoutOptions,
            notice,
            nowSeconds,
            normalizeProductCount,
            openCount,
            openTableDialog,
            payment,
            paymentDescription,
            paymentSelectionLabel,
            productCount,
            productList,
            productLoading,
            productPickerOpen,
            productSelectionLabel,
            refreshAll,
            resultText,
            resultTone,
            selectedOrder,
            selectedProduct,
            selectedTable,
            selectedTableId,
            selectedTableStatus,
            submitAddProduct,
            submitCheckout,
            submitCloseTable,
            submitOpenTable,
            submitPayment,
            tableList,
            togglePaymentPicker,
            toggleProductPicker
        };
    }
};

const app = window.Vue.createApp(App);
app.component("table-card", TableCard);
app.mount("#app");
