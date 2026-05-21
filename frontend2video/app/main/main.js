const path = require("node:path");
const { app } = require("electron");

const { registerDialogIpc } = require("./ipc/dialog-ipc");
const { registerQueueIpc } = require("./ipc/queue-ipc");
const { registerRenderIpc } = require("./ipc/render-ipc");
const { registerSettingsIpc } = require("./ipc/settings-ipc");
const { AppState } = require("./services/app-state");
const { QueueService } = require("./services/queue-service");
const { RenderService } = require("./services/render-service");
const { SettingsService } = require("./services/settings-service");
const { createMainWindow } = require("./windows/main-window");

let mainWindow = null;
let services = null;

async function bootstrap() {
    if (!services) {
        const appRoot = path.resolve(__dirname, "..", "..");
        const appState = new AppState();
        const settingsService = new SettingsService({ app, appRoot });
        await settingsService.initialize();

        const queueService = new QueueService({
            appState,
            settingsService
        });
        const renderService = new RenderService({
            appState,
            queueService,
            settingsService
        });

        appState.setSettings(settingsService.getSettings());
        appState.setQueue(queueService.getQueue());

        services = {
            appState,
            queueService,
            renderService,
            settingsService
        };

        registerQueueIpc({
            appState,
            queueService,
            renderService,
            settingsService
        });
        registerDialogIpc({
            appState,
            getMainWindow: () => mainWindow,
            queueService,
            settingsService
        });
        registerRenderIpc({
            renderService
        });
        registerSettingsIpc({
            app,
            appState,
            getMainWindow: () => mainWindow,
            queueService,
            settingsService
        });
    }

    mainWindow = createMainWindow();
    registerStateForwarding(mainWindow, services.appState);

    mainWindow.webContents.once("did-finish-load", () => {
        mainWindow.webContents.send(
            "settings:changed",
            services.settingsService.getSettings()
        );
        mainWindow.webContents.send("queue:changed", services.queueService.getQueue());
    });
}

function registerStateForwarding(window, appState) {
    appState.on("queue:changed", (queue) => {
        sendToRenderer(window, "queue:changed", queue);
    });

    appState.on("settings:changed", (settings) => {
        sendToRenderer(window, "settings:changed", settings);
    });

    appState.on("render:progress", (payload) => {
        sendToRenderer(window, "render:progress", payload);
    });

    appState.on("render:preview", (payload) => {
        sendToRenderer(window, "render:preview", payload);
    });

    appState.on("render:status", (payload) => {
        sendToRenderer(window, "render:status", payload);
    });
}

function sendToRenderer(window, channel, payload) {
    if (!window || window.isDestroyed()) {
        return;
    }

    window.webContents.send(channel, payload);
}

app.whenReady().then(async () => {
    await bootstrap();

    app.on("activate", async () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            return;
        }
        await bootstrap();
    });
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});
