const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const { validateHtmlPath } = require("../app/core/validate-html");

test("validateHtmlPath accepts existing html files", async () => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), "frontend2video-"));
    const filePath = path.join(directory, "page.html");
    await fs.writeFile(filePath, "<html></html>", "utf8");

    const result = validateHtmlPath(filePath);
    assert.equal(result.ok, true);
    assert.equal(result.normalizedPath, path.resolve(filePath));

    await fs.rm(directory, { force: true, recursive: true });
});

test("validateHtmlPath rejects unsupported file extensions", () => {
    const result = validateHtmlPath("C:/work/page.txt");
    assert.equal(result.ok, false);
    assert.equal(result.error.code, "HTML_EXTENSION_INVALID");
});
