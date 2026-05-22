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
        const viewportBinding = await applyPageThemeBinding(hiddenWindow, task);
        await preparePage(hiddenWindow, 0, DEFAULT_SETTLE_MS);
        const image = await captureWindowImage(hiddenWindow, task.width, task.height);

        return {
            ...viewportBinding,
            canvasHeight: task.height,
            canvasWidth: task.width,
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
        const viewportBinding = await applyPageThemeBinding(hiddenWindow, task);
        await captureFrames({
            hiddenWindow,
            onPreview,
            onProgress,
            signal,
            task,
            viewportBinding,
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
        viewportBinding,
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
                ...viewportBinding,
                canvasHeight: task.height,
                canvasWidth: task.width,
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

async function applyPageThemeBinding(hiddenWindow, task) {
    hiddenWindow.webContents.setZoomFactor(1);

    const pageMetrics = await inspectPageThemeMetrics(hiddenWindow);
    const viewportBinding = resolveViewportBinding({
        targetHeight: task.height,
        targetWidth: task.width,
        themeHeight: pageMetrics.themeHeight,
        themeWidth: pageMetrics.themeWidth
    });

    hiddenWindow.webContents.setZoomFactor(viewportBinding.renderScale);

    await hiddenWindow.webContents.executeJavaScript(
        `
            (() => {
                document.documentElement.style.overflow = "hidden";
                document.documentElement.style.setProperty("--render-scale", "${viewportBinding.renderScale}");
                document.documentElement.setAttribute("data-render-scale", "${viewportBinding.renderScale}");

                if (document.body) {
                    document.body.style.overflow = "hidden";
                }

                window.scrollTo(0, 0);
            })();
        `,
        true
    );

    await hiddenWindow.webContents.executeJavaScript(
        "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
        true
    );

    return viewportBinding;
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

async function inspectPageThemeMetrics(hiddenWindow) {
    const metrics = await hiddenWindow.webContents.executeJavaScript(
        `
            (() => {
                const documentElement = document.documentElement;
                const body = document.body;
                const firstElement = body && body.firstElementChild
                    ? body.firstElementChild
                    : null;

                const bodyRect = body ? body.getBoundingClientRect() : null;
                const firstElementRect = firstElement
                    ? firstElement.getBoundingClientRect()
                    : null;

                const widthCandidates = [
                    window.innerWidth,
                    documentElement ? documentElement.clientWidth : 0,
                    documentElement ? documentElement.scrollWidth : 0,
                    documentElement ? documentElement.offsetWidth : 0,
                    body ? body.clientWidth : 0,
                    body ? body.scrollWidth : 0,
                    body ? body.offsetWidth : 0,
                    bodyRect ? bodyRect.width : 0,
                    firstElementRect ? firstElementRect.width : 0
                ];

                const heightCandidates = [
                    window.innerHeight,
                    documentElement ? documentElement.clientHeight : 0,
                    documentElement ? documentElement.scrollHeight : 0,
                    documentElement ? documentElement.offsetHeight : 0,
                    body ? body.clientHeight : 0,
                    body ? body.scrollHeight : 0,
                    body ? body.offsetHeight : 0,
                    bodyRect ? bodyRect.height : 0,
                    firstElementRect ? firstElementRect.height : 0
                ];

                return {
                    themeHeight: Math.max(...heightCandidates.map((value) => Math.ceil(Number(value) || 0))),
                    themeWidth: Math.max(...widthCandidates.map((value) => Math.ceil(Number(value) || 0)))
                };
            })();
        `,
        true
    );

    return {
        themeHeight: toPositiveNumber(metrics.themeHeight),
        themeWidth: toPositiveNumber(metrics.themeWidth)
    };
}

function resolveViewportBinding(options) {
    const safeTargetWidth = toPositiveNumber(options.targetWidth) || 1;
    const safeTargetHeight = toPositiveNumber(options.targetHeight) || 1;
    const safeThemeWidth = toPositiveNumber(options.themeWidth) || safeTargetWidth;
    const safeThemeHeight = toPositiveNumber(options.themeHeight) || safeTargetHeight;

    const renderScale = Math.min(
        safeTargetWidth / safeThemeWidth,
        safeTargetHeight / safeThemeHeight
    );

    return {
        renderScale: normalizeScale(renderScale),
        themeHeight: safeThemeHeight,
        themeWidth: safeThemeWidth
    };
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

function toPositiveNumber(value) {
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) {
        return number;
    }

    return 0;
}

function normalizeScale(value) {
    if (!Number.isFinite(value) || value <= 0) {
        return 1;
    }

    return Number(value.toFixed(4));
}

module.exports = {
    RenderStoppedError,
    capturePreviewFrame,
    renderTaskToMov,
    resolveViewportBinding
};
