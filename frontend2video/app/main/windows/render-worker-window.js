const crypto = require("node:crypto");
const { BrowserWindow } = require("electron");

function createRenderWorkerWindow(options) {
    const {
        height,
        width
    } = options;

    return new BrowserWindow({
        backgroundColor: "#00000000",
        height,
        paintWhenInitiallyHidden: true,
        show: false,
        transparent: true,
        useContentSize: true,
        webPreferences: {
            backgroundThrottling: false,
            contextIsolation: true,
            nodeIntegration: false,
            partition: `render-worker-${crypto.randomUUID()}`,
            sandbox: false,
            webSecurity: false
        },
        width
    });
}

module.exports = {
    createRenderWorkerWindow
};
