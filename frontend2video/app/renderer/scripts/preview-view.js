(function () {
    function createPreviewView(elements) {
        const {
            currentTaskName,
            currentTaskStatus,
            outputDirectory,
            previewEmpty,
            previewImage,
            progressBar,
            progressCounts,
            progressPercent,
            progressTitle,
            statusMessage
        } = elements;

        function render(state) {
            const activeTask = getActiveTask(state);
            if (!activeTask) {
                currentTaskName.textContent = "?????";
                currentTaskStatus.textContent = "idle";
                outputDirectory.textContent = "?????";
                progressTitle.textContent = "???????";
                progressCounts.textContent = "0 / 0 ?";
                progressPercent.textContent = "0%";
                progressBar.style.width = "0%";
                statusMessage.textContent = state.statusMessage || "????";
                previewImage.style.display = "none";
                previewEmpty.style.display = "block";
                return;
            }

            currentTaskName.textContent = getFileName(activeTask.pagePath);
            currentTaskStatus.textContent = activeTask.status;
            outputDirectory.textContent = activeTask.outputDirectory;
            progressTitle.textContent = `???${getFileName(activeTask.pagePath)}`;
            progressCounts.textContent = `${activeTask.progress.currentFrame} / ${activeTask.progress.totalFrames} ?`;
            progressPercent.textContent = `${activeTask.progress.percent}%`;
            progressBar.style.width = `${Math.max(0, Math.min(100, activeTask.progress.percent))}%`;
            statusMessage.textContent = state.statusMessage || getStatusMessage(activeTask);

            const preview = state.previews[activeTask.id];
            if (preview && preview.previewDataUrl) {
                previewImage.src = preview.previewDataUrl;
                previewImage.style.display = "block";
                previewEmpty.style.display = "none";
            } else {
                previewImage.style.display = "none";
                previewEmpty.style.display = "block";
            }
        }

        return {
            render
        };
    }

    function getActiveTask(state) {
        return (
            state.queue.find((task) => task.status === "rendering") ||
            state.queue.find((task) => task.id === state.selectedTaskId) ||
            null
        );
    }

    function getFileName(filePath) {
        return filePath.split(/[\\/]/).pop() || filePath;
    }

    function getStatusMessage(task) {
        if (task.error && task.error.message) {
            return task.error.message;
        }

        const statusToMessage = {
            done: "?????",
            error: "?????",
            idle: "?????",
            ready: "?????",
            rendering: `?? ${task.progress.stage === "encoding" ? "??" : "??"}?`,
            stopped: "??????"
        };

        return statusToMessage[task.status] || "?????";
    }

    window.RenderStudioPreviewView = {
        createPreviewView
    };
})();
