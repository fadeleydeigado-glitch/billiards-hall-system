import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME_PATH = process.env.CHROME_PATH
    || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PREVIEW_URL = process.env.PREVIEW_URL
    || "http://127.0.0.1:8765/preview";
const DEBUG_PORT = Number(process.env.CHROME_DEBUG_PORT || 9333);

const sleep = (milliseconds) => new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
});

async function waitForDevtoolsTarget() {
    const deadline = Date.now() + 8000;

    while (Date.now() < deadline) {
        try {
            const response = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
            const targets = await response.json();
            const pageTarget = targets.find((target) => target.type === "page");

            if (pageTarget?.webSocketDebuggerUrl) {
                return pageTarget;
            }
        } catch (error) {
            // Chrome is still starting.
        }

        await sleep(120);
    }

    throw new Error("Chrome DevTools endpoint did not become ready");
}

function createCdpClient(webSocket) {
    let messageId = 0;
    const pending = new Map();
    const events = [];
    const exceptions = [];

    webSocket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);

        if (message.id && pending.has(message.id)) {
            const { resolve, reject } = pending.get(message.id);
            pending.delete(message.id);

            if (message.error) {
                reject(new Error(message.error.message));
            } else {
                resolve(message.result);
            }
            return;
        }

        events.push(message);

        if (message.method === "Runtime.exceptionThrown") {
            exceptions.push(
                message.params?.exceptionDetails?.exception?.description
                || message.params?.exceptionDetails?.text
                || "Unknown browser exception"
            );
        }

        if (
            message.method === "Log.entryAdded"
            && message.params?.entry?.level === "error"
        ) {
            exceptions.push(message.params.entry.text);
        }
    });

    function command(method, params = {}) {
        const id = ++messageId;

        return new Promise((resolve, reject) => {
            pending.set(id, { resolve, reject });
            webSocket.send(JSON.stringify({ id, method, params }));
        });
    }

    return { command, events, exceptions };
}

