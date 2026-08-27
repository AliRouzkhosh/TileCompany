// navbar.js
const storefrontOverlayEvent = "storefront:header-overlay-open";

function announceHeaderOverlay(name, source) {
    document.dispatchEvent(new CustomEvent(storefrontOverlayEvent, { detail: { name, source } }));
}

document.addEventListener("DOMContentLoaded", () => {
    initializeShopDisclosure();
    initializeAvatarMenu();
    initializeMobileDrawer();
    initializeMobileSearch();
    initializeMobileDisclosures();
    initializeNotificationMenus();
    initializeResponsiveHeaderReset();
});

const mobileInertTargets = new Set();

function focusableElements(surface) {
    return [...surface.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => !element.hidden && !element.closest("[inert]") && element.offsetParent !== null);
}

function containFocus(surface, event) {
    if (event.key !== "Tab") return;
    const focusable = focusableElements(surface);
    if (!focusable.length) {
        event.preventDefault();
        surface.focus();
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

function setMobileBackgroundInert(activeLayer) {
    const header = activeLayer.closest("header");
    const targets = [
        document.querySelector("body > main"),
        document.querySelector("body > footer"),
        ...(header ? [...header.children].filter((child) => child !== activeLayer) : []),
    ].filter(Boolean);
    targets.forEach((element) => {
        element.inert = true;
        element.setAttribute("aria-hidden", "true");
        mobileInertTargets.add(element);
    });
    document.body.classList.add("sf-scroll-locked");
}

function releaseMobileBackground() {
    mobileInertTargets.forEach((element) => {
        element.inert = false;
        element.removeAttribute("aria-hidden");
    });
    mobileInertTargets.clear();
    document.querySelectorAll("[data-mobile-drawer-layer], [data-mobile-search-layer]").forEach((layer) => {
        if (!layer.classList.contains("is-open")) layer.inert = true;
    });
    document.body.classList.remove("sf-scroll-locked");
}

function initializeShopDisclosure() {
    const disclosure = document.querySelector("[data-shop-disclosure]");
    if (!disclosure) return;
    const triggerArea = disclosure.querySelector("[data-shop-trigger-area]");
    const trigger = disclosure.querySelector("[data-shop-trigger]");
    const panel = disclosure.querySelector("[data-shop-panel]");
    if (!triggerArea || !trigger || !panel) return;

    const openDelay = 125;
    const closeDelay = 225;
    let openTimer;
    let closeTimer;
    let hideTimer;

    function cancelTimers() {
        window.clearTimeout(openTimer);
        window.clearTimeout(closeTimer);
        window.clearTimeout(hideTimer);
    }
    function open() {
        cancelTimers();
        announceHeaderOverlay("shop", disclosure);
        panel.classList.remove("hidden");
        window.requestAnimationFrame(() => panel.classList.add("is-open"));
        trigger.setAttribute("aria-expanded", "true");
    }
    function close({ restoreFocus = false } = {}) {
        window.clearTimeout(openTimer);
        window.clearTimeout(closeTimer);
        panel.classList.remove("is-open");
        trigger.setAttribute("aria-expanded", "false");
        window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(() => panel.classList.add("hidden"), 200);
        if (restoreFocus) trigger.focus();
    }
    function scheduleOpen() {
        window.clearTimeout(closeTimer);
        openTimer = window.setTimeout(open, openDelay);
    }
    function scheduleClose() {
        window.clearTimeout(openTimer);
        closeTimer = window.setTimeout(close, closeDelay);
    }

    triggerArea.addEventListener("pointerenter", scheduleOpen);
    triggerArea.addEventListener("pointerleave", scheduleClose);
    panel.addEventListener("pointerenter", cancelTimers);
    panel.addEventListener("pointerleave", scheduleClose);
    disclosure.addEventListener("pointerleave", scheduleClose);
    trigger.addEventListener("click", () => {
        if (trigger.getAttribute("aria-expanded") === "true") close();
        else open();
    });
    disclosure.addEventListener("focusin", (event) => {
        if (triggerArea.contains(event.target) || panel.contains(event.target)) open();
    });
    disclosure.addEventListener("focusout", () => {
        window.setTimeout(() => {
            if (!disclosure.contains(document.activeElement)) close();
        }, 0);
    });
    disclosure.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && trigger.getAttribute("aria-expanded") === "true") {
            event.preventDefault();
            close({ restoreFocus: true });
        }
    });
    document.addEventListener("pointerdown", (event) => {
        if (!disclosure.contains(event.target)) close();
    });
    document.addEventListener(storefrontOverlayEvent, (event) => {
        if (event.detail.source !== disclosure) close();
    });
}

