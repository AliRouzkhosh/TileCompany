(() => {
    const scrollTolerance = 8;

    const targetIdFor = (event) => {
        const target = event.detail.target || event.detail.requestConfig?.target;
        if (typeof target === "string") return target.replace(/^#/, "");
        return target?.id || "";
    };

    const isHistoryRestore = (requestConfig) => (
        requestConfig?.headers?.["HX-History-Restore-Request"] === "true"
    );

    document.addEventListener("htmx:beforeRequest", (event) => {
        const requestConfig = event.detail.requestConfig;
        if (isHistoryRestore(requestConfig)) return;
        const paginationLink = requestConfig?.elt?.closest?.("[data-pagination-link]");
        requestConfig.scrollStability = {
            previousScrollY: window.scrollY,
            targetId: targetIdFor(event),
            pagination: Boolean(paginationLink),
        };
    });

    document.addEventListener("htmx:beforeSwap", (event) => {
        const responseUrl = event.detail.xhr.responseURL;
        const targetId = targetIdFor(event);
        if (targetId === "auth-workspace") return;
        if (!responseUrl || !new URL(responseUrl, window.location.href).pathname.includes("/login")) return;
        event.detail.shouldSwap = false;
        window.location.assign(responseUrl);
    });

    document.addEventListener("htmx:afterSwap", (event) => {
        window.lucide?.createIcons();

        const requestConfig = event.detail.requestConfig;
        if (requestConfig?.elt?.closest?.('[data-htmx-intent="navigation"]')) {
            const heading = document.querySelector("#account-workspace h1");
            if (heading) {
                heading.tabIndex = -1;
                heading.focus({ preventScroll: true });
            }
        }
    });

    document.addEventListener("htmx:afterSettle", (event) => {
        const requestConfig = event.detail.requestConfig;
        const state = requestConfig?.scrollStability;
        if (!state || isHistoryRestore(requestConfig)) return;

        if (state.pagination) {
            const workspace = state.targetId && document.getElementById(state.targetId);
            const scrollTarget = workspace?.matches?.("[data-pagination-scroll-target]")
                ? workspace
                : document.querySelector("[data-pagination-scroll-target]");
            scrollTarget?.scrollIntoView({ behavior: "auto", block: "start" });
            return;
        }

        const maxScrollY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        if (state.previousScrollY <= maxScrollY + scrollTolerance) {
            window.scrollTo({ top: state.previousScrollY, behavior: "auto" });
            return;
        }

        const workspace = state.targetId && document.getElementById(state.targetId);
        if (workspace) {
            workspace.classList.add("scroll-mt-24");
            workspace.scrollIntoView({ behavior: "auto", block: "start" });
        }
    });
})();
