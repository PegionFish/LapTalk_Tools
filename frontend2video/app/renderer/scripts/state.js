(function () {
    function createStore(initialState) {
        let state = JSON.parse(JSON.stringify(initialState));
        const listeners = new Set();

        function getState() {
            return JSON.parse(JSON.stringify(state));
        }

        function setState(updater) {
            const nextState =
                typeof updater === "function" ? updater(getState()) : updater;
            state = JSON.parse(JSON.stringify(nextState));

            for (const listener of listeners) {
                listener(getState());
            }
        }

        function subscribe(listener) {
            listeners.add(listener);
            listener(getState());
            return () => {
                listeners.delete(listener);
            };
        }

        return {
            getState,
            setState,
            subscribe
        };
    }

    window.RenderStudioState = {
        createStore
    };
})();
