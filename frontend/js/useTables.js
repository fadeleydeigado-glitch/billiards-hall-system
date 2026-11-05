const { computed, onBeforeUnmount, onMounted, reactive, ref } = window.Vue;

// 桌台以 table_id 为 key 保存成唯一的页面字典。
// 卡片模板只读取这个字典，不再单独维护 DOM 字段。
function normalizeTable(tableId, tableData) {
    function optionalNumber(value) {
        if (value === null || value === undefined || value === "") {
            return null;
        }

        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    return {
        tableId: String(tableData.table_id ?? tableId),
        isOpen: tableData.is_open === true,
        startTime: optionalNumber(tableData.start_time),
        hourPrice: optionalNumber(tableData.hour_price)
    };
}

export function useTables() {
    const tablesById = reactive({});
    // 所有桌卡共用一个秒级时钟；它不会触发后端请求。
    const nowSeconds = ref(Math.floor(Date.now() / 1000));
    let clockTimer = null;

    const tableList = computed(() => {
        const collator = new Intl.Collator("zh-CN", {
            numeric: true,
            sensitivity: "base"
        });

        return Object.values(tablesById).sort((left, right) => (
            collator.compare(left.tableId, right.tableId)
        ));
    });

    function replaceTables(tablePayload) {
        const nextTables = {};

        Object.entries(tablePayload || {}).forEach(([tableId, tableData]) => {
            nextTables[String(tableId)] = normalizeTable(tableId, tableData);
        });

        Object.keys(tablesById).forEach((tableId) => {
            delete tablesById[tableId];
        });
        Object.assign(tablesById, nextTables);
    }

    function syncNow() {
        nowSeconds.value = Math.floor(Date.now() / 1000);
    }

    function startClock() {
        syncNow();
        if (clockTimer === null) {
            clockTimer = window.setInterval(syncNow, 1000);
        }
    }

    function stopClock() {
        if (clockTimer !== null) {
            window.clearInterval(clockTimer);
            clockTimer = null;
        }
    }

    function handleVisibilityChange() {
        if (!document.hidden) {
            syncNow();
        }
    }

    onMounted(() => {
        startClock();
        document.addEventListener("visibilitychange", handleVisibilityChange);
    });

    onBeforeUnmount(() => {
        stopClock();
        document.removeEventListener("visibilitychange", handleVisibilityChange);
    });

    return {
        tableList,
        tablesById,
        nowSeconds,
        replaceTables
    };
}
