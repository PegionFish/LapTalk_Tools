function createEmptyCapabilities() {
    return {
        proresKs: false,
        libx264: false,
        h264Videotoolbox: false,
        hevcVideotoolbox: false,
        proresVideotoolbox: false
    };
}

function parseFfmpegCapabilities(encodersOutput) {
    const capabilities = createEmptyCapabilities();
    const lines = String(encodersOutput || "").split(/\r?\n/);

    for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        const encoderName = parts[1];
        if (!encoderName) {
            continue;
        }

        if (encoderName === "prores_ks") {
            capabilities.proresKs = true;
        }
        if (encoderName === "libx264") {
            capabilities.libx264 = true;
        }
        if (encoderName === "h264_videotoolbox") {
            capabilities.h264Videotoolbox = true;
        }
        if (encoderName === "hevc_videotoolbox") {
            capabilities.hevcVideotoolbox = true;
        }
        if (encoderName === "prores_videotoolbox") {
            capabilities.proresVideotoolbox = true;
        }
    }

    return capabilities;
}

module.exports = {
    createEmptyCapabilities,
    parseFfmpegCapabilities
};