function initializeAvatarMenu() {
    const container = document.querySelector("[data-avatar-menu]");
    if (!container) return;
    const button = container.querySelector("[data-avatar-toggle]");
    const menu = container.querySelector("[data-avatar-dropdown]");
    if (!button || !menu) return;

    function close() {
        menu.classList.add("hidden");
        button.setAttribute("aria-expanded", "false");
    }
    function open() {
        announceHeaderOverlay("avatar", container);
        menu.classList.remove("hidden");
        button.setAttribute("aria-expanded", "true");
    }
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (menu.classList.contains("hidden")) open();
        else close();
    });
    menu.addEventListener("click", (event) => event.stopPropagation());
    document.addEventListener("click", close);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !menu.classList.contains("hidden")) {
            close();
            button.focus();
        }
    });
    document.addEventListener(storefrontOverlayEvent, (event) => {
        if (event.detail.source !== container) close();
    });
}

function initializeMobileDrawer() {
    const button = document.getElementById("mobileMenuButton");
    const layer = document.querySelector("[data-mobile-drawer-layer]");
    const drawer = document.getElementById("mobileDrawer");
    const backdrop = layer?.querySelector("[data-mobile-drawer-backdrop]");
    const closeButton = layer?.querySelector("[data-mobile-drawer-close]");
    if (!button || !layer || !drawer || !backdrop || !closeButton) return;

    function openDrawer() {
        announceHeaderOverlay("mobile-drawer", layer);
        layer.inert = false;
        layer.setAttribute("aria-hidden", "false");
        layer.classList.add("is-open");
        button.setAttribute("aria-expanded", "true");
        setMobileBackgroundInert(layer);
        window.requestAnimationFrame(() => closeButton.focus());
    }
    function closeDrawer({ restoreFocus = false } = {}) {
        if (!layer.classList.contains("is-open")) return;
        layer.classList.remove("is-open");
        layer.setAttribute("aria-hidden", "true");
        layer.inert = true;
        button.setAttribute("aria-expanded", "false");
        releaseMobileBackground();
        if (restoreFocus) button.focus();
    }
    button.addEventListener("click", openDrawer);
    closeButton.addEventListener("click", () => closeDrawer({ restoreFocus: true }));
    backdrop.addEventListener("click", () => closeDrawer({ restoreFocus: true }));
    drawer.addEventListener("keydown", (event) => containFocus(drawer, event));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && layer.classList.contains("is-open")) {
            event.preventDefault();
            closeDrawer({ restoreFocus: true });
        }
    });
    document.addEventListener(storefrontOverlayEvent, (event) => {
        if (!layer.contains(event.detail.source)) closeDrawer();
    });
}

