const { ipcMain } = require("electron");

function registerQueueIpc(options) {
    const {
        appState,
        queueService,
        renderService,
        settingsService
    } = options;

    ipcMain.handle("queue:add-paths", async (_event, filePaths) => {
        const result = await queueService.addPaths(filePaths);
        appState.setSettings(settingsService.getSettings());

        const previewTask = result.addedTasks[0];
        if (previewTask) {
            void renderService.captureTaskPreview(previewTask.id);
        }

        return result;
    });

    ipcMain.handle("queue:clear", () => {
        queueService.clear();
        return {
            ok: true
        };
    });

    ipcMain.handle("queue:update-defaults", async (_event, defaults) => {
        const settings = await queueService.updateDefaults(defaults);
        appState.setSettings(settings);
        return {
            ok: true,
            settings
        };
    });
}

module.exports = {
    registerQueueIpc
};
