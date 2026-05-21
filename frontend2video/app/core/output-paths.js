const path = require("node:path");

const {
    DEFAULT_OUTPUT_EXTENSION
} = require("./constants");

function getDefaultOutputDirectory(pagePath) {
    const pageDirectory = path.dirname(pagePath);
    const parent = path.basename(pageDirectory).toLowerCase();

    if (parent === "pages") {
        return path.join(path.dirname(pageDirectory), "exports");
    }

    return path.join(pageDirectory, "exports");
}

function getDefaultOutputFilename(pagePath, extension = DEFAULT_OUTPUT_EXTENSION) {
    const parsed = path.parse(pagePath);
    return `${parsed.name}${extension}`;
}

module.exports = {
    getDefaultOutputDirectory,
    getDefaultOutputFilename
};
