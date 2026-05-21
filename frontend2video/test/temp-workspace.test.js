const test = require("node:test");
const assert = require("node:assert/strict");

const {
    removeDirectoryWithRetries
} = require("../app/core/temp-workspace");

test("removeDirectoryWithRetries retries transient cleanup errors", async () => {
    let attempts = 0;

    await removeDirectoryWithRetries("C:/temp/workspace", {
        attempts: 4,
        delayMs: 1,
        async rmImpl() {
            attempts += 1;
            if (attempts < 3) {
                const error = new Error("busy");
                error.code = "EBUSY";
                throw error;
            }
        }
    });

    assert.equal(attempts, 3);
});

test("removeDirectoryWithRetries throws non-retryable errors immediately", async () => {
    await assert.rejects(
        () =>
            removeDirectoryWithRetries("C:/temp/workspace", {
                attempts: 4,
                delayMs: 1,
                async rmImpl() {
                    const error = new Error("permission denied");
                    error.code = "EACCES";
                    throw error;
                }
            }),
        (error) => error.code === "EACCES"
    );
});
