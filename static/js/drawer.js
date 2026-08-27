let closeActiveCatalogDrawer = null;

const initialiseCatalogDrawer = () => {
    const drawer = document.getElementById("filterDrawer");
    const panel = document.getElementById("drawerPanel");
    const overlay = document.getElementById("drawerOverlay");
    const closeButton = document.getElementById("closeFiltersButton");
    const openButtons = Array.from(document.querySelectorAll("[data-filter-trigger]"));
    const permanentSidebar = window.matchMedia("(min-width: 1280px)");
    const focusableSelector = [
        "a[href]",
        "button:not([disabled])",
        "input:not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "details > summary",
        "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    if (!drawer || !panel || !overlay || drawer.dataset.initialised === "true") return;
    drawer.dataset.initialised = "true";

    let returnFocus = null;
    let closeTimer = null;
    let backgroundState = [];

    const isOpen = () => drawer.getAttribute("aria-hidden") === "false";
    const isVisible = (element) => Boolean(
        element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length)
    );

    function setTriggerState(activeTrigger = null) {
        openButtons.forEach((button) => {
            button.setAttribute("aria-expanded", button === activeTrigger ? "true" : "false");
        });
    }

    function inertBackground() {
        backgroundState = [];
        let activeBranch = drawer;

        while (activeBranch.parentElement && activeBranch !== document.body) {
            const parent = activeBranch.parentElement;
            Array.from(parent.children).forEach((sibling) => {
                if (sibling === activeBranch || !(sibling instanceof HTMLElement)) return;
                if (["SCRIPT", "STYLE", "LINK"].includes(sibling.tagName)) return;
                backgroundState.push({
                    element: sibling,
                    hadInert: sibling.hasAttribute("inert"),
                    ariaHidden: sibling.getAttribute("aria-hidden"),
                });
                sibling.setAttribute("inert", "");
                sibling.setAttribute("aria-hidden", "true");
            });
            activeBranch = parent;
        }
    }

    function restoreBackground() {
        backgroundState.forEach(({ element, hadInert, ariaHidden }) => {
            if (!hadInert) element.removeAttribute("inert");
            if (ariaHidden === null) element.removeAttribute("aria-hidden");
            else element.setAttribute("aria-hidden", ariaHidden);
        });
        backgroundState = [];
    }

    function openDrawer(trigger) {
        if (permanentSidebar.matches) return;
        if (closeTimer) window.clearTimeout(closeTimer);
        returnFocus = trigger;
        setTriggerState(trigger);
        drawer.classList.remove("hidden");
        drawer.removeAttribute("inert");
        drawer.setAttribute("aria-hidden", "false");
        inertBackground();
        document.body.classList.add("sf-scroll-locked");

        window.requestAnimationFrame(() => {
            overlay.classList.remove("opacity-0");
            panel.classList.remove("is-inline-end-closed");
        });

        (closeButton || panel).focus();
    }

    function closeDrawer({ restoreFocus = true, immediate = false } = {}) {
        if (closeTimer) window.clearTimeout(closeTimer);
        overlay.classList.add("opacity-0");
        panel.classList.add("is-inline-end-closed");
        drawer.setAttribute("aria-hidden", "true");
        drawer.setAttribute("inert", "");
        setTriggerState();
        document.body.classList.remove("sf-scroll-locked");
        restoreBackground();

        const opener = returnFocus;
        returnFocus = null;
        if (restoreFocus && opener instanceof HTMLElement && opener.isConnected && isVisible(opener)) {
            opener.focus();
        }

        const finish = () => drawer.classList.add("hidden");
        if (immediate) finish();
        else closeTimer = window.setTimeout(finish, 240);
    }

    closeActiveCatalogDrawer = (options = {}) => closeDrawer(options);

    function trapFocus(event) {
        if (event.key !== "Tab" || !isOpen()) return;
        const focusable = Array.from(drawer.querySelectorAll(focusableSelector)).filter(isVisible);
        if (!focusable.length) {
            event.preventDefault();
            panel.focus();
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    openButtons.forEach((button) => button.addEventListener("click", (event) => {
        event.stopPropagation();
        openDrawer(button);
    }));
    closeButton?.addEventListener("click", () => closeDrawer());
    overlay.addEventListener("click", () => closeDrawer());
    drawer.addEventListener("keydown", trapFocus);

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && isOpen()) closeDrawer();
    });

    permanentSidebar.addEventListener("change", (event) => {
        if (event.matches && (isOpen() || !drawer.classList.contains("hidden"))) {
            closeDrawer({ restoreFocus: false, immediate: true });
        }
    });
};

document.addEventListener("DOMContentLoaded", initialiseCatalogDrawer);
// Keep closing controls delegated as the drawer itself is replaced by Catalog
// outerHTML swaps. Capture prevents an outgoing drawer listener from retaining
// an obsolete panel/body-lock state during rapid repeated interactions.
document.addEventListener("click", (event) => {
    const closeControl = event.target.closest("#closeFiltersButton, #drawerOverlay");
    if (!closeControl || !closeActiveCatalogDrawer) return;
    event.preventDefault();
    event.stopPropagation();
    closeActiveCatalogDrawer();
}, true);
document.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target?.id === "catalog-workspace") {
        closeActiveCatalogDrawer?.({ restoreFocus: false, immediate: true });
        closeActiveCatalogDrawer = null;
    }
});
// HTMX may report the old outerHTML target during `afterSwap`; initialise after
// settlement so the current drawer is discovered rather than the removed node.
document.addEventListener("htmx:afterSettle", initialiseCatalogDrawer);

window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    closeActiveCatalogDrawer?.({ restoreFocus: false, immediate: true });
    document.body.classList.remove("sf-scroll-locked");
    document.querySelectorAll("[data-filter-trigger]").forEach((trigger) => {
        trigger.setAttribute("aria-expanded", "false");
    });
});
