const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const { resolveAvailableFfmpeg } = require("../app/core/ffmpeg");

async function main() {
    const repoRoot = path.resolve(__dirname, "..");
    const ffmpeg = await resolveAvailableFfmpeg({
        appRoot: repoRoot,
        resourcesPath: repoRoot,
        savedPath: ""
    });

    if (!ffmpeg.ffmpegPath) {
        throw new Error("FFmpeg not found for smoke render.");
    }

    const tempRoot = path.join(
        os.tmpdir(),
        `frontend2video-smoke-${Date.now()}`
    );

    await fs.mkdir(tempRoot, { recursive: true });

    try {
        const appDir = path.join(tempRoot, "app");
        const htmlPath = path.join(tempRoot, "sample.html");
        const outputDirectory = path.join(tempRoot, "exports");
        const resultPath = path.join(tempRoot, "result.json");

        await fs.mkdir(appDir, { recursive: true });
        await fs.mkdir(outputDirectory, { recursive: true });

        await fs.writeFile(
            path.join(appDir, "package.json"),
            JSON.stringify({
                name: "frontend2video-smoke",
                main: "smoke-render-app.js"
            }),
            "utf8"
        );

        await fs.writeFile(htmlPath, getSmokeHtml(), "utf8");
        await fs.writeFile(
            path.join(appDir, "smoke-render-app.js"),
            getSmokeRunnerSource(),
            "utf8"
        );

        await runElectronSmoke({
            appDir,
            ffmpegPath: ffmpeg.ffmpegPath,
            htmlPath,
            outputDirectory,
            repoRoot,
            resultPath
        });

        const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
        if (!result.ok) {
            throw new Error(result.error || result.fatal || "Smoke render failed.");
        }

        const outputPath = result.outputPath;
        const outputStat = await fs.stat(outputPath);

        process.stdout.write(
            `${JSON.stringify(
                {
                    ffmpegPath: ffmpeg.ffmpegPath,
                    framesCaptured: result.progress.length,
                    outputBytes: outputStat.size,
                    outputPath,
                    previewsCaptured: result.previews.length
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

function runElectronSmoke(options) {
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
                options.htmlPath,
                options.outputDirectory,
                options.resultPath,
                options.ffmpegPath
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
                const error = new Error(
                    [
                        `Smoke render exited with code ${exitCode}.`,
                        stdout.trim(),
                        stderr.trim()
                    ]
                        .filter(Boolean)
                        .join("\n")
                );
                reject(error);
                return;
            }

            resolve();
        });
    });
}

function getSmokeHtml() {
    return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="laptalk:duration-seconds" content="1">
    <style>
        html, body {
            margin: 0;
            width: 100%;
            height: 100%;
            background: transparent;
            overflow: hidden;
        }

        body {
            position: relative;
        }

        .box {
            position: absolute;
            top: 40px;
            left: 20px;
            width: 80px;
            height: 80px;
            border-radius: 24px;
            background: rgba(216, 155, 67, 0.9);
        }

        .label {
            position: absolute;
            left: 18px;
            bottom: 18px;
            color: white;
            font: 700 20px/1.2 Segoe UI, sans-serif;
        }
    </style>
</head>
<body>
    <div class="box" id="box"></div>
    <div class="label" id="label">0 ms</div>
    <script>
        window.__setRenderTime = async (ms) => {
            const progress = Math.min(1, Math.max(0, ms / 1000));
            const box = document.getElementById("box");
            const label = document.getElementById("label");

            box.style.left = \`\${20 + progress * 180}px\`;
            box.style.top = \`\${40 + progress * 40}px\`;
            box.style.transform = \`rotate(\${progress * 180}deg)\`;
            label.textContent = \`\${Math.round(ms)} ms\`;
        };
    </script>
</body>
</html>
`;
}

function getSmokeRunnerSource() {
    return `const fs = require("node:fs/promises");
const path = require("node:path");
const { app } = require("electron");

app.on("window-all-closed", (event) => {
    event.preventDefault();
});

const repoRoot = process.argv[2];
const htmlPath = process.argv[3];
const outputDir = process.argv[4];
const resultPath = process.argv[5];
const ffmpegPath = process.argv[6];

async function writeResult(payload) {
    await fs.writeFile(resultPath, JSON.stringify(payload, null, 2), "utf8");
}

async function main() {
    await app.whenReady();

    const { renderTaskToMov } = require(path.join(repoRoot, "app", "core", "render-engine.js"));
    const { createRenderWorkerWindow } = require(path.join(repoRoot, "app", "main", "windows", "render-worker-window.js"));

    const task = {
        id: "smoke-task",
        pagePath: htmlPath,
        outputDirectory: outputDir,
        outputFilename: "sample.mov",
        width: 320,
        height: 180,
        fps: 12,
        durationSeconds: 1,
        durationSource: "html-meta",
        progress: {
            currentFrame: 0,
            totalFrames: 12,
            percent: 0,
            stage: "idle"
        }
    };

    const abortController = new AbortController();
    const payload = {
        ok: false,
        progress: [],
        previews: []
    };

    try {
        const result = await renderTaskToMov({
            createWorkerWindow: createRenderWorkerWindow,
            ffmpegPath,
            onFfmpegSpawn: () => {
                payload.ffmpegSpawned = true;
            },
            onPreview: (preview) => {
                payload.previews.push(preview.frameIndex);
            },
            onProgress: (progress) => {
                payload.progress.push(progress);
            },
            signal: abortController.signal,
            task
        });

        payload.ok = true;
        payload.outputPath = result.outputPath;
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
