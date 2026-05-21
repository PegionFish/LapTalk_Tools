const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const {
    MOV_ENCODER,
    MOV_PIXEL_FORMAT,
    MOV_PROFILE
} = require("./constants");
const {
    createEmptyCapabilities,
    parseFfmpegCapabilities
} = require("./ffmpeg-capabilities");

function getFfmpegBinaryName() {
    return process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg";
}

function fileExists(filePath) {
    try {
        return fs.existsSync(filePath);
    } catch {
        return false;
    }
}

function runProcess(command, args, options = {}) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, {
            cwd: options.cwd,
            windowsHide: true
        });

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
            resolve({ exitCode, stdout, stderr });
        });
    });
}

async function getFfmpegVersionLine(ffmpegPath) {
    const result = await runProcess(ffmpegPath, ["-version"]);
    if (result.exitCode !== 0) {
        throw new Error(result.stderr || "Unable to read FFmpeg version.");
    }

    return result.stdout.split(/\r?\n/)[0] ?? "";
}

async function listFfmpegEncoders(ffmpegPath) {
    const result = await runProcess(ffmpegPath, ["-hide_banner", "-encoders"]);
    if (result.exitCode !== 0) {
        throw new Error(result.stderr || "Unable to inspect FFmpeg encoders.");
    }

    return result.stdout;
}

async function validateFfmpegPath(ffmpegPath) {
    if (!ffmpegPath || typeof ffmpegPath !== "string") {
        return {
            ok: false,
            error: {
                code: "FFMPEG_NOT_FOUND",
                message: "Unable to locate FFmpeg executable.",
                details: ""
            }
        };
    }

    const isPathToken = !path.isAbsolute(ffmpegPath) && !ffmpegPath.includes(path.sep);
    if (!isPathToken && !fileExists(ffmpegPath)) {
        return {
            ok: false,
            error: {
                code: "FFMPEG_NOT_FOUND",
                message: "Unable to locate FFmpeg executable.",
                details: ffmpegPath
            }
        };
    }

    try {
        const versionLine = await getFfmpegVersionLine(ffmpegPath);
        if (!/ffmpeg version/i.test(versionLine)) {
            return {
                ok: false,
                error: {
                    code: "FFMPEG_INVALID",
                    message: "FFmpeg executable did not return a valid version line.",
                    details: versionLine
                }
            };
        }

        return {
            ok: true,
            versionLine
        };
    } catch (error) {
        return {
            ok: false,
            error: {
                code: "FFMPEG_INVALID",
                message: "Unable to execute FFmpeg.",
                details: error.message
            }
        };
    }
}

async function findExecutableOnPath(binaryName) {
    const command = process.platform === "win32" ? "where.exe" : "which";
    const result = await runProcess(command, [binaryName]);

    if (result.exitCode !== 0) {
        return "";
    }

    const firstLine = result.stdout.split(/\r?\n/).find(Boolean);
    return firstLine ? firstLine.trim() : "";
}

function getSidecarCandidatePaths(appRoot, resourcesPath) {
    const binaryName = getFfmpegBinaryName();
    const platformDirectory = process.platform;

    return [
        path.join(appRoot, "vendor", "ffmpeg", platformDirectory, binaryName),
        path.join(appRoot, "tools", "ffmpeg", platformDirectory, binaryName),
        path.join(resourcesPath || "", "bin", binaryName),
        path.join(resourcesPath || "", "ffmpeg", binaryName)
    ];
}

async function resolveAvailableFfmpeg(options) {
    const {
        appRoot,
        resourcesPath,
        savedPath
    } = options;

    const candidates = [];
    if (savedPath) {
        candidates.push({ path: savedPath, source: "settings" });
    }

    for (const candidatePath of getSidecarCandidatePaths(appRoot, resourcesPath)) {
        candidates.push({ path: candidatePath, source: "sidecar" });
    }

    const pathCandidate = await findExecutableOnPath(getFfmpegBinaryName());
    if (pathCandidate) {
        candidates.push({ path: pathCandidate, source: "path" });
    }

    for (const candidate of candidates) {
        const validation = await validateFfmpegPath(candidate.path);
        if (!validation.ok) {
            continue;
        }

        let capabilities = createEmptyCapabilities();
        try {
            const encodersOutput = await listFfmpegEncoders(candidate.path);
            capabilities = parseFfmpegCapabilities(encodersOutput);
        } catch {}

        return {
            ffmpegPath: candidate.path,
            ffmpegVersion: validation.versionLine,
            source: candidate.source,
            capabilities
        };
    }

    return {
        ffmpegPath: "",
        ffmpegVersion: "",
        source: "missing",
        capabilities: createEmptyCapabilities()
    };
}

function encodeMovFromFrames(options) {
    const {
        ffmpegPath,
        fps,
        framesDirectory,
        onSpawn,
        outputPath,
        signal
    } = options;

    const args = [
        "-y",
        "-framerate",
        String(fps),
        "-i",
        path.join(framesDirectory, "frame_%05d.png"),
        "-c:v",
        MOV_ENCODER,
        "-profile:v",
        MOV_PROFILE,
        "-pix_fmt",
        MOV_PIXEL_FORMAT,
        outputPath
    ];

    return new Promise((resolve, reject) => {
        const child = spawn(ffmpegPath, args, {
            stdio: ["ignore", "ignore", "pipe"],
            windowsHide: true
        });

        let stderr = "";
        let settled = false;

        const finalize = (callback) => {
            if (settled) {
                return;
            }
            settled = true;
            if (signal) {
                signal.removeEventListener("abort", onAbort);
            }
            callback();
        };

        const onAbort = () => {
            child.kill();
        };

        if (signal) {
            signal.addEventListener("abort", onAbort, { once: true });
        }

        if (onSpawn) {
            onSpawn(child);
        }

        child.stderr.on("data", (chunk) => {
            stderr += chunk.toString();
        });

        child.once("error", (error) => {
            finalize(() => {
                reject(error);
            });
        });

        child.once("close", (exitCode) => {
            finalize(() => {
                if (signal && signal.aborted) {
                    const error = new Error("Encoding stopped by user.");
                    error.code = "RENDER_STOPPED";
                    reject(error);
                    return;
                }

                if (exitCode !== 0) {
                    const error = new Error(stderr || "FFmpeg encoding failed.");
                    error.code = "FFMPEG_ENCODE_FAILED";
                    reject(error);
                    return;
                }

                resolve({ stderr });
            });
        });
    });
}

module.exports = {
    createEmptyCapabilities,
    encodeMovFromFrames,
    getFfmpegVersionLine,
    resolveAvailableFfmpeg,
    validateFfmpegPath
};
