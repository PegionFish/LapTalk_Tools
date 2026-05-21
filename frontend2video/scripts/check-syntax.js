const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const rootDirectory = path.resolve(__dirname, "..");
const ignoredDirectories = new Set([
    ".git",
    "node_modules",
    "dist",
    "coverage",
    "out"
]);

function collectJavaScriptFiles(directory, files) {
    const entries = fs.readdirSync(directory, { withFileTypes: true });

    for (const entry of entries) {
        const absolutePath = path.join(directory, entry.name);

        if (entry.isDirectory()) {
            if (!ignoredDirectories.has(entry.name)) {
                collectJavaScriptFiles(absolutePath, files);
            }
            continue;
        }

        if (absolutePath.endsWith(".js")) {
            files.push(absolutePath);
        }
    }
}

const files = [];
collectJavaScriptFiles(rootDirectory, files);

let hasFailure = false;

for (const filePath of files) {
    const result = spawnSync(process.execPath, ["--check", filePath], {
        cwd: rootDirectory,
        encoding: "utf8"
    });

    if (result.status !== 0) {
        hasFailure = true;
        process.stderr.write(`Syntax check failed: ${filePath}\n`);
        if (result.stderr) {
            process.stderr.write(`${result.stderr}\n`);
        }
    }
}

if (hasFailure) {
    process.exitCode = 1;
} else {
    process.stdout.write(`Syntax check passed for ${files.length} file(s).\n`);
}
