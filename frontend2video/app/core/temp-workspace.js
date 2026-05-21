const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

async function createTempWorkspace() {
    const rootDirectory = path.join(
        os.tmpdir(),
        `laptalk-render-${crypto.randomUUID()}`
    );

    const browserProfileDirectory = path.join(rootDirectory, "browser-profile");
    const framesDirectory = path.join(rootDirectory, "frames");

    await fs.mkdir(browserProfileDirectory, { recursive: true });
    await fs.mkdir(framesDirectory, { recursive: true });

    return {
        rootDirectory,
        browserProfileDirectory,
        framesDirectory
    };
}

async function cleanupTempWorkspace(workspace) {
    if (!workspace || !workspace.rootDirectory) {
        return;
    }

    await removeDirectoryWithRetries(workspace.rootDirectory);
}

async function removeDirectoryWithRetries(
    directoryPath,
    options = {}
) {
    const attempts = options.attempts ?? 8;
    const delayMs = options.delayMs ?? 180;
    const rmImpl = options.rmImpl ?? fs.rm;

    let lastError = null;

    for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
            await rmImpl(directoryPath, {
                recursive: true,
                force: true
            });
            return;
        } catch (error) {
            lastError = error;

            if (!isRetryableDirectoryCleanupError(error) || attempt === attempts) {
                throw error;
            }

            await sleep(delayMs * attempt);
        }
    }

    if (lastError) {
        throw lastError;
    }
}

function isRetryableDirectoryCleanupError(error) {
    return Boolean(
        error &&
        (
            error.code === "EBUSY" ||
            error.code === "EPERM" ||
            error.code === "ENOTEMPTY"
        )
    );
}

function sleep(delayMs) {
    return new Promise((resolve) => {
        setTimeout(resolve, delayMs);
    });
}

module.exports = {
    cleanupTempWorkspace,
    createTempWorkspace,
    removeDirectoryWithRetries
};
