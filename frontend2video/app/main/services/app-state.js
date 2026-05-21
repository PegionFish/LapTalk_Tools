const { EventEmitter } = require("node:events");

class AppState extends EventEmitter {
    constructor() {
        super();
        this.queue = [];
        this.settings = null;
    }

    setQueue(queue) {
        this.queue = cloneValue(queue);
        this.emit("queue:changed", this.getQueue());
    }

    getQueue() {
        return cloneValue(this.queue);
    }

    setSettings(settings) {
        this.settings = cloneValue(settings);
        this.emit("settings:changed", this.getSettings());
    }

    getSettings() {
        return cloneValue(this.settings);
    }

    emitRenderProgress(payload) {
        this.emit("render:progress", cloneValue(payload));
    }

    emitRenderPreview(payload) {
        this.emit("render:preview", cloneValue(payload));
    }

    emitRenderStatus(payload) {
        this.emit("render:status", cloneValue(payload));
    }
}

function cloneValue(value) {
    if (value === undefined) {
        return undefined;
    }

    return JSON.parse(JSON.stringify(value));
}

module.exports = {
    AppState
};
