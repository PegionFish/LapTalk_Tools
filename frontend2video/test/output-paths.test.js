const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
    getDefaultOutputDirectory,
    getDefaultOutputFilename
} = require("../app/core/output-paths");

test("pages directory resolves to sibling exports", () => {
    const inputPath = path.join("work", "projects", "demo", "pages", "a.html");
    const result = getDefaultOutputDirectory(inputPath);
    assert.equal(result, path.join("work", "projects", "demo", "exports"));
});

test("non-pages directory resolves to local exports", () => {
    const inputPath = path.join("work", "demo", "a.html");
    const result = getDefaultOutputDirectory(inputPath);
    assert.equal(result, path.join("work", "demo", "exports"));
});

test("output filename keeps basename and switches extension", () => {
    const result = getDefaultOutputFilename(path.join("work", "demo", "a.html"));
    assert.equal(result, "a.mov");
});
