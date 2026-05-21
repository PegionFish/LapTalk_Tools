const fs = require("node:fs/promises");

const {
    DEFAULT_DURATION_SECONDS
} = require("./constants");

const DURATION_META_PATTERN =
    /<meta\b[^>]*\bname\s*=\s*["']laptalk:duration-seconds["'][^>]*\bcontent\s*=\s*["'](\d+(?:\.\d+)?)["'][^>]*>/i;

async function resolveDurationSeconds({ htmlPath, manualDurationSeconds }) {
    if (Number.isFinite(manualDurationSeconds) && manualDurationSeconds > 0) {
        return { seconds: manualDurationSeconds, source: "manual" };
    }

    const html = await fs.readFile(htmlPath, "utf8");
    return resolveDurationSecondsFromHtmlText(html, manualDurationSeconds);
}

function resolveDurationSecondsFromHtmlText(html, manualDurationSeconds) {
    if (Number.isFinite(manualDurationSeconds) && manualDurationSeconds > 0) {
        return { seconds: manualDurationSeconds, source: "manual" };
    }

    const match = html.match(DURATION_META_PATTERN);
    if (match) {
        const value = Number(match[1]);
        if (Number.isFinite(value) && value > 0) {
            return { seconds: value, source: "html-meta" };
        }
    }

    return {
        seconds: DEFAULT_DURATION_SECONDS,
        source: "default-30s"
    };
}

function alignDurationToFrames(durationSeconds, fps) {
    const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 1;
    const safeDuration =
        Number.isFinite(durationSeconds) && durationSeconds > 0
            ? durationSeconds
            : DEFAULT_DURATION_SECONDS;

    const totalFrames = Math.max(1, Math.round(safeDuration * safeFps));
    return {
        declaredDurationSeconds: safeDuration,
        fps: safeFps,
        totalFrames,
        frameAlignedDurationSeconds: totalFrames / safeFps
    };
}

module.exports = {
    alignDurationToFrames,
    resolveDurationSeconds,
    resolveDurationSecondsFromHtmlText
};
