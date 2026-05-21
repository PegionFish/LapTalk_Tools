const fs = require("node:fs/promises");
const path = require("node:path");

const {
    DEFAULT_FPS,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH
} = require("../../core/constants");
const {
    createEmptyCapabilities,
    resolveAvailableFfmpeg,
    validateFfmpegPath
} = require("../../core/ffmpeg");

class SettingsService {
    constructor(options) {
        this.app = options.app;
        this.appRoot = options.appRoot;
        this.capabilities = createEmptyCapabilities();
        this.settings = getDefaultSettings();
        this.settingsPath = path.join(
            this.app.getPath("userData"),
            "settings.json"
        );
    }

    async initialize() {
        const savedSettings = await this.readSettingsFile();
        this.settings = {
            ...getDefaultSettings(),
            ...sanitizeSettings(savedSettings)
        };

        await this.refreshFfmpeg();
        await this.save();
        return this.getSettings();
    }

    getSettings() {
        return {
            ...this.settings
        };
    }

    getCapabilities() {
        return {
            ...this.capabilities
        };
    }

    async updateDefaults(defaults) {
        this.settings.defaultWidth = toPositiveInteger(
            defaults.defaultWidth,
            this.settings.defaultWidth
        );
        this.settings.defaultHeight = toPositiveInteger(
            defaults.defaultHeight,
            this.settings.defaultHeight
        );
        this.settings.defaultFps = toPositiveInteger(
            defaults.defaultFps,
            this.settings.defaultFps
        );

        await this.save();
        return this.getSettings();
    }

    async setLastOutputDirectory(directoryPath) {
        this.settings.lastOutputDirectory = directoryPath || "";
        await this.save();
        return this.getSettings();
    }

    async setFfmpegPath(ffmpegPath) {
        const validation = await validateFfmpegPath(ffmpegPath);
        if (!validation.ok) {
            return validation;
        }

        this.settings.ffmpegPath = ffmpegPath;
        this.settings.ffmpegVersion = validation.versionLine;
        await this.refreshFfmpeg();
        await this.save();

        return {
            ok: true,
            settings: this.getSettings()
        };
    }

    async ensureFfmpeg() {
        if (!this.settings.ffmpegPath) {
            await this.refreshFfmpeg();
        }

        return this.getSettings();
    }

    async refreshFfmpeg() {
        const resolved = await resolveAvailableFfmpeg({
            appRoot: this.appRoot,
            resourcesPath: process.resourcesPath,
            savedPath: this.settings.ffmpegPath
        });

        this.settings.ffmpegPath = resolved.ffmpegPath;
        this.settings.ffmpegVersion = resolved.ffmpegVersion;
        this.capabilities = resolved.capabilities;
    }

    async save() {
        await fs.mkdir(path.dirname(this.settingsPath), { recursive: true });
        await fs.writeFile(
            this.settingsPath,
            `${JSON.stringify(this.settings, null, 4)}\n`,
            "utf8"
        );
    }

    async readSettingsFile() {
        try {
            const text = await fs.readFile(this.settingsPath, "utf8");
            return JSON.parse(text);
        } catch (error) {
            if (error.code === "ENOENT") {
                return {};
            }
            throw error;
        }
    }
}

function getDefaultSettings() {
    return {
        defaultFps: DEFAULT_FPS,
        defaultHeight: DEFAULT_HEIGHT,
        defaultWidth: DEFAULT_WIDTH,
        ffmpegPath: "",
        ffmpegVersion: "",
        lastOutputDirectory: ""
    };
}

function sanitizeSettings(settings) {
    return {
        defaultFps: toPositiveInteger(settings.defaultFps, DEFAULT_FPS),
        defaultHeight: toPositiveInteger(settings.defaultHeight, DEFAULT_HEIGHT),
        defaultWidth: toPositiveInteger(settings.defaultWidth, DEFAULT_WIDTH),
        ffmpegPath: typeof settings.ffmpegPath === "string" ? settings.ffmpegPath : "",
        ffmpegVersion:
            typeof settings.ffmpegVersion === "string" ? settings.ffmpegVersion : "",
        lastOutputDirectory:
            typeof settings.lastOutputDirectory === "string"
                ? settings.lastOutputDirectory
                : ""
    };
}

function toPositiveInteger(value, fallbackValue) {
    const number = Number(value);
    if (Number.isInteger(number) && number > 0) {
        return number;
    }
    return fallbackValue;
}

module.exports = {
    SettingsService
};
