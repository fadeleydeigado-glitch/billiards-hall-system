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
import { addProduct, openTable, requestJson } from "../js/api.js";
import {
    createIdempotencyKey,
    createIdempotencyKeyStore
} from "../js/idempotency.js";

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

test("requestJson keeps the backend error payload on non-2xx responses", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(
        JSON.stringify({
            status: "error",
            error_type: "TableOccupied",
            data: null,
            message: "3 号桌正在使用中"
        }),
        {
            status: 409,
            headers: { "Content-Type": "application/json" }
        }
    );

    try {
        const payload = await requestJson(
            "/tables/open?table_id=3",
            { method: "POST" }
        );

        assert.equal(payload.status, "error");
        assert.equal(payload.error_type, "TableOccupied");
        assert.equal(payload.message, "3 号桌正在使用中");
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("openTable sends confirm_unsettled only on confirmed retry", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];

    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify({
            status: "success",
            error_type: null,
            data: null,
            message: "开台成功"
        }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
        });
    };

    try {
        await openTable("3");
        await openTable("3", { confirmUnsettled: true });

        assert.equal(calls[0].url, "/tables/open?table_id=3");
        assert.equal(calls[0].options.method, "POST");
        assert.equal(
            calls[1].url,
            "/tables/open?table_id=3&confirm_unsettled=true"
        );
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("open table warning panel is wired into the dialog", async () => {
    const appSource = await readFile(
        new URL("../app.js", import.meta.url),
        "utf8"
    );
    const indexSource = await readFile(
        new URL("../index.html", import.meta.url),
        "utf8"
    );

    assert.equal(appSource.includes("TableHasUnsettledConsumptions"), true);
    assert.equal(appSource.includes("confirmOpenWithUnsettled"), true);
    assert.equal(indexSource.includes('v-if="openWarning.visible"'), true);
    assert.equal(indexSource.includes("@click=\"confirmOpenWithUnsettled\""), true);
});

test("idempotency keys are UUID-shaped and reused until cleared", () => {
    assert.match(
        createIdempotencyKey(),
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    );

    let generated = 0;
    const store = createIdempotencyKeyStore(() => `key-${++generated}`);

    assert.equal(store.getOrCreate(), "key-1");
    assert.equal(store.getOrCreate(), "key-1");
    assert.equal(store.peek(), "key-1");

    store.clear();
    assert.equal(store.getOrCreate(), "key-2");
});

test("addProduct sends the idempotency key in the request header", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];

    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify({
            status: "success",
            error_type: null,
            data: null,
            message: "添加成功"
        }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
        });
    };

    try {
        const result = await addProduct("1", "P_001", 2, "fixed-key");

        assert.equal(result.status, "success");
        assert.equal(
            calls[0].options.headers["Idempotency-Key"],
            "fixed-key"
        );
        assert.match(calls[0].url, /product_count=2/);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("addProduct refuses to submit without an idempotency key", async () => {
    const originalFetch = globalThis.fetch;
    let called = false;
    globalThis.fetch = async () => {
        called = true;
        throw new Error("should not be called");
    };

    try {
        const result = await addProduct("1", "P_001", 1, "");

        assert.equal(result.status, "error");
        assert.equal(result.error_type, "IdempotencyKeyMissing");
        assert.equal(called, false);
    } finally {
        globalThis.fetch = originalFetch;
    }
});
