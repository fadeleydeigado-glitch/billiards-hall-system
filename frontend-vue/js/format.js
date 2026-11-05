const DURATION_PLACEHOLDER = "--:--:--";

function toFiniteNumber(value) {
    if (value === null || value === undefined || value === "") {
        return null;
    }

    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

export function elapsedSeconds(startTime, nowSeconds) {
    const start = toFiniteNumber(startTime);
    const now = toFiniteNumber(nowSeconds);

    if (start === null || now === null) {
        return null;
    }

    return Math.max(0, Math.floor(now - start));
}

export function formatDuration(totalSeconds) {
    const seconds = toFiniteNumber(totalSeconds);

    if (seconds === null) {
        return DURATION_PLACEHOLDER;
    }

    const wholeSeconds = Math.max(0, Math.floor(seconds));
    const hours = String(Math.floor(wholeSeconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((wholeSeconds % 3600) / 60)).padStart(2, "0");
    const remainder = String(wholeSeconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${remainder}`;
}

export function formatClockTime(timestamp) {
    const seconds = toFiniteNumber(timestamp);
    if (seconds === null) {
        return "--:--:--";
    }

    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) {
        return "--:--:--";
    }

    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    const remain = String(date.getSeconds()).padStart(2, "0");
    return `${hours}:${minutes}:${remain}`;
}

export function formatDateTime(timestamp) {
    const seconds = toFiniteNumber(timestamp);
    if (seconds === null) {
        return "--";
    }

    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) {
        return "--";
    }

    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${month}-${day} ${formatClockTime(seconds).slice(0, 5)}`;
}

export function formatMoney(value, placeholder = "金额待确定") {
    const amount = toFiniteNumber(value);
    return amount === null ? placeholder : `¥${amount}`;
}

export function formatRate(value) {
    const amount = toFiniteNumber(value);
    return amount === null ? "价格未配置" : `¥${amount}/小时`;
}
