(function () {
    function createQueueView(options) {
        const {
            container,
            countElement,
            onSelect
        } = options;

        function render(state) {
            const queue = state.queue;
            countElement.textContent = `${queue.length} ?`;
            container.innerHTML = "";

            if (!queue.length) {
                const empty = document.createElement("div");
                empty.className = "empty-state";
                empty.textContent = "???????? HTML ???????? HTML??";
                container.appendChild(empty);
                return;
            }

            for (const task of queue) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = `queue-item${
                    task.id === state.selectedTaskId ? " active" : ""
                }`;
                button.addEventListener("click", () => {
                    onSelect(task.id);
                });

                const titleRow = document.createElement("div");
                titleRow.className = "queue-item-title";

                const title = document.createElement("strong");
                title.textContent = getFileName(task.pagePath);

                const badge = document.createElement("span");
                badge.className = `status-badge status-${task.status}`;
                badge.textContent = task.status;

                titleRow.append(title, badge);

                const metaRow = document.createElement("div");
                metaRow.className = "queue-item-meta";
                metaRow.textContent = `${task.width}x${task.height} ? ${task.fps}fps ? ${formatSeconds(task.durationSeconds)}s`;

                const footerRow = document.createElement("div");
                footerRow.className = "queue-item-footer";

                const durationSource = document.createElement("span");
                durationSource.textContent = `?????${task.durationSource}`;

                const progress = document.createElement("span");
                progress.textContent = `${task.progress.currentFrame}/${task.progress.totalFrames} ?`;

                footerRow.append(durationSource, progress);
                button.append(titleRow, metaRow, footerRow);
                container.appendChild(button);
            }
        }

        return {
            render
        };
    }

    function formatSeconds(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return "0";
        }

        return number % 1 === 0 ? String(number) : number.toFixed(2);
    }

    function getFileName(filePath) {
        return filePath.split(/[\\/]/).pop() || filePath;
    }

    window.RenderStudioQueueView = {
        createQueueView
    };
})();
