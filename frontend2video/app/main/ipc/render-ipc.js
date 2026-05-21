const { ipcMain } = require("electron");

function registerRenderIpc(options) {
    const { renderService } = options;

    ipcMain.handle("render:start", async () => {
        return renderService.start();
    });

    ipcMain.handle("render:stop", async () => {
        return renderService.stop();
    });

    ipcMain.handle("preview:capture", async (_event, taskId) => {
        await renderService.captureTaskPreview(taskId);
        return {
            ok: true
        };
    });
}

module.exports = {
    registerRenderIpc
};
