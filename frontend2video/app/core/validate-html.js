const fs = require("node:fs");
const path = require("node:path");

function isHtmlFilePath(filePath) {
    const extension = path.extname(filePath).toLowerCase();
    return extension === ".html" || extension === ".htm";
}

function normalizeHtmlPath(filePath) {
    return path.resolve(filePath);
}

function getPathIdentity(filePath) {
    const normalized = normalizeHtmlPath(filePath);
    return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function validateHtmlPath(filePath) {
    if (!filePath || typeof filePath !== "string") {
        return {
            ok: false,
            error: {
                code: "HTML_PATH_INVALID",
                message: "HTML path must be a non-empty string.",
                details: ""
            }
        };
    }

    const normalizedPath = normalizeHtmlPath(filePath);
    if (!isHtmlFilePath(normalizedPath)) {
        return {
            ok: false,
            error: {
                code: "HTML_EXTENSION_INVALID",
                message: "Only .html and .htm files are supported.",
                details: normalizedPath
            }
        };
    }

    if (!fs.existsSync(normalizedPath)) {
        return {
            ok: false,
            error: {
                code: "HTML_FILE_NOT_FOUND",
                message: "HTML file does not exist.",
                details: normalizedPath
            }
        };
    }

    return {
        ok: true,
        normalizedPath,
        identity: getPathIdentity(normalizedPath)
    };
}

module.exports = {
    getPathIdentity,
    isHtmlFilePath,
    normalizeHtmlPath,
    validateHtmlPath
};
