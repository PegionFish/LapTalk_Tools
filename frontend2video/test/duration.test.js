const test = require("node:test");
const assert = require("node:assert/strict");

const {
    alignDurationToFrames,
    resolveDurationSecondsFromHtmlText
} = require("../app/core/duration");

test("manual duration has highest priority", () => {
    const result = resolveDurationSecondsFromHtmlText(
        '<meta name="laptalk:duration-seconds" content="6.75">',
        12
    );

    assert.deepEqual(result, {
        seconds: 12,
        source: "manual"
    });
});

test("html metadata is used when manual duration is absent", () => {
    const result = resolveDurationSecondsFromHtmlText(
        '<meta name="laptalk:duration-seconds" content="6.75">'
    );

    assert.deepEqual(result, {
        seconds: 6.75,
        source: "html-meta"
    });
});

test("duration aligns to nearest frame", () => {
    const result = alignDurationToFrames(6.75, 24);

    assert.equal(result.totalFrames, 162);
    assert.equal(result.frameAlignedDurationSeconds, 6.75);
});
