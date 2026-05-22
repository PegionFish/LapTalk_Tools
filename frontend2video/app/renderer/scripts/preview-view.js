(function () {
    function createPreviewView(elements) {
        const {
            currentTaskName,
            currentTaskStatus,
            outputDirectory,
            previewCanvasSize,
            previewEmpty,
            previewFrame,
            previewImage,
            previewStage,
            previewThemeSize,
            progressBar,
            progressCounts,
            progressPercent,
            progressTitle,
            statusMessage
        } = elements;

        function render(state) {
            const activeTask = getActiveTask(state);
            if (!activeTask) {
                currentTaskName.textContent = "未选择任务";
                currentTaskStatus.textContent = "idle";
                outputDirectory.textContent = "未选择任务";
                previewCanvasSize.textContent = "-";
                previewThemeSize.textContent = "-";
                progressTitle.textContent = "进度：等待导出";
                progressCounts.textContent = "0 / 0 帧";
                progressPercent.textContent = "0%";
                progressBar.style.width = "0%";
                statusMessage.textContent = state.statusMessage || "准备就绪";
                fitPreviewStage(previewFrame, previewStage, 16 / 9);
                previewImage.style.display = "none";
                previewEmpty.style.display = "block";
                return;
            }

            currentTaskName.textContent = getFileName(activeTask.pagePath);
            currentTaskStatus.textContent = activeTask.status;
            outputDirectory.textContent = activeTask.outputDirectory;
            progressTitle.textContent = `进度：${getFileName(activeTask.pagePath)}`;
            progressCounts.textContent = `${activeTask.progress.currentFrame} / ${activeTask.progress.totalFrames} 帧`;
            progressPercent.textContent = `${activeTask.progress.percent}%`;
            progressBar.style.width = `${Math.max(0, Math.min(100, activeTask.progress.percent))}%`;
            statusMessage.textContent = state.statusMessage || getStatusMessage(activeTask);

            const preview = state.previews[activeTask.id];
            const canvasWidth = preview && preview.canvasWidth
                ? preview.canvasWidth
                : activeTask.width;
            const canvasHeight = preview && preview.canvasHeight
                ? preview.canvasHeight
                : activeTask.height;

            previewCanvasSize.textContent = `${canvasWidth} × ${canvasHeight}`;

            if (preview && preview.themeWidth && preview.themeHeight) {
                previewThemeSize.textContent = `${preview.themeWidth} × ${preview.themeHeight} · ${formatScale(preview.renderScale)}`;
            } else {
                previewThemeSize.textContent = `${activeTask.width} × ${activeTask.height}`;
            }

            fitPreviewStage(
                previewFrame,
                previewStage,
                canvasWidth / Math.max(canvasHeight, 1)
            );

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

    function fitPreviewStage(previewFrame, previewStage, aspectRatio) {
        const frameBounds = previewFrame.getBoundingClientRect();
        const safeAspectRatio = Number.isFinite(aspectRatio) && aspectRatio > 0
            ? aspectRatio
            : 16 / 9;

        const availableWidth = Math.max(0, frameBounds.width - 8);
        const availableHeight = Math.max(0, frameBounds.height - 8);

        if (!availableWidth || !availableHeight) {
            return;
        }

        let stageWidth = availableWidth;
        let stageHeight = stageWidth / safeAspectRatio;

        if (stageHeight > availableHeight) {
            stageHeight = availableHeight;
            stageWidth = stageHeight * safeAspectRatio;
        }

        previewStage.style.width = `${Math.max(220, Math.floor(stageWidth))}px`;
        previewStage.style.height = `${Math.max(160, Math.floor(stageHeight))}px`;
    }

    function getFileName(filePath) {
        return filePath.split(/[\\/]/).pop() || filePath;
    }

    function getStatusMessage(task) {
        if (task.error && task.error.message) {
            return task.error.message;
        }

        const statusToMessage = {
            done: "导出完成。",
            error: "导出失败。",
            idle: "等待开始。",
            ready: "准备就绪。",
            rendering: `正在 ${task.progress.stage === "encoding" ? "编码" : "抓帧"}。`,
            stopped: "渲染已停止。"
        };

        return statusToMessage[task.status] || "准备就绪。";
    }

    function formatScale(value) {
        const number = Number(value);
        if (!Number.isFinite(number) || number <= 0) {
            return "scale 1.00x";
        }

        return `scale ${number.toFixed(2)}x`;
    }

    window.RenderStudioPreviewView = {
        createPreviewView
    };
})();
