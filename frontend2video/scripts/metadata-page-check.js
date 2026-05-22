const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const {
    alignDurationToFrames,
    resolveDurationSeconds
} = require("../app/core/duration");
const { resolveAvailableFfmpeg } = require("../app/core/ffmpeg");

async function main() {
    const repoRoot = path.resolve(__dirname, "..");
    const samplePath = path.join(
        repoRoot,
        "samples",
        "metadata-timeline-demo.html"
    );

    const duration = await resolveDurationSeconds({
        htmlPath: samplePath,
        manualDurationSeconds: null
    });

    if (duration.source !== "html-meta") {
        throw new Error(`Expected html-meta duration source, got ${duration.source}.`);
    }

    if (duration.seconds !== 3.25) {
        throw new Error(`Expected metadata duration 3.25, got ${duration.seconds}.`);
    }

    const fps = 12;
    const framePlan = alignDurationToFrames(duration.seconds, fps);
    const ffmpeg = await resolveAvailableFfmpeg({
        appRoot: repoRoot,
        resourcesPath: repoRoot,
        savedPath: ""
    });

    if (!ffmpeg.ffmpegPath) {
        throw new Error("FFmpeg not found for metadata page test.");
    }

    const tempRoot = path.join(
        os.tmpdir(),
        `frontend2video-metadata-${Date.now()}`
    );

    await fs.mkdir(tempRoot, { recursive: true });

    try {
        const appDir = path.join(tempRoot, "app");
        const outputDirectory = path.join(tempRoot, "exports");
        const resultPath = path.join(tempRoot, "result.json");

        await fs.mkdir(appDir, { recursive: true });
        await fs.mkdir(outputDirectory, { recursive: true });

        await fs.writeFile(
            path.join(appDir, "package.json"),
            JSON.stringify({
                name: "frontend2video-metadata-page-test",
                main: "metadata-render-app.js"
            }),
            "utf8"
        );

        await fs.writeFile(
            path.join(appDir, "metadata-render-app.js"),
            getMetadataRunnerSource(),
            "utf8"
        );

        await runElectronMetadataTest({
            appDir,
            ffmpegPath: ffmpeg.ffmpegPath,
            fps,
            outputDirectory,
            pagePath: samplePath,
            repoRoot,
            resultPath,
            totalFrames: framePlan.totalFrames
        });

        const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
        if (!result.ok) {
            throw new Error(result.error || result.fatal || "Metadata page render failed.");
        }

        if (result.durationSource !== "html-meta") {
            throw new Error(`Expected rendered duration source html-meta, got ${result.durationSource}.`);
        }

        if (result.durationSeconds !== 3.25) {
            throw new Error(`Expected rendered duration 3.25, got ${result.durationSeconds}.`);
        }

        if (result.totalFrames !== framePlan.totalFrames) {
            throw new Error(`Expected ${framePlan.totalFrames} frames, got ${result.totalFrames}.`);
        }

        const outputStat = await fs.stat(result.outputPath);
        process.stdout.write(
            `${JSON.stringify(
                {
                    durationSeconds: result.durationSeconds,
                    durationSource: result.durationSource,
                    ffmpegPath: ffmpeg.ffmpegPath,
                    frameAlignedDurationSeconds: framePlan.frameAlignedDurationSeconds,
                    outputBytes: outputStat.size,
                    outputPath: result.outputPath,
                    progressEvents: result.progressEvents,
                    totalFrames: result.totalFrames
                },
                null,
                2
            )}\n`
        );
    } finally {
        await fs.rm(tempRoot, {
            force: true,
            recursive: true
        });
    }
}

function runElectronMetadataTest(options) {
    const electronExecutablePath =
        process.platform === "win32"
            ? path.join(
                options.repoRoot,
                "node_modules",
                "electron",
                "dist",
                "electron.exe"
            )
            : path.join(
                options.repoRoot,
                "node_modules",
                ".bin",
                "electron"
            );

    return new Promise((resolve, reject) => {
        const child = spawn(
            electronExecutablePath,
            [
                options.appDir,
                options.repoRoot,
                options.pagePath,
                options.outputDirectory,
                options.resultPath,
                options.ffmpegPath,
                String(options.fps),
                String(options.totalFrames)
            ],
            {
                cwd: options.repoRoot,
                env: {
                    ...process.env,
                    NODE_PATH: path.join(options.repoRoot, "node_modules")
                },
                stdio: ["ignore", "pipe", "pipe"],
                windowsHide: true
            }
        );

        let stdout = "";
        let stderr = "";

        child.stdout.on("data", (chunk) => {
            stdout += chunk.toString();
        });

        child.stderr.on("data", (chunk) => {
            stderr += chunk.toString();
        });

        child.once("error", (error) => {
            reject(error);
        });

        child.once("close", (exitCode) => {
            if (exitCode !== 0) {
                reject(
                    new Error(
                        [
                            `Metadata page test exited with code ${exitCode}.`,
                            stdout.trim(),
                            stderr.trim()
                        ]
                            .filter(Boolean)
                            .join("\n")
                    )
                );
                return;
            }

            resolve();
        });
    });
}

function getMetadataRunnerSource() {
    return `const fs = require("node:fs/promises");
const path = require("node:path");
const { app } = require("electron");

app.on("window-all-closed", (event) => {
    event.preventDefault();
});

const repoRoot = process.argv[2];
const pagePath = process.argv[3];
const outputDirectory = process.argv[4];
const resultPath = process.argv[5];
const ffmpegPath = process.argv[6];
const fps = Number(process.argv[7]);
const totalFrames = Number(process.argv[8]);

async function writeResult(payload) {
    await fs.writeFile(resultPath, JSON.stringify(payload, null, 2), "utf8");
}

async function main() {
    await app.whenReady();

    const { resolveDurationSeconds } = require(path.join(repoRoot, "app", "core", "duration.js"));
    const { renderTaskToMov } = require(path.join(repoRoot, "app", "core", "render-engine.js"));
    const { createRenderWorkerWindow } = require(path.join(repoRoot, "app", "main", "windows", "render-worker-window.js"));

    const duration = await resolveDurationSeconds({
        htmlPath: pagePath,
        manualDurationSeconds: null
    });

    const task = {
        id: "metadata-page-task",
        pagePath,
        outputDirectory,
        outputFilename: "metadata-page.mov",
        width: 1920,
        height: 1080,
        fps,
        durationSeconds: duration.seconds,
        durationSource: duration.source,
        progress: {
            currentFrame: 0,
            totalFrames,
            percent: 0,
            stage: "idle"
        }
    };

    const payload = {
        ok: false,
        durationSeconds: duration.seconds,
        durationSource: duration.source,
        progressEvents: 0
    };

    try {
        const result = await renderTaskToMov({
            createWorkerWindow: createRenderWorkerWindow,
            ffmpegPath,
            onFfmpegSpawn: () => {
                payload.ffmpegSpawned = true;
            },
            onPreview: () => {},
            onProgress: () => {
                payload.progressEvents += 1;
            },
            signal: new AbortController().signal,
            task
        });

        payload.ok = true;
        payload.outputPath = result.outputPath;
        payload.totalFrames = totalFrames;
    } catch (error) {
        payload.error = error && error.stack ? error.stack : String(error);
    }

    await writeResult(payload);
    app.exit(payload.ok ? 0 : 1);
}

main().catch(async (error) => {
    await writeResult({
        ok: false,
        fatal: error && error.stack ? error.stack : String(error)
    });
    app.exit(1);
});
`;
}

main().catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
});
