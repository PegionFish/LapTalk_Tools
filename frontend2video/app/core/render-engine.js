const fs = require("node:fs/promises");
const path = require("node:path");

const {
    DEFAULT_LOAD_TIMEOUT_MS,
    DEFAULT_PREVIEW_FRAME_INTERVAL,
    DEFAULT_SETTLE_MS
} = require("./constants");
const { encodeMovFromFrames } = require("./ffmpeg");
const { buildRenderUrl, getPreparePageScript } = require("./page-prepare");
const {
    cleanupTempWorkspace,
    createTempWorkspace
} = require("./temp-workspace");

class RenderStoppedError extends Error {
    constructor(message = "Rendering stopped by user.") {
        super(message);
        this.name = "RenderStoppedError";
        this.code = "RENDER_STOPPED";
    }
}

async function capturePreviewFrame(options) {
    const {
        createWorkerWindow,
        onWindowCreated,
        onWindowDestroyed,
        task
    } = options;

    const workspace = await createTempWorkspace();
    let hiddenWindow = null;

    try {
        hiddenWindow = createWorkerWindow({
            browserProfileDirectory: workspace.browserProfileDirectory,
            height: task.height,
            width: task.width
        });

        if (onWindowCreated) {
            onWindowCreated(hiddenWindow);
        }

        await loadTaskPage(hiddenWindow, task);
        await preparePage(hiddenWindow, 0, DEFAULT_SETTLE_MS);
        const image = await captureWindowImage(hiddenWindow, task.width, task.height);

        return {
            frameIndex: 0,
            previewDataUrl: image.toDataURL(),
            totalFrames: task.progress.totalFrames
        };
    } finally {
        await destroyHiddenWindow(hiddenWindow, onWindowDestroyed);
        await cleanupTempWorkspace(workspace);
    }
}

async function renderTaskToMov(options) {
    const {
        createWorkerWindow,
        ffmpegPath,
        onFfmpegSpawn,
        onPreview,
        onProgress,
        onWindowCreated,
        onWindowDestroyed,
        signal,
        task
    } = options;

    const workspace = await createTempWorkspace();
    let hiddenWindow = null;

    try {
        await fs.mkdir(task.outputDirectory, { recursive: true });

        hiddenWindow = createWorkerWindow({
            browserProfileDirectory: workspace.browserProfileDirectory,
            height: task.height,
            width: task.width
        });

        if (onWindowCreated) {
            onWindowCreated(hiddenWindow);
        }

        await loadTaskPage(hiddenWindow, task);
        await captureFrames({
            hiddenWindow,
            onPreview,
            onProgress,
            signal,
            task,
            workspace
        });

        if (signal && signal.aborted) {
            throw new RenderStoppedError();
        }

        const outputPath = path.join(task.outputDirectory, task.outputFilename);
        await encodeMovFromFrames({
            ffmpegPath,
            fps: task.fps,
            framesDirectory: workspace.framesDirectory,
            onSpawn: onFfmpegSpawn,
            outputPath,
            signal
        });

        return {
            outputPath
        };
    } finally {
        await destroyHiddenWindow(hiddenWindow, onWindowDestroyed);
        await cleanupTempWorkspace(workspace);
    }
}

async function captureFrames(options) {
    const {
        hiddenWindow,
        onPreview,
        onProgress,
        signal,
        task,
        workspace
    } = options;

    for (let frameIndex = 0; frameIndex < task.progress.totalFrames; frameIndex += 1) {
        if (signal && signal.aborted) {
            throw new RenderStoppedError();
        }

        const timeMs = (frameIndex * 1000) / task.fps;
        await preparePage(hiddenWindow, timeMs, DEFAULT_SETTLE_MS);

        const image = await captureWindowImage(hiddenWindow, task.width, task.height);
        await writePngFrame(workspace.framesDirectory, image, frameIndex);

        if (
            frameIndex === 0 ||
            frameIndex === task.progress.totalFrames - 1 ||
            frameIndex % DEFAULT_PREVIEW_FRAME_INTERVAL === 0
        ) {
            onPreview({
                frameIndex,
                previewDataUrl: image.toDataURL(),
                totalFrames: task.progress.totalFrames
            });
        }

        onProgress({
            currentFrame: frameIndex + 1,
            percent: Number(
                (((frameIndex + 1) / task.progress.totalFrames) * 100).toFixed(2)
            ),
            stage: "capturing",
            totalFrames: task.progress.totalFrames
        });
    }
}

async function loadTaskPage(hiddenWindow, task) {
    const renderUrl = buildRenderUrl(task.pagePath, {
        renderHeight: task.height,
        renderMs: 0,
        renderSettleMs: DEFAULT_SETTLE_MS,
        renderWidth: task.width
    });

    await withTimeout(
        hiddenWindow.loadURL(renderUrl),
        DEFAULT_LOAD_TIMEOUT_MS,
        "PAGE_LOAD_TIMEOUT"
    );
}

async function preparePage(hiddenWindow, renderTimeMs, settleMs) {
    try {
        await hiddenWindow.webContents.executeJavaScript(
            getPreparePageScript(renderTimeMs, settleMs),
            true
        );
    } catch (error) {
        error.code = error.code || "PAGE_PREPARE_FAILED";
        throw error;
    }
}

async function captureWindowImage(hiddenWindow, width, height) {
    return hiddenWindow.webContents.capturePage({
        height,
        width,
        x: 0,
        y: 0
    });
}

async function writePngFrame(framesDirectory, image, frameIndex) {
    const fileName = `frame_${String(frameIndex + 1).padStart(5, "0")}.png`;
    const framePath = path.join(framesDirectory, fileName);
    await fs.writeFile(framePath, image.toPNG());
}

function withTimeout(promise, timeoutMs, code) {
    let timerId = null;

    return Promise.race([
        promise,
        new Promise((_, reject) => {
            timerId = setTimeout(() => {
                const error = new Error("Operation timed out.");
                error.code = code;
                reject(error);
            }, timeoutMs);
        })
    ]).finally(() => {
        if (timerId) {
            clearTimeout(timerId);
        }
    });
}

async function destroyHiddenWindow(hiddenWindow, onWindowDestroyed) {
    if (hiddenWindow && !hiddenWindow.isDestroyed()) {
        const activeSession = hiddenWindow.webContents.session;

        try {
            if (activeSession && typeof activeSession.flushStorageData === "function") {
                await activeSession.flushStorageData();
            }
        } catch {}

        hiddenWindow.destroy();
        await sleep(180);
    }

    if (onWindowDestroyed) {
        onWindowDestroyed();
    }
}

function sleep(delayMs) {
    return new Promise((resolve) => {
        setTimeout(resolve, delayMs);
    });
}

module.exports = {
    RenderStoppedError,
    capturePreviewFrame,
    renderTaskToMov
};
