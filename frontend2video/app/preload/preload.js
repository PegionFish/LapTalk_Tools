const { contextBridge, ipcRenderer } = require("electron");

function subscribe(channel, callback) {
    const listener = (_event, payload) => {
        callback(payload);
    };

    ipcRenderer.on(channel, listener);

    return () => {
        ipcRenderer.removeListener(channel, listener);
    };
}

contextBridge.exposeInMainWorld("frontend2video", {
    app: {
        getBootstrap() {
            return ipcRenderer.invoke("app:get-bootstrap");
        }
    },
    dialog: {
        importHtml() {
            return ipcRenderer.invoke("dialog:import-html");
        }
    },
    output: {
        chooseDirectory(taskId) {
            return ipcRenderer.invoke("output:choose-directory", taskId);
        },
        openDirectory(taskId) {
            return ipcRenderer.invoke("output:open-directory", taskId);
        }
    },
    preview: {
        capture(taskId) {
            return ipcRenderer.invoke("preview:capture", taskId);
        }
    },
    queue: {
        addPaths(paths) {
            return ipcRenderer.invoke("queue:add-paths", paths);
        },
        clear() {
            return ipcRenderer.invoke("queue:clear");
        },
        updateDefaults(defaults) {
            return ipcRenderer.invoke("queue:update-defaults", defaults);
        }
    },
    render: {
        start() {
            return ipcRenderer.invoke("render:start");
        },
        stop() {
            return ipcRenderer.invoke("render:stop");
        }
    },
    settings: {
        get() {
            return ipcRenderer.invoke("settings:get");
        },
        setFfmpegPath(explicitPath) {
            return ipcRenderer.invoke("settings:set-ffmpeg-path", explicitPath);
        }
    },
    events: {
        onQueueChanged(callback) {
            return subscribe("queue:changed", callback);
        },
        onRenderPreview(callback) {
            return subscribe("render:preview", callback);
        },
        onRenderProgress(callback) {
            return subscribe("render:progress", callback);
        },
        onRenderStatus(callback) {
            return subscribe("render:status", callback);
        },
        onSettingsChanged(callback) {
            return subscribe("settings:changed", callback);
        }
    }
});
