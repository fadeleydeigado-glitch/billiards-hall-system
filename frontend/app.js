import * as api from "./js/api.js";
import {
    elapsedSeconds,
    formatClockTime,
    formatDateTime,
    formatDuration,
    formatMoney
} from "./js/format.js";
import { TableCard } from "./js/components/TableCard.js";
import { createIdempotencyKeyStore } from "./js/idempotency.js";
import { useTables } from "./js/useTables.js";

const {
    computed,
    onBeforeUnmount,
    onMounted,
    reactive,
    ref
} = window.Vue;

const REFRESH_INTERVAL_MS = 4000;
const TABLE_CONFLICT_ERRORS = new Set([
    "TableOccupied",
    "TableFeeAlreadyClosed",
    "OpenTableConsumptionNotFound",
    "TableFeeNotFound"
]);
const PRODUCT_CONFLICT_ERRORS = new Set([
    "ProductStockNotEnough",
    "ProductNotFound"
]);
const PAYMENT_CONFLICT_ERRORS = new Set([
    "OrderNotUnpaid",
    "OrderNotFound"
]);
const CHECKOUT_CONFLICT_ERRORS = new Set([
    "SelectedConsumptionNotFound",
    "ConsumptionAlreadyInUnpaidOrder",
    "TableNotClosed"
]);

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
        const isBackgroundRefreshing = ref(false);
        const refreshButtonBusy = computed(() => (
            isRefreshing.value && !isBackgroundRefreshing.value
        ));
        let refreshPromise = null;
        let pendingRefresh = false;
        let pendingRefreshOptions = null;
        let refreshTimer = null;
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
        const openWarning = reactive({
            visible: false,
            tableId: null,
            items: [],
            summary: null,
            message: "",
            canConfirm: false
        });

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
        const canOpenTable = computed(() => (
            Boolean(selectedTable.value)
            && !selectedTable.value.isOpen
            && !actionBusy.open
        ));
        const canCloseTable = computed(() => (
            Boolean(selectedTable.value)
            && selectedTable.value.isOpen
            && !actionBusy.close
        ));
        const openButtonLabel = computed(() => {
            if (actionBusy.open) {
                return "处理中…";
            }
            return selectedTable.value?.isOpen ? "桌台使用中" : "确认开台";
        });
        const confirmOpenButtonLabel = computed(() => {
            if (actionBusy.open) {
                return "处理中…";
            }
            return selectedTable.value?.isOpen ? "桌台使用中" : "仍然开台";
        });
        const closeButtonLabel = computed(() => {
            if (actionBusy.close) {
                return "处理中…";
            }
            return selectedTable.value && !selectedTable.value.isOpen
                ? "桌台空闲"
                : "确认关台";
        });
        const openWarningSummary = computed(() => {
            if (!openWarning.visible || !openWarning.summary) {
                return "";
            }

            const count = openWarning.summary["消费笔数"] ?? 0;
            const amount = openWarning.summary["金额完整"] === false
                ? "金额待确定"
                : formatMoney(openWarning.summary["合计金额"]);

            return `${count} 笔 · 合计 ${amount}`;
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
            reconcileProductSelection();
        }

        function reconcileProductSelection() {
            if (selectedProductId.value === null) {
                return;
            }

            const currentProduct = productsById[selectedProductId.value];
            if (!currentProduct || currentProduct.stock <= 0) {
                resetProductState();
                return;
            }

            normalizeProductCount();
        }

        function normalizeRefreshOptions(options = {}) {
            const announce = options.announce ?? true;
            return {
                announce,
                updateResult: options.updateResult ?? announce,
                quiet: options.quiet === true,
                background: options.background === true,
                refreshPanels: options.refreshPanels !== false
            };
        }

        function mergeRefreshOptions(current, next) {
            return {
                announce: current.announce || next.announce,
                updateResult: current.updateResult || next.updateResult,
                quiet: current.quiet && next.quiet,
                background: current.background && next.background,
                refreshPanels: current.refreshPanels || next.refreshPanels
            };
        }

        async function refreshActivePanels() {
            const tasks = [];
            const currentTableId = selectedTableId.value;

            if (checkout.visible && currentTableId && !checkout.submitting) {
                tasks.push(loadCheckoutOptions(currentTableId, {
                    preserveSelection: true,
                    quiet: true,
                    showLoading: false
                }));
            }

            if (
                (payment.pickerOpen || selectedOrder.value)
                && !payment.submitting
            ) {
                tasks.push(loadUnpaidOrders({
                    quiet: true,
                    showLoading: false
                }));
            }

            if (tasks.length > 0) {
                await Promise.all(tasks);
            }
        }

        async function performRefresh(options) {
            try {
                const result = await api.getRefreshData();

                if (result.status !== "success") {
                    if (!options.quiet) {
                        publishOperation(result);
                    }
                    return false;
                }

                replaceTables(result.data?.["桌台基础信息"] || {});
                replaceProducts(result.data?.["商品信息"] || {});

                if (options.refreshPanels) {
                    await refreshActivePanels();
                }

                if (options.updateResult) {
                    resultText.value = JSON.stringify({
                        status: "success",
                        error_type: null,
                        data: null,
                        message: "桌台与商品信息已刷新"
                    }, null, 2);
                    resultTone.value = "success";
                }

                if (options.announce) {
                    setNotice("桌台与商品信息已刷新", "success");
                }

                return true;
            } catch (error) {
                if (!options.quiet) {
                    setLocalError(
                        "RefreshFailed",
                        error.message || "刷新数据失败"
                    );
                }
                return false;
            }
        }

        async function runRefreshLoop(initialOptions) {
            let options = initialOptions;
            let lastResult = false;
            isRefreshing.value = true;

            try {
                while (true) {
                    pendingRefresh = false;
                    isBackgroundRefreshing.value = options.background;
                    lastResult = await performRefresh(options);

                    if (!pendingRefresh) {
                        break;
                    }

                    options = pendingRefreshOptions || normalizeRefreshOptions({
                        announce: false,
                        updateResult: false,
                        quiet: true,
                        background: true
                    });
                    pendingRefreshOptions = null;
                }

                return lastResult;
            } finally {
                isRefreshing.value = false;
                isBackgroundRefreshing.value = false;
                pendingRefresh = false;
                pendingRefreshOptions = null;
            }
        }

        function refreshAll(options = {}) {
            const normalizedOptions = normalizeRefreshOptions(options);

            if (refreshPromise !== null) {
                pendingRefresh = true;
                pendingRefreshOptions = pendingRefreshOptions === null
                    ? normalizedOptions
                    : mergeRefreshOptions(
                        pendingRefreshOptions,
                        normalizedOptions
                    );
                if (!normalizedOptions.background) {
                    isBackgroundRefreshing.value = false;
                }
                return refreshPromise;
            }

            refreshPromise = runRefreshLoop(normalizedOptions).finally(() => {
                refreshPromise = null;
            });
            return refreshPromise;
        }

        async function refreshAfterMutation() {
            const refreshed = await refreshAll({
                announce: false,
                updateResult: false,
                quiet: true,
                background: true
            });

            if (!refreshed && notice.tone !== "error") {
                setNotice(
                    "操作已完成，但自动同步暂未成功，页面会在可见状态继续重试。",
                    "warning"
                );
            }
        }

        async function refreshAfterConflict(result) {
            const refreshed = await refreshAll({
                announce: false,
                updateResult: false,
                quiet: true,
                background: true
            });
            const message = result.message || "操作状态已变化";

            if (refreshed) {
                setNotice(
                    `${message}；已自动同步最新状态，请按当前界面重试。`,
                    "warning"
                );
            } else {
                setNotice(
                    `${message}；自动同步暂未成功，页面会继续重试。`,
                    "warning"
                );
            }
        }

        function resetOpenWarning() {
            openWarning.visible = false;
            openWarning.tableId = null;
            openWarning.items = [];
            openWarning.summary = null;
            openWarning.message = "";
            openWarning.canConfirm = false;
        }

        function showOpenWarning(result, tableId) {
            const data = result.data || {};

            openWarning.visible = true;
            openWarning.tableId = tableId;
            openWarning.items = data["消费明细"] || [];
            openWarning.summary = data["汇总"] || null;
            openWarning.message = result.message || "该桌还有未结账消费";
            openWarning.canConfirm = data["可确认开台"] === true;

            resultText.value = JSON.stringify(result, null, 2);
            resultTone.value = "warning";
            setNotice(openWarning.message, "warning");
        }

        async function goToCheckoutFromOpenWarning() {
            const tableId = openWarning.tableId || selectedTableId.value;

            if (!tableId) {
                return;
            }

            resetOpenWarning();
            const loaded = await loadCheckoutOptions(tableId);

            if (loaded) {
                setNotice("已切换到结账明细，请先勾选并结账。", "info");
            }
        }

        async function confirmOpenWithUnsettled() {
            if (!openWarning.visible || !openWarning.canConfirm) {
                return;
            }

            await submitOpenTable({ confirmUnsettled: true });
        }

        async function submitOpenTable(options = {}) {
            if (!selectedTableId.value || actionBusy.open) {
                return;
            }

            const confirmUnsettled = options.confirmUnsettled === true;
            const tableId = selectedTableId.value;
            actionBusy.open = true;

            try {
                const result = await api.openTable(tableId, { confirmUnsettled });

                if (result.status === "success") {
                    resetOpenWarning();
                    resetCheckoutState();
                    publishOperation(result);
                    await refreshAfterMutation();
                    return;
                }

                if (result.error_type === "TableHasUnsettledConsumptions") {
                    showOpenWarning(result, tableId);
                    return;
                }

                resetOpenWarning();
                publishOperation(result);
                if (TABLE_CONFLICT_ERRORS.has(result.error_type)) {
                    await refreshAfterConflict(result);
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
                } else if (TABLE_CONFLICT_ERRORS.has(result.error_type)) {
                    await refreshAfterConflict(result);
                }
            } finally {
                actionBusy.close = false;
            }
        }

        const productPickerOpen = ref(false);
        const productLoading = ref(false);
        const selectedProductId = ref(null);
        const productCount = ref(1);
        const addProductIdempotency = createIdempotencyKeyStore();

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
        const canSubmitProduct = computed(() => (
            Boolean(selectedProduct.value)
            && selectedProduct.value.stock > 0
            && productCount.value <= selectedProduct.value.stock
            && !actionBusy.addProduct
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

            let idempotencyKey;
            try {
                // 准备提交时才生成；失败重试继续复用，直到收到成功响应。
                idempotencyKey = addProductIdempotency.getOrCreate();
            } catch (error) {
                setLocalError(
                    "IdempotencyKeyGenerationFailed",
                    error.message || "生成 Idempotency-Key 失败"
                );
                return;
            }

            actionBusy.addProduct = true;

            try {
                const result = await api.addProduct(
                    selectedTableId.value,
                    selectedProduct.value.product_id,
                    productCount.value,
                    idempotencyKey
                );
                publishOperation(result);

                if (result.status === "success") {
                    addProductIdempotency.clear();
                    resetProductState();
                    await refreshAfterMutation();
                } else if (PRODUCT_CONFLICT_ERRORS.has(result.error_type)) {
                    await refreshAfterConflict(result);
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

        async function loadCheckoutOptions(tableId, options = {}) {
            const requestTableId = String(tableId || "");
            if (!requestTableId) {
                return false;
            }

            const {
                preserveSelection = false,
                quiet = false,
                showLoading = true
            } = options;
            const requestVersion = ++checkoutRequestVersion;
            if (showLoading) {
                checkout.loading = true;
            }

            try {
                const result = await api.getCheckoutOptions(requestTableId);

                if (
                    requestVersion !== checkoutRequestVersion
                    || selectedTableId.value !== requestTableId
                ) {
                    return false;
                }

                if (result.status !== "success") {
                    if (!quiet) {
                        publishOperation(result);
                    }
                    return false;
                }

                const nextOptions = result.data?.["消费明细"] || [];
                const selectableIds = new Set(
                    nextOptions
                        .filter((option) => option.selectable === true)
                        .map((option) => option.consumption_id)
                );
                const selectedIdsToKeep = preserveSelection
                    ? [...checkout.selectedIds]
                    : [];

                checkout.options = nextOptions;
                checkout.selectedIds = preserveSelection
                    ? selectedIdsToKeep.filter((id) => selectableIds.has(id))
                    : [];
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
            let result = null;

            try {
                result = await api.createUnpaidOrder(currentTableId, consumptionIds);
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
            } finally {
                checkout.submitting = false;
            }

            if (result !== null) {
                if (CHECKOUT_CONFLICT_ERRORS.has(result.error_type)) {
                    await refreshAfterConflict(result);
                } else {
                    await refreshAll({
                        announce: false,
                        updateResult: false,
                        quiet: true,
                        background: true
                    });
                }
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

        async function loadUnpaidOrders(options = {}) {
            const {
                quiet = false,
                showLoading = true
            } = options;
            const requestVersion = ++paymentRequestVersion;
            if (showLoading) {
                payment.loading = true;
            }

            try {
                const result = await api.getUnpaidOrders();

                if (requestVersion !== paymentRequestVersion) {
                    return false;
                }

                if (result.status !== "success") {
                    if (!quiet) {
                        publishOperation(result);
                    }
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
            let result = null;

            try {
                result = await api.confirmPayment(orderId);
                publishOperation(result, { hideData: true });

                if (result.status === "success") {
                    selectedOrderId.value = null;
                }
            } finally {
                payment.submitting = false;
            }

            if (result !== null) {
                if (PAYMENT_CONFLICT_ERRORS.has(result.error_type)) {
                    await refreshAfterConflict(result);
                } else if (result.status === "success") {
                    await loadUnpaidOrders();
                }
            }
        }

        function openTableDialog(tableId) {
            const nextTableId = String(tableId);

            if (selectedTableId.value !== nextTableId) {
                resetOpenWarning();
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
            resetOpenWarning();
            resetProductState();
            resetCheckoutState();
            resetPaymentState();
        }

        function handleDialogBackdrop(event) {
            if (event.target === event.currentTarget) {
                closeTableDialog();
            }
        }

        function startPolling() {
            if (
                refreshTimer !== null
                || document.visibilityState !== "visible"
            ) {
                return;
            }

            refreshTimer = window.setInterval(() => {
                if (document.visibilityState === "visible") {
                    refreshAll({
                        announce: false,
                        updateResult: false,
                        quiet: true,
                        background: true
                    });
                }
            }, REFRESH_INTERVAL_MS);
        }

        function stopPolling() {
            if (refreshTimer !== null) {
                window.clearInterval(refreshTimer);
                refreshTimer = null;
            }
        }

        function handleVisibilityChange() {
            if (document.visibilityState === "visible") {
                startPolling();
                refreshAll({
                    announce: false,
                    updateResult: false,
                    quiet: true,
                    background: true
                });
            } else {
                stopPolling();
            }
        }

        onMounted(() => {
            startPolling();
            document.addEventListener("visibilitychange", handleVisibilityChange);
            refreshAll({
                announce: false,
                updateResult: false
            });
        });

        onBeforeUnmount(() => {
            stopPolling();
            document.removeEventListener(
                "visibilitychange",
                handleVisibilityChange
            );
        });

        return {
            actionBusy,
            canDecreaseProduct,
            canIncreaseProduct,
            canCloseTable,
            canOpenTable,
            canSubmitProduct,
            changeProductCount,
            checkout,
            checkoutAmount,
            checkoutDescription,
            checkoutSelectedCount,
            chooseProduct,
            chooseUnpaidOrder,
            closeTableDialog,
            closeButtonLabel,
            confirmOpenWithUnsettled,
            confirmOpenButtonLabel,
            dialogElement,
            dialogTitle,
            formatClockTime,
            formatDateTime,
            formatDuration,
            formatMoney,
            goToCheckoutFromOpenWarning,
            handleDialogBackdrop,
            handleDialogClosed,
            idleCount,
            isRefreshing,
            refreshButtonBusy,
            loadCheckoutOptions,
            notice,
            nowSeconds,
            normalizeProductCount,
            openCount,
            openButtonLabel,
            openWarning,
            openWarningSummary,
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
