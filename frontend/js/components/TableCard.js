import {
    elapsedSeconds,
    formatClockTime,
    formatDuration,
    formatRate
} from "../format.js";

// 所有桌台共用这一份卡片模板。
// props 提供数据，emits 只把点击事件交回页面处理。
export const TableCard = {
    props: {
        table: {
            type: Object,
            required: true
        },
        nowSeconds: {
            type: Number,
            required: true
        }
    },
    emits: ["select"],
    computed: {
        statusText() {
            return this.table.isOpen ? "使用中" : "空闲";
        },
        elapsedLabel() {
            const seconds = elapsedSeconds(
                this.table.startTime,
                this.nowSeconds
            );
            return formatDuration(seconds);
        },
        startLabel() {
            return formatClockTime(this.table.startTime);
        },
        rateLabel() {
            return formatRate(this.table.hourPrice);
        }
    },
    template: `
        <button
            class="table-card"
            :class="{ 'is-open': table.isOpen }"
            type="button"
            :data-table-id="table.tableId"
            :data-status="table.isOpen ? 'open' : 'idle'"
            :aria-label="table.tableId + '号桌，' + statusText"
            @click="$emit('select', table.tableId)"
        >
            <span class="table-card-head">
                <span class="table-ball" aria-hidden="true">{{ table.tableId }}</span>
                <span class="status-pill" :class="{ 'is-live': table.isOpen }">
                    <span class="status-dot" aria-hidden="true"></span>
                    {{ statusText }}
                </span>
            </span>

            <span class="table-card-title">{{ table.tableId }}号桌</span>

            <template v-if="table.isOpen">
                <span class="table-live-details">
                    <span class="table-live-row">
                        <span>开始</span>
                        <strong>{{ startLabel }}</strong>
                    </span>
                    <span class="table-live-row">
                        <span>已用</span>
                        <strong class="live-duration">{{ elapsedLabel }}</strong>
                    </span>
                </span>
                <span class="table-rate">{{ rateLabel }}</span>
            </template>

            <span v-else class="table-idle-copy">点击开台或查看桌台操作</span>
        </button>
    `
};
