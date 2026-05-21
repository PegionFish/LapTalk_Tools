const { pathToFileURL } = require("node:url");

function buildRenderUrl(pagePath, options) {
    const {
        renderHeight,
        renderMs = 0,
        renderSettleMs = 0,
        renderWidth
    } = options;

    const url = pathToFileURL(pagePath);
    url.searchParams.set("laptalkRender", "1");
    url.searchParams.set("renderMs", String(renderMs));
    url.searchParams.set("renderWidth", String(renderWidth));
    url.searchParams.set("renderHeight", String(renderHeight));
    url.searchParams.set("renderSettleMs", String(renderSettleMs));
    return url.toString();
}

function getPreparePageScript(renderTimeMs, settleMs) {
    return `
        (async () => {
            if (document.fonts && document.fonts.ready) {
                try {
                    await document.fonts.ready;
                } catch {}
            }

            document.documentElement.setAttribute("data-render-mode", "1");
            document.documentElement.style.setProperty("--render-ms", String(${renderTimeMs}));

            if (typeof window.__setRenderTime === "function") {
                await window.__setRenderTime(${renderTimeMs});
            } else if (document.getAnimations) {
                for (const animation of document.getAnimations()) {
                    try {
                        animation.pause();
                        animation.currentTime = ${renderTimeMs};
                    } catch {}
                }
            }

            await new Promise((resolve) =>
                requestAnimationFrame(() => requestAnimationFrame(resolve))
            );

            if (${settleMs} > 0) {
                await new Promise((resolve) => setTimeout(resolve, ${settleMs}));
            }
        })();
    `;
}

module.exports = {
    buildRenderUrl,
    getPreparePageScript
};
