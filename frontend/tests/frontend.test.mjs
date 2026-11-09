import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
    elapsedSeconds,
    formatClockTime,
    formatDateTime,
    formatDuration,
    formatMoney,
    formatRate
} from "../js/format.js";

test("elapsedSeconds derives a non-negative integer duration", () => {
    assert.equal(elapsedSeconds(1000, 1060), 60);
    assert.equal(elapsedSeconds(1060, 1000), 0);
    assert.equal(elapsedSeconds(null, 1060), null);
    assert.equal(elapsedSeconds("bad", 1060), null);
});

test("formatDuration handles minute, hour and multi-day sessions", () => {
    assert.equal(formatDuration(0), "00:00:00");
    assert.equal(formatDuration(59), "00:00:59");
    assert.equal(formatDuration(3600), "01:00:00");
    assert.equal(formatDuration(90061), "25:01:01");
    assert.equal(formatDuration(null), "--:--:--");
});

test("time formatting follows the browser local timezone", () => {
    const localDate = new Date(2026, 10, 5, 9, 7, 3);
    const timestamp = Math.floor(localDate.getTime() / 1000);

    assert.equal(formatClockTime(timestamp), "09:07:03");
    assert.equal(formatDateTime(timestamp), "11-05 09:07");
});

test("money formatting only formats values supplied by the backend", () => {
    assert.equal(formatMoney(36), "¥36");
    assert.equal(formatMoney("59.97"), "¥59.97");
    assert.equal(formatMoney(0), "¥0");
    assert.equal(formatMoney(null), "金额待确定");
    assert.equal(formatRate(30), "¥30/小时");
    assert.equal(formatRate("30.00"), "¥30/小时");
});

test("the frontend no longer contains a second table-fee calculation", async () => {
    const appSource = await readFile(
        new URL("../app.js", import.meta.url),
        "utf8"
    );

    assert.equal(appSource.includes("calculateDisplayTableFee"), false);
    assert.equal(appSource.includes("estimatedTotal"), false);
    assert.equal(appSource.includes("hourPrice *"), false);
    assert.equal(appSource.includes("querySelector"), false);
});

test("the table card template is defined once and loaded as a component", async () => {
    const indexSource = await readFile(
        new URL("../index.html", import.meta.url),
        "utf8"
    );
    const cardSource = await readFile(
        new URL("../js/components/TableCard.js", import.meta.url),
        "utf8"
    );

    assert.equal((indexSource.match(/<table-card/g) || []).length, 1);
    assert.equal(indexSource.includes('v-for="table in tableList"'), true);
    assert.equal(indexSource.includes("/assets/vendor/vue.global.prod.js"), true);
    assert.equal(cardSource.includes("template:"), true);
    assert.equal(cardSource.includes("table-card-title"), true);
});
