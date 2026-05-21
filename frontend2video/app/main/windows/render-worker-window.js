const { BrowserWindow, session } = require("electron");

function createRenderWorkerWindow(options) {
    const {
        browserProfileDirectory,
        height,
        width
    } = options;

    const isolatedSession = session.fromPath(browserProfileDirectory, {
        cache: false
    });

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
            sandbox: false,
            session: isolatedSession,
            webSecurity: false
        },
        width
    });
}

module.exports = {
    createRenderWorkerWindow
};
