const path = require("node:path");
const { BrowserWindow } = require("electron");

function createMainWindow() {
    const preloadPath = path.join(__dirname, "..", "..", "preload", "preload.js");
    const htmlPath = path.join(__dirname, "..", "..", "renderer", "index.html");

    const window = new BrowserWindow({
        backgroundColor: "#131416",
        height: 920,
        minHeight: 760,
        minWidth: 1120,
        show: false,
        title: "Frontend2Video",
        useContentSize: true,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            preload: preloadPath,
            sandbox: false
        },
        width: 1480
    });

    window.once("ready-to-show", () => {
        window.show();
    });

    window.loadFile(htmlPath);
    return window;
}

module.exports = {
    createMainWindow
};