function initializeMobileSearch() {
    const button = document.getElementById("mobileSearchButton");
    const layer = document.querySelector("[data-mobile-search-layer]");
    const panel = layer?.querySelector("[data-mobile-search-panel]");
    const input = layer?.querySelector("[data-search-input]");
    const suggestions = layer?.querySelector("[data-search-suggestions]");
    const backdrop = layer?.querySelector("[data-mobile-search-backdrop]");
    const closeButton = layer?.querySelector("[data-mobile-search-close]");
    if (!button || !layer || !panel || !input || !suggestions || !backdrop || !closeButton) return;

    function openSearch() {
        announceHeaderOverlay("mobile-search", layer);
        layer.inert = false;
        layer.setAttribute("aria-hidden", "false");
        layer.classList.add("is-open");
        button.setAttribute("aria-expanded", "true");
        setMobileBackgroundInert(layer);
        window.requestAnimationFrame(() => input.focus());
    }
    function closeSearch({ restoreFocus = false } = {}) {
        if (!layer.classList.contains("is-open")) return;
        layer.classList.remove("is-open");
        layer.setAttribute("aria-hidden", "true");
        layer.inert = true;
        button.setAttribute("aria-expanded", "false");
        input.setAttribute("aria-expanded", "false");
        suggestions.classList.add("hidden");
        releaseMobileBackground();
        if (restoreFocus) button.focus();
    }
    button.addEventListener("click", openSearch);
    closeButton.addEventListener("click", () => closeSearch({ restoreFocus: true }));
    backdrop.addEventListener("click", () => closeSearch({ restoreFocus: true }));
    panel.addEventListener("keydown", (event) => containFocus(panel, event));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && layer.classList.contains("is-open")) {
            event.preventDefault();
            closeSearch({ restoreFocus: true });
        }
    });
    document.addEventListener(storefrontOverlayEvent, (event) => {
        if (!layer.contains(event.detail.source)) closeSearch();
    });
}

function initializeMobileDisclosures() {
    document.querySelectorAll("[data-mobile-disclosure]").forEach((button) => {
        const panelId = button.getAttribute("aria-controls");
        const panel = panelId ? document.getElementById(panelId) : null;
        if (!panel) return;
        button.addEventListener("click", () => {
            const willOpen = button.getAttribute("aria-expanded") !== "true";
            button.setAttribute("aria-expanded", String(willOpen));
            panel.setAttribute("aria-hidden", String(!willOpen));
            panel.inert = !willOpen;
            panel.classList.toggle("is-open", willOpen);
        });
    });
}

function initializeResponsiveHeaderReset() {
    const desktopQuery = window.matchMedia("(min-width: 64rem)");
    desktopQuery.addEventListener("change", () => {
        announceHeaderOverlay("responsive-reset", document.documentElement);
        document.querySelectorAll("[data-mobile-disclosure]").forEach((button) => {
            const panelId = button.getAttribute("aria-controls");
            const panel = panelId ? document.getElementById(panelId) : null;
            button.setAttribute("aria-expanded", "false");
            if (panel) {
                panel.classList.remove("is-open");
                panel.setAttribute("aria-hidden", "true");
                panel.inert = true;
            }
        });
        if (document.activeElement?.closest("[data-mobile-drawer-layer], [data-mobile-search-layer]")) {
            document.activeElement.blur();
        }
        releaseMobileBackground();
    });
}

function initializeNotificationMenus() {
    const menus = document.querySelectorAll("[data-notification-menu]");
    menus.forEach((container) => {
        const button = container.querySelector("[data-notification-toggle]");
        const dropdown = container.querySelector("[data-notification-dropdown]");
        if (!button || !dropdown) return;
        function close() {
            dropdown.classList.add("hidden");
            button.setAttribute("aria-expanded", "false");
        }
        function open() {
            announceHeaderOverlay("notifications", container);
            dropdown.classList.remove("hidden");
            button.setAttribute("aria-expanded", "true");
        }
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            if (dropdown.classList.contains("hidden")) open();
            else close();
        });
        dropdown.addEventListener("click", (event) => event.stopPropagation());
        document.addEventListener("click", close);
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !dropdown.classList.contains("hidden")) {
                close();
                button.focus();
            }
        });
        document.addEventListener(storefrontOverlayEvent, (event) => {
            if (event.detail.source !== container) close();
        });
    });
}
