const test = require("node:test");
const assert = require("node:assert/strict");

const { parseFfmpegCapabilities } = require("../app/core/ffmpeg-capabilities");

test("parseFfmpegCapabilities detects expected encoders", () => {
    const result = parseFfmpegCapabilities(`
 V..... prores_ks            Apple ProRes (iCodec Pro) (codec prores)
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder
 V....D hevc_videotoolbox    VideoToolbox HEVC Encoder
`);

    assert.deepEqual(result, {
        h264Videotoolbox: true,
        hevcVideotoolbox: true,
        libx264: true,
        proresKs: true,
        proresVideotoolbox: false
    });
});
