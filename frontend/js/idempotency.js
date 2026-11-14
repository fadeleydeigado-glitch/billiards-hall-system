// 生成并保存“尚未成功”的幂等键。
// 同一笔提交失败重试时复用旧键，成功后由调用方 clear()。
function fallbackUuid() {
    const cryptoApi = globalThis.crypto;

    if (!cryptoApi || typeof cryptoApi.getRandomValues !== "function") {
        throw new Error("当前浏览器不支持安全随机数，无法生成 Idempotency-Key");
    }

    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, (byte) => (
        byte.toString(16).padStart(2, "0")
    ));

    return [
        hex.slice(0, 4).join(""),
        hex.slice(4, 6).join(""),
        hex.slice(6, 8).join(""),
        hex.slice(8, 10).join(""),
        hex.slice(10, 16).join("")
    ].join("-");
}

export function createIdempotencyKey() {
    const cryptoApi = globalThis.crypto;

    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
        return cryptoApi.randomUUID();
    }

    return fallbackUuid();
}

export function createIdempotencyKeyStore(generateKey = createIdempotencyKey) {
    let currentKey = null;

    return {
        getOrCreate() {
            if (currentKey === null) {
                currentKey = generateKey();
            }
            return currentKey;
        },

        clear() {
            currentKey = null;
        },

        peek() {
            return currentKey;
        }
    };
}
