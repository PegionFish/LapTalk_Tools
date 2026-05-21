const fs = require("node:fs/promises");
const { dialog, ipcMain, shell } = require("electron");

function registerDialogIpc(options) {
    const {
        appState,
        getMainWindow,
        queueService,
        settingsService
    } = options;

    ipcMain.handle("dialog:import-html", async () => {
        const mainWindow = getMainWindow();
        const result = await dialog.showOpenDialog(mainWindow, {
            filters: [
                {
                    extensions: ["html", "htm"],
                    name: "HTML"
                }
            ],
            properties: ["openFile", "multiSelections"]
        });

        if (result.canceled || !result.filePaths.length) {
            return {
                addedTasks: [],
                rejected: []
            };
        }

        return queueService.addPaths(result.filePaths);
    });

    ipcMain.handle("output:choose-directory", async (_event, taskId) => {
        const task = queueService.getTask(taskId);
        const startPath =
            (task && task.outputDirectory) ||
            settingsService.getSettings().lastOutputDirectory;
        const mainWindow = getMainWindow();

        const result = await dialog.showOpenDialog(mainWindow, {
            defaultPath: startPath || undefined,
            properties: ["openDirectory", "createDirectory"]
        });

        if (result.canceled || !result.filePaths.length) {
            return {
                ok: false
            };
        }

        const outputDirectory = result.filePaths[0];
        const updatedTask = await queueService.updateTaskOutputDirectory(
            taskId,
            outputDirectory
        );

        appState.setSettings(settingsService.getSettings());

        return {
            ok: true,
            task: updatedTask
        };
    });

    ipcMain.handle("output:open-directory", async (_event, taskId) => {
        const task = queueService.getTask(taskId);
        const outputDirectory =
            (task && task.outputDirectory) ||
            settingsService.getSettings().lastOutputDirectory;

        if (!outputDirectory) {
            return {
                ok: false
            };
        }

        await fs.mkdir(outputDirectory, { recursive: true });
        const errorMessage = await shell.openPath(outputDirectory);

        return {
            ok: !errorMessage,
            error: errorMessage || ""
        };
    });
}

module.exports = {
    registerDialogIpc
};
