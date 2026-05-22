const path = require("node:path");

const { capturePreviewFrame, RenderStoppedError, renderTaskToMov } = require("../../core/render-engine");
const { createRenderWorkerWindow } = require("../windows/render-worker-window");

class RenderService {
    constructor(options) {
        this.appState = options.appState;
        this.queueService = options.queueService;
        this.settingsService = options.settingsService;
        this.abortController = null;
        this.currentChildProcess = null;
        this.currentTaskId = "";
        this.currentWorkerWindow = null;
        this.isRendering = false;
        this.previewInFlight = new Set();
        this.renderPromise = null;
    }

    async start() {
        if (this.isRendering) {
            return {
                accepted: false,
                error: {
                    code: "RENDER_ALREADY_RUNNING",
                    message: "A render task is already running.",
                    details: ""
                }
            };
        }

        if (!this.queueService.getQueue().length) {
            return {
                accepted: false,
                error: {
                    code: "QUEUE_EMPTY",
                    message: "Queue is empty.",
                    details: ""
                }
            };
        }

        const settings = await this.settingsService.ensureFfmpeg();
        if (!settings.ffmpegPath) {
            return {
                accepted: false,
                error: {
                    code: "FFMPEG_NOT_FOUND",
                    message: "Unable to locate FFmpeg executable.",
                    details: ""
                }
            };
        }

        this.isRendering = true;
        this.abortController = new AbortController();
        this.queueService.resetAllForRender();
        const tasks = this.queueService.getRenderableTasks();

        this.renderPromise = this.runQueue(tasks, settings.ffmpegPath).finally(() => {
            this.abortController = null;
            this.currentChildProcess = null;
            this.currentTaskId = "";
            this.currentWorkerWindow = null;
            this.isRendering = false;
            this.renderPromise = null;
        });

        return {
            accepted: true
        };
    }

    async stop() {
        if (!this.abortController) {
            return {
                accepted: false
            };
        }

        this.abortController.abort();

        if (this.currentChildProcess) {
            this.currentChildProcess.kill();
        }

        if (this.currentWorkerWindow && !this.currentWorkerWindow.isDestroyed()) {
            this.currentWorkerWindow.destroy();
        }

        return {
            accepted: true
        };
    }

    async captureTaskPreview(taskId) {
        if (!taskId || this.isRendering || this.previewInFlight.has(taskId)) {
            return;
        }

        const task = this.queueService.getTask(taskId);
        if (!task) {
            return;
        }

        this.previewInFlight.add(taskId);

        try {
            const preview = await capturePreviewFrame({
                createWorkerWindow: createRenderWorkerWindow,
                onWindowCreated: (window) => {
                    this.currentWorkerWindow = window;
                },
                onWindowDestroyed: () => {
                    if (!this.isRendering) {
                        this.currentWorkerWindow = null;
                    }
                },
                task
            });

            this.appState.emitRenderPreview({
                taskId,
                ...preview
            });
        } catch {}
        finally {
            this.previewInFlight.delete(taskId);
            if (!this.isRendering) {
                this.currentWorkerWindow = null;
            }
        }
    }

    async runQueue(tasks, ffmpegPath) {
        for (const task of tasks) {
            if (!this.abortController || this.abortController.signal.aborted) {
                break;
            }

            await this.runTask(task.id, ffmpegPath);
        }
    }

    async runTask(taskId, ffmpegPath) {
        const task = this.queueService.markTaskRendering(taskId);
        if (!task) {
            return;
        }

        this.currentTaskId = taskId;
        this.appState.emitRenderStatus({
            status: "rendering",
            taskId
        });

        try {
            const result = await renderTaskToMov({
                createWorkerWindow: createRenderWorkerWindow,
                ffmpegPath,
                onFfmpegSpawn: (child) => {
                    this.currentChildProcess = child;
                    this.queueService.markTaskEncoding(taskId);
                    this.appState.emitRenderProgress({
                        currentFrame: task.progress.totalFrames,
                        fileName: path.basename(task.pagePath),
                        percent: 100,
                        stage: "encoding",
                        taskId,
                        totalFrames: task.progress.totalFrames
                    });
                },
                onPreview: (preview) => {
                    this.appState.emitRenderPreview({
                        taskId,
                        ...preview
                    });
                },
                onProgress: (progress) => {
                    this.queueService.updateTaskProgress(taskId, progress);
                    this.appState.emitRenderProgress({
                        currentFrame: progress.currentFrame,
                        fileName: path.basename(task.pagePath),
                        percent: progress.percent,
                        stage: progress.stage,
                        taskId,
                        totalFrames: progress.totalFrames
                    });
                },
                onWindowCreated: (window) => {
                    this.currentWorkerWindow = window;
                },
                onWindowDestroyed: () => {
                    this.currentWorkerWindow = null;
                },
                signal: this.abortController.signal,
                task
            });

            this.queueService.markTaskDone(taskId);
            this.appState.emitRenderStatus({
                outputPath: result.outputPath,
                status: "done",
                taskId
            });
        } catch (error) {
            if (error instanceof RenderStoppedError || error.code === "RENDER_STOPPED") {
                this.queueService.markTaskStopped(taskId);
                this.appState.emitRenderStatus({
                    status: "stopped",
                    taskId
                });
                return;
            }

            const appError = mapError(error);
            this.queueService.markTaskError(taskId, appError);
            this.appState.emitRenderStatus({
                error: appError,
                status: "error",
                taskId
            });
        } finally {
            this.currentChildProcess = null;
            this.currentTaskId = "";
        }
    }
}

function mapError(error) {
    const code = error.code || "RENDER_FAILED";

    const codeToMessage = {
        FFMPEG_ENCODE_FAILED: "FFmpeg 编码失败。",
        HTML_FILE_NOT_FOUND: "HTML 文件不存在。",
        HTML_EXTENSION_INVALID: "仅支持导入 .html / .htm 文件。",
        PAGE_LOAD_TIMEOUT: "页面加载超时。",
        PAGE_PREPARE_FAILED: "页面准备脚本执行失败。",
        QUEUE_EMPTY: "当前没有可导出的任务。",
        RENDER_FAILED: "渲染失败。"
    };

    return {
        code,
        details: error.message || "",
        message: codeToMessage[code] || error.message || "渲染失败。"
    };
}

module.exports = {
    RenderService
};
