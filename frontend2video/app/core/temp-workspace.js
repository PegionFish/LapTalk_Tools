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

    await fs.rm(workspace.rootDirectory, {
        recursive: true,
        force: true
    });
}

module.exports = {
    cleanupTempWorkspace,
    createTempWorkspace
};
