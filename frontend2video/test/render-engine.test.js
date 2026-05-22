const test = require("node:test");
const assert = require("node:assert/strict");

const {
    resolveViewportBinding
} = require("../app/core/render-engine");

test("resolveViewportBinding scales down oversized themes proportionally", () => {
    const result = resolveViewportBinding({
        targetHeight: 1080,
        targetWidth: 1920,
        themeHeight: 1600,
        themeWidth: 2560
    });

    assert.deepEqual(result, {
        renderScale: 0.675,
        themeHeight: 1600,
        themeWidth: 2560
    });
});

test("resolveViewportBinding scales up smaller themes proportionally", () => {
    const result = resolveViewportBinding({
        targetHeight: 2160,
        targetWidth: 3840,
        themeHeight: 1080,
        themeWidth: 1920
    });

    assert.deepEqual(result, {
        renderScale: 2,
        themeHeight: 1080,
        themeWidth: 1920
    });
});

test("resolveViewportBinding falls back to target size on invalid theme metrics", () => {
    const result = resolveViewportBinding({
        targetHeight: 1440,
        targetWidth: 2560,
        themeHeight: 0,
        themeWidth: NaN
    });

    assert.deepEqual(result, {
        renderScale: 1,
        themeHeight: 1440,
        themeWidth: 2560
    });
});
