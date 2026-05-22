(function () {
    function createSettingsDialog(elements) {
        const {
            chooseButton,
            closeButton,
            dialog,
            ffmpegPath,
            ffmpegVersion
        } = elements;

        function bind(actions) {
            closeButton.addEventListener("click", () => {
                dialog.close();
            });

            chooseButton.addEventListener("click", async () => {
                const result = await actions.chooseFfmpeg();
                if (result && result.ok) {
                    dialog.close();
                }
            });
        }

        function render(state) {
            const settings = state.settings;
            ffmpegPath.textContent = settings.ffmpegPath || "未设置";
            ffmpegVersion.textContent = settings.ffmpegVersion || "未检测到";
        }

        function open() {
            dialog.showModal();
        }

        return {
            bind,
            open,
            render
        };
    }

    window.RenderStudioSettingsDialog = {
        createSettingsDialog
    };
})();
