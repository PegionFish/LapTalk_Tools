const { dialog, ipcMain } = require("electron");

function registerSettingsIpc(options) {
    const {
        app,
        appState,
        mainWindow,
        queueService,
        settingsService
    } = options;

    ipcMain.handle("settings:get", () => {
        return settingsService.getSettings();
    });

    ipcMain.handle("settings:set-ffmpeg-path", async (_event, explicitPath) => {
        let ffmpegPath = explicitPath;

        if (!ffmpegPath) {
            const result = await dialog.showOpenDialog(mainWindow, {
                filters: [
                    {
                        extensions: process.platform === "win32" ? ["exe"] : ["*"],
                        name: "FFmpeg"
                    }
                ],
                properties: ["openFile"]
            });

            if (result.canceled || !result.filePaths.length) {
                return {
                    ok: false
                };
            }

            ffmpegPath = result.filePaths[0];
        }

        const updateResult = await settingsService.setFfmpegPath(ffmpegPath);
        if (!updateResult.ok) {
            return updateResult;
        }

        appState.setSettings(settingsService.getSettings());
        return updateResult;
    });

    ipcMain.handle("app:get-bootstrap", () => {
        return {
            appVersion: app.getVersion(),
            queue: queueService.getQueue(),
            settings: settingsService.getSettings()
        };
    });
}

module.exports = {
    registerSettingsIpc
};
