(function () {
    const { createStore } = window.RenderStudioState;
    const { createQueueView } = window.RenderStudioQueueView;
    const { createPreviewView } = window.RenderStudioPreviewView;
    const { createSettingsDialog } = window.RenderStudioSettingsDialog;

    const store = createStore({
        appVersion: "0.0.0",
        previews: {},
        queue: [],
        selectedTaskId: "",
        settings: {
            defaultFps: 60,
            defaultHeight: 2160,
            defaultWidth: 3840,
            ffmpegPath: "",
            ffmpegVersion: "",
            lastOutputDirectory: ""
        },
        statusMessage: "????"
    });

    const requestedPreviewIds = new Set();

    const elements = {
        appVersion: document.getElementById("app-version"),
        browseOutputButton: document.getElementById("browse-output-button"),
        clearButton: document.getElementById("clear-button"),
        currentTaskName: document.getElementById("current-task-name"),
        currentTaskStatus: document.getElementById("current-task-status"),
        dropTarget: document.getElementById("drop-target"),
        exportButton: document.getElementById("export-button"),
        ffmpegVersion: document.getElementById("ffmpeg-version"),
        fpsSelect: document.getElementById("fps-select"),
        heightInput: document.getElementById("height-input"),
        importButton: document.getElementById("import-button"),
        openOutputButton: document.getElementById("open-output-button"),
        outputDirectory: document.getElementById("output-directory"),
        previewEmpty: document.getElementById("preview-empty"),
        previewImage: document.getElementById("preview-image"),
        progressBar: document.getElementById("progress-bar"),
        progressCounts: document.getElementById("progress-counts"),
        progressPercent: document.getElementById("progress-percent"),
        progressTitle: document.getElementById("progress-title"),
        queueCount: document.getElementById("queue-count"),
        queueList: document.getElementById("queue-list"),
        resolutionPreset: document.getElementById("resolution-preset"),
        settingsButton: document.getElementById("settings-button"),
        settingsCloseButton: document.getElementById("settings-close-button"),
        settingsDialog: document.getElementById("settings-dialog"),
        settingsFfmpegPath: document.getElementById("settings-ffmpeg-path"),
        settingsFfmpegVersion: document.getElementById("settings-ffmpeg-version"),
        statusMessage: document.getElementById("status-message"),
        stopButton: document.getElementById("stop-button"),
        widthInput: document.getElementById("width-input"),
        chooseFfmpegButton: document.getElementById("choose-ffmpeg-button")
    };

    const queueView = createQueueView({
        container: elements.queueList,
        countElement: elements.queueCount,
        onSelect(taskId) {
            updateSelection(taskId);
        }
    });

    const previewView = createPreviewView({
        currentTaskName: elements.currentTaskName,
        currentTaskStatus: elements.currentTaskStatus,
        outputDirectory: elements.outputDirectory,
        previewEmpty: elements.previewEmpty,
        previewImage: elements.previewImage,
        progressBar: elements.progressBar,
        progressCounts: elements.progressCounts,
        progressPercent: elements.progressPercent,
        progressTitle: elements.progressTitle,
        statusMessage: elements.statusMessage
    });

    const settingsDialog = createSettingsDialog({
        chooseButton: elements.chooseFfmpegButton,
        closeButton: elements.settingsCloseButton,
        dialog: elements.settingsDialog,
        ffmpegPath: elements.settingsFfmpegPath,
        ffmpegVersion: elements.settingsFfmpegVersion
    });

    settingsDialog.bind({
        async chooseFfmpeg() {
            const result = await window.frontend2video.settings.setFfmpegPath();
            if (result && result.ok && result.settings) {
                applySettings(result.settings);
                setStatusMessage("FFmpeg ??????");
                return result;
            }

            if (result && result.error) {
                setStatusMessage(result.error.message);
            }

            return result;
        }
    });

    populatePresetOptions();
    bindEvents();
    bindIpcEvents();

    store.subscribe((state) => {
        queueView.render(state);
        previewView.render(state);
        settingsDialog.render(state);
        renderFooter(state);
        requestPreviewIfNeeded(state);
        updateButtonStates(state);
    });

    void bootstrap();

    async function bootstrap() {
        const data = await window.frontend2video.app.getBootstrap();
        store.setState((state) => ({
            ...state,
            appVersion: data.appVersion,
            queue: data.queue,
            selectedTaskId: data.queue[0] ? data.queue[0].id : "",
            settings: data.settings
        }));
        syncControlsWithSettings(data.settings);
    }

    function bindEvents() {
        elements.importButton.addEventListener("click", async () => {
            const result = await window.frontend2video.dialog.importHtml();
            handleAddPathsResult(result);
        });

        elements.clearButton.addEventListener("click", async () => {
            await window.frontend2video.queue.clear();
            requestedPreviewIds.clear();
            store.setState((state) => ({
                ...state,
                previews: {},
                queue: [],
                selectedTaskId: "",
                statusMessage: "??????"
            }));
        });

        elements.exportButton.addEventListener("click", async () => {
            const result = await window.frontend2video.render.start();
            if (result.accepted) {
                setStatusMessage("??????");
                return;
            }

            if (result.error) {
                setStatusMessage(result.error.message);
            }
        });

        elements.stopButton.addEventListener("click", async () => {
            const result = await window.frontend2video.render.stop();
            if (result.accepted) {
                setStatusMessage("???????");
            }
        });

        elements.openOutputButton.addEventListener("click", async () => {
            const selectedTask = getSelectedTask(store.getState());
            if (!selectedTask) {
                return;
            }
            await window.frontend2video.output.openDirectory(selectedTask.id);
        });

        elements.browseOutputButton.addEventListener("click", async () => {
            const selectedTask = getSelectedTask(store.getState());
            if (!selectedTask) {
                return;
            }

            const result = await window.frontend2video.output.chooseDirectory(
                selectedTask.id
            );
            if (result.ok) {
                setStatusMessage("????????");
            }
        });

        elements.settingsButton.addEventListener("click", () => {
            settingsDialog.open();
        });

        elements.resolutionPreset.addEventListener("change", async () => {
            const [width, height] = elements.resolutionPreset.value.split("x").map(Number);
            elements.widthInput.value = String(width);
            elements.heightInput.value = String(height);
            await pushDefaultUpdate();
        });

        elements.fpsSelect.addEventListener("change", async () => {
            await pushDefaultUpdate();
        });

        elements.widthInput.addEventListener("change", async () => {
            await pushDefaultUpdate();
        });

        elements.heightInput.addEventListener("change", async () => {
            await pushDefaultUpdate();
        });

        bindDropTarget();
    }

    function bindDropTarget() {
        const target = elements.dropTarget;
        const markActive = (isActive) => {
            target.classList.toggle("drop-active", isActive);
        };

        ["dragenter", "dragover"].forEach((eventName) => {
            target.addEventListener(eventName, (event) => {
                event.preventDefault();
                markActive(true);
            });
        });

        ["dragleave", "drop"].forEach((eventName) => {
            target.addEventListener(eventName, (event) => {
                event.preventDefault();
                markActive(false);
            });
        });

        target.addEventListener("drop", async (event) => {
            const paths = Array.from(event.dataTransfer.files || [])
                .map((file) => file.path)
                .filter(Boolean);

            if (!paths.length) {
                return;
            }

            const result = await window.frontend2video.queue.addPaths(paths);
            handleAddPathsResult(result);
        });
    }

    function bindIpcEvents() {
        window.frontend2video.events.onQueueChanged((queue) => {
            store.setState((state) => ({
                ...state,
                queue,
                selectedTaskId: chooseSelectedTaskId(state.selectedTaskId, queue)
            }));
        });

        window.frontend2video.events.onSettingsChanged((settings) => {
            applySettings(settings);
        });

        window.frontend2video.events.onRenderPreview((payload) => {
            store.setState((state) => ({
                ...state,
                previews: {
                    ...state.previews,
                    [payload.taskId]: payload
                }
            }));
        });

        window.frontend2video.events.onRenderProgress((payload) => {
            setStatusMessage(
                `${payload.stage === "encoding" ? "????" : "????"}?${payload.currentFrame}/${payload.totalFrames}`
            );
        });

        window.frontend2video.events.onRenderStatus((payload) => {
            if (payload.status === "done") {
                setStatusMessage("?????");
                return;
            }

            if (payload.status === "stopped") {
                setStatusMessage("??????");
                return;
            }

            if (payload.status === "error" && payload.error) {
                setStatusMessage(payload.error.message);
            }
        });
    }

    function renderFooter(state) {
        elements.appVersion.textContent = `v${state.appVersion}`;
        elements.ffmpegVersion.textContent =
            state.settings.ffmpegVersion || "FFmpeg ????";
    }

    function updateButtonStates(state) {
        const hasQueue = state.queue.length > 0;
        const isRendering = state.queue.some((task) => task.status === "rendering");
        elements.clearButton.disabled = !hasQueue || isRendering;
        elements.exportButton.disabled = !hasQueue || isRendering;
        elements.stopButton.disabled = !isRendering;
        elements.browseOutputButton.disabled = !getSelectedTask(state);
        elements.openOutputButton.disabled = !getSelectedTask(state);
        elements.importButton.disabled = isRendering;
    }

    function handleAddPathsResult(result) {
        if (!result) {
            return;
        }

        if (result.rejected && result.rejected.length) {
            setStatusMessage(result.rejected[0].error.message);
        } else if (result.addedTasks && result.addedTasks.length) {
            setStatusMessage(`??? ${result.addedTasks.length} ????`);
        }
    }

    function applySettings(settings) {
        store.setState((state) => ({
            ...state,
            settings
        }));
        syncControlsWithSettings(settings);
    }

    function syncControlsWithSettings(settings) {
        elements.widthInput.value = String(settings.defaultWidth);
        elements.heightInput.value = String(settings.defaultHeight);
        elements.fpsSelect.value = String(settings.defaultFps);

        const presetValue = `${settings.defaultWidth}x${settings.defaultHeight}`;
        const optionExists = Array.from(elements.resolutionPreset.options).some(
            (option) => option.value === presetValue
        );
        elements.resolutionPreset.value = optionExists ? presetValue : "custom";
    }

    async function pushDefaultUpdate() {
        const width = Number(elements.widthInput.value);
        const height = Number(elements.heightInput.value);
        const fps = Number(elements.fpsSelect.value);

        if (!Number.isInteger(width) || width <= 0) {
            setStatusMessage("?????????");
            return;
        }

        if (!Number.isInteger(height) || height <= 0) {
            setStatusMessage("?????????");
            return;
        }

        if (!Number.isInteger(fps) || fps <= 0) {
            setStatusMessage("?????????");
            return;
        }

        const result = await window.frontend2video.queue.updateDefaults({
            fps,
            height,
            width
        });

        if (result.ok) {
            setStatusMessage("??????????");
        }
    }

    function requestPreviewIfNeeded(state) {
        const selectedTask = getSelectedTask(state);
        if (!selectedTask) {
            return;
        }

        if (
            selectedTask.status === "rendering" ||
            state.previews[selectedTask.id] ||
            requestedPreviewIds.has(selectedTask.id)
        ) {
            return;
        }

        requestedPreviewIds.add(selectedTask.id);
        void window.frontend2video.preview.capture(selectedTask.id);
    }

    function updateSelection(taskId) {
        store.setState((state) => ({
            ...state,
            selectedTaskId: taskId
        }));
    }

    function chooseSelectedTaskId(currentTaskId, queue) {
        if (currentTaskId && queue.some((task) => task.id === currentTaskId)) {
            return currentTaskId;
        }

        const renderingTask = queue.find((task) => task.status === "rendering");
        if (renderingTask) {
            return renderingTask.id;
        }

        return queue[0] ? queue[0].id : "";
    }

    function getSelectedTask(state) {
        return state.queue.find((task) => task.id === state.selectedTaskId) || null;
    }

    function setStatusMessage(message) {
        store.setState((state) => ({
            ...state,
            statusMessage: message
        }));
    }

    function populatePresetOptions() {
        [
            { label: "3840 x 2160", value: "3840x2160" },
            { label: "2560 x 1440", value: "2560x1440" },
            { label: "1920 x 1080", value: "1920x1080" },
            { label: "???", value: "custom" }
        ].forEach((optionData) => {
            const option = document.createElement("option");
            option.textContent = optionData.label;
            option.value = optionData.value;
            elements.resolutionPreset.appendChild(option);
        });

        [24, 30, 60].forEach((fps) => {
            const option = document.createElement("option");
            option.textContent = `${fps} fps`;
            option.value = String(fps);
            elements.fpsSelect.appendChild(option);
        });
    }
})();