async function main() {
    const resetResponse = await fetch("http://127.0.0.1:8765/__reset", {
        method: "POST"
    });
    assert.equal(resetResponse.ok, true, "Mock backend reset failed");

    const profileDir = await mkdtemp(join(tmpdir(), "billiards-vue-smoke-"));
    const chrome = spawn(CHROME_PATH, [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--no-first-run",
        "--remote-allow-origins=*",
        `--remote-debugging-port=${DEBUG_PORT}`,
        `--user-data-dir=${profileDir}`,
        "about:blank"
    ], {
        stdio: ["ignore", "ignore", "pipe"],
        windowsHide: true
    });

    let webSocket = null;

    try {
        const target = await waitForDevtoolsTarget();
        webSocket = new WebSocket(target.webSocketDebuggerUrl);

        await new Promise((resolve, reject) => {
            webSocket.addEventListener("open", resolve, { once: true });
            webSocket.addEventListener("error", reject, { once: true });
        });

        const cdp = createCdpClient(webSocket);
        await cdp.command("Page.enable");
        await cdp.command("Runtime.enable");
        await cdp.command("Log.enable");
        await cdp.command("Network.enable");
        await cdp.command("Emulation.setDeviceMetricsOverride", {
            width: 1440,
            height: 1200,
            deviceScaleFactor: 1,
            mobile: false
        });
        await cdp.command("Page.navigate", { url: PREVIEW_URL });

        async function evaluate(expression) {
            const result = await cdp.command("Runtime.evaluate", {
                expression,
                returnByValue: true,
                awaitPromise: true
            });

            if (result.exceptionDetails) {
                throw new Error(
                    result.exceptionDetails.exception?.description
                    || result.exceptionDetails.text
                );
            }

            return result.result.value;
        }

        async function waitFor(expression, message, timeout = 6000) {
            const deadline = Date.now() + timeout;

            while (Date.now() < deadline) {
                if (await evaluate(`Boolean(${expression})`)) {
                    return;
                }
                await sleep(100);
            }

            throw new Error(message);
        }

        await waitFor(
            "document.querySelectorAll('[data-table-id]').length === 4",
            "Vue table cards did not render"
        );

        assert.equal(
            await evaluate("document.querySelectorAll('[data-table-id][data-status=\"open\"]').length"),
            2,
            "Expected two open mock tables"
        );
        assert.equal(
            await evaluate("document.body.innerText.includes('4号桌')"),
            true,
            "The fourth database-configured table should render automatically"
        );
        assert.equal(
            await evaluate("document.body.innerText.includes('台费：¥')"),
            false,
            "The table card must not render a client-calculated fee"
        );
        assert.equal(
            await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"),
            true,
            "Desktop layout should not overflow horizontally"
        );
        console.log("✔ table cards are data-driven and use one template");

        await evaluate("document.querySelector('[data-table-id=\"2\"]').click(); true");
        await waitFor(
            "document.querySelector('dialog.table-dialog')?.open === true",
            "Table dialog did not open"
        );
        assert.equal(
            (await evaluate("document.querySelector('.dialog-status').textContent.trim()")),
            "空闲"
        );

        await evaluate(
            "document.querySelector('form[data-action=\"open-table\"] button[type=\"submit\"]').click(); true"
        );
        await waitFor(
            "document.querySelector('[data-table-id=\"2\"]')?.dataset.status === 'open'",
            "Opening a table did not reactively refresh its card"
        );
        assert.equal(
            (await evaluate("document.querySelector('.dialog-status').textContent.trim()")),
            "使用中"
        );
        assert.equal(
            await evaluate("document.querySelector('dialog.table-dialog').getBoundingClientRect().width <= window.innerWidth"),
            true,
            "The dialog should fit inside the desktop viewport"
        );
        console.log("✔ open action updates table state without a manual refresh");

        const firstDuration = await evaluate(
            "document.querySelector('[data-table-id=\"2\"] .live-duration').textContent.trim()"
        );
        await sleep(2150);
        const secondDuration = await evaluate(
            "document.querySelector('[data-table-id=\"2\"] .live-duration').textContent.trim()"
        );
        assert.notEqual(firstDuration, secondDuration, "Live duration did not tick");

        const requestCountBeforeIdle = cdp.events.filter(
            (event) => event.method === "Network.requestWillBeSent"
        ).length;
        await sleep(2200);
        const requestCountAfterIdle = cdp.events.filter(
            (event) => event.method === "Network.requestWillBeSent"
        ).length;
        assert.equal(
            requestCountAfterIdle,
            requestCountBeforeIdle,
            "The one-second clock should not poll the backend"
        );
        console.log("✔ elapsed time ticks locally without extra API requests");

        await evaluate(
            "document.querySelector('[data-action=\"add-product\"] [data-role=\"product-select\"]').click(); true"
        );
        await waitFor(
            "document.querySelectorAll('[data-action=\"add-product\"] .option-row').length === 2",
            "Product options did not render"
        );
        await evaluate(
            "document.querySelector('[data-action=\"add-product\"] .option-row:not(:disabled)').click(); true"
        );
        assert.equal(
            await evaluate("document.querySelector('.quantity-input').disabled"),
            false,
            "Quantity input should enable after selecting a product"
        );
        console.log("✔ product options and selection are reactive");

        await evaluate(
            "document.querySelector('[data-role=\"load-checkout\"]').click(); true"
        );
        await waitFor(
            "document.querySelectorAll('[data-action=\"create-unpaid-order\"] .checkout-item').length === 2",
            "Checkout options did not render"
        );
        const checkoutSummary = await evaluate(
            "document.querySelector('.checkout-summary').textContent"
        );
        assert.equal(
            /¥\s*\d/.test(checkoutSummary),
            false,
            "The checkout preview must not calculate or display a client-side total"
        );
        await evaluate(
            "document.querySelector('[data-action=\"create-unpaid-order\"] .checkout-item input:not(:disabled)').click(); true"
        );
        await waitFor(
            "document.querySelector('.checkout-summary').textContent.includes('已选 1 项')",
            "Checkout selection did not update reactively"
        );
        assert.equal(
            /¥\s*\d/.test(await evaluate("document.querySelector('.checkout-summary').textContent")),
            false,
            "Checkbox changes must not introduce a frontend total"
        );
        console.log("✔ checkout shows backend line items without a frontend total");

        await evaluate(
            "document.querySelector('[data-action=\"create-unpaid-order\"] button[type=\"submit\"]').click(); true"
        );
        await waitFor(
            "document.querySelector('.notice.is-success')?.textContent.includes('后端确认应收 ¥66') === true",
            "Created-order notice did not display the backend total"
        );
        console.log("✔ created-order result uses the backend total verbatim");

        await evaluate(
            "document.querySelector('[data-role=\"payment-select\"]').click(); true"
        );
        await waitFor(
            "document.querySelectorAll('[data-action=\"confirm-payment\"] .option-row').length === 1",
            "Unpaid orders did not render"
        );
        await evaluate(
            "document.querySelector('[data-action=\"confirm-payment\"] .option-row').click(); true"
        );
        await waitFor(
            "Boolean(document.querySelector('.payment-preview'))",
            "Payment preview did not render"
        );
        assert.equal(
            (await evaluate("document.querySelector('.payment-total').textContent.trim()")),
            "¥51",
            "Payment preview should display the backend total verbatim"
        );
        console.log("✔ payment preview displays the backend order total");

        await cdp.command("Emulation.setDeviceMetricsOverride", {
            width: 390,
            height: 844,
            deviceScaleFactor: 1,
            mobile: true
        });
        await sleep(300);
        assert.equal(
            await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"),
            true,
            "Mobile layout should not overflow horizontally"
        );
        assert.equal(
            await evaluate("getComputedStyle(document.querySelector('.table-grid')).gridTemplateColumns.split(' ').length"),
            2,
            "Mobile table grid should use two columns"
        );
        assert.equal(
            await evaluate("document.querySelector('dialog.table-dialog').getBoundingClientRect().width <= window.innerWidth"),
            true,
            "The dialog should fit inside the mobile viewport"
        );
        console.log("✔ desktop and mobile layouts fit their viewports");

        assert.deepEqual(cdp.exceptions, [], "Browser reported runtime errors");
        console.log("✔ no browser runtime errors");
    } finally {
        if (webSocket) {
            webSocket.close();
        }
        chrome.kill();
    }
}

main().catch((error) => {
    console.error("Browser smoke test failed:", error);
    process.exitCode = 1;
});
