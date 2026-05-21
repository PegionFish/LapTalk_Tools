const crypto = require("node:crypto");

const { alignDurationToFrames, resolveDurationSeconds } = require("../../core/duration");
const { getDefaultOutputDirectory, getDefaultOutputFilename } = require("../../core/output-paths");
const { validateHtmlPath } = require("../../core/validate-html");

class QueueService {
    constructor(options) {
        this.appState = options.appState;
        this.settingsService = options.settingsService;
        this.queue = [];
    }

    getQueue() {
        return cloneValue(this.queue);
    }

    getTask(taskId) {
        return cloneValue(this.queue.find((task) => task.id === taskId) || null);
    }

    async addPaths(filePaths) {
        const settings = this.settingsService.getSettings();
        const knownIdentities = new Set(this.queue.map((task) => task.identity));

        const addedTasks = [];
        const rejected = [];

        for (const filePath of filePaths || []) {
            const validation = validateHtmlPath(filePath);
            if (!validation.ok) {
                rejected.push({
                    path: filePath,
                    error: validation.error
                });
                continue;
            }

            if (knownIdentities.has(validation.identity)) {
                continue;
            }

            knownIdentities.add(validation.identity);

            const duration = await resolveDurationSeconds({
                htmlPath: validation.normalizedPath,
                manualDurationSeconds: null
            });

            const task = createTask(validation.normalizedPath, validation.identity, {
                durationSeconds: duration.seconds,
                durationSource: duration.source,
                fps: settings.defaultFps,
                height: settings.defaultHeight,
                width: settings.defaultWidth
            });

            this.queue.push(task);
            addedTasks.push(cloneValue(task));
        }

        this.publishQueue();

        return {
            addedTasks,
            rejected
        };
    }

    clear() {
        this.queue = [];
        this.publishQueue();
    }

    async updateDefaults(defaults) {
        const settings = await this.settingsService.updateDefaults({
            defaultFps: defaults.fps,
            defaultHeight: defaults.height,
            defaultWidth: defaults.width
        });

        this.queue = this.queue.map((task) => {
            if (task.status === "rendering") {
                return task;
            }

            return applyTaskDefaults(task, {
                fps: settings.defaultFps,
                height: settings.defaultHeight,
                width: settings.defaultWidth
            });
        });

        this.publishQueue();
        return settings;
    }

    async updateTaskOutputDirectory(taskId, outputDirectory) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return null;
        }

        task.outputDirectory = outputDirectory;
        await this.settingsService.setLastOutputDirectory(outputDirectory);
        this.publishQueue();
        return cloneValue(task);
    }

    resetAllForRender() {
        this.queue = this.queue.map((task) => resetTaskForRender(task));
        this.publishQueue();
    }

    getRenderableTasks() {
        return this.queue
            .filter((task) => task.status === "ready")
            .map((task) => cloneValue(task));
    }

    markTaskRendering(taskId) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return null;
        }

        task.status = "rendering";
        task.error = null;
        task.progress.stage = "capturing";
        this.publishQueue();
        return cloneValue(task);
    }

    markTaskEncoding(taskId) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return;
        }

        task.progress.stage = "encoding";
        task.progress.currentFrame = task.progress.totalFrames;
        task.progress.percent = 100;
        this.publishQueue();
    }

    updateTaskProgress(taskId, progress) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return;
        }

        task.progress = {
            ...task.progress,
            ...progress
        };
        this.publishQueue();
    }

    markTaskDone(taskId) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return;
        }

        task.status = "done";
        task.error = null;
        task.progress.currentFrame = task.progress.totalFrames;
        task.progress.percent = 100;
        task.progress.stage = "done";
        this.publishQueue();
    }

    markTaskStopped(taskId) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return;
        }

        task.status = "stopped";
        task.progress.stage = "stopped";
        this.publishQueue();
    }

    markTaskError(taskId, error) {
        const task = this.queue.find((item) => item.id === taskId);
        if (!task) {
            return;
        }

        task.status = "error";
        task.error = error;
        task.progress.stage = "error";
        this.publishQueue();
    }

    publishQueue() {
        this.appState.setQueue(this.queue);
    }
}

function createTask(pagePath, identity, defaults) {
    const durationInfo = alignDurationToFrames(
        defaults.durationSeconds,
        defaults.fps
    );

    return {
        durationSeconds: defaults.durationSeconds,
        durationSource: defaults.durationSource,
        error: null,
        fps: defaults.fps,
        frameAlignedDurationSeconds: durationInfo.frameAlignedDurationSeconds,
        height: defaults.height,
        id: crypto.randomUUID(),
        identity,
        outputDirectory: getDefaultOutputDirectory(pagePath),
        outputFilename: getDefaultOutputFilename(pagePath),
        pagePath,
        progress: {
            currentFrame: 0,
            percent: 0,
            stage: "idle",
            totalFrames: durationInfo.totalFrames
        },
        status: "ready",
        width: defaults.width
    };
}

function applyTaskDefaults(task, defaults) {
    const durationInfo = alignDurationToFrames(task.durationSeconds, defaults.fps);

    return {
        ...task,
        fps: defaults.fps,
        frameAlignedDurationSeconds: durationInfo.frameAlignedDurationSeconds,
        height: defaults.height,
        progress: {
            currentFrame: 0,
            percent: 0,
            stage: "idle",
            totalFrames: durationInfo.totalFrames
        },
        status: task.status === "done" ? "done" : "ready",
        width: defaults.width
    };
}

function resetTaskForRender(task) {
    const durationInfo = alignDurationToFrames(task.durationSeconds, task.fps);
    return {
        ...task,
        error: null,
        frameAlignedDurationSeconds: durationInfo.frameAlignedDurationSeconds,
        progress: {
            currentFrame: 0,
            percent: 0,
            stage: "idle",
            totalFrames: durationInfo.totalFrames
        },
        status: "ready"
    };
}

function cloneValue(value) {
    return JSON.parse(JSON.stringify(value));
}

module.exports = {
    QueueService
};
