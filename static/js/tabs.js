document.addEventListener("DOMContentLoaded", () => {
    const tabsRoot = document.querySelector("[data-product-tabs]");
    if (!tabsRoot) return;

    const buttons = Array.from(tabsRoot.querySelectorAll('[role="tab"]'));
    const panels = Array.from(tabsRoot.querySelectorAll('[role="tabpanel"]'));
    if (!buttons.length || !panels.length) return;

    function activateTab(tabId, {focus = false, updateHash = false, ensureVisible = false} = {}) {
        const activeButton = buttons.find((button) => button.dataset.tab === tabId);
        if (!activeButton) return false;

        buttons.forEach((button) => {
            const active = button === activeButton;
            button.setAttribute("aria-selected", String(active));
            button.tabIndex = active ? 0 : -1;
            button.classList.toggle("border-brand", active);
            button.classList.toggle("text-text-primary", active);
            button.classList.toggle("font-semibold", active);
            button.classList.toggle("border-transparent", !active);
            button.classList.toggle("text-text-muted", !active);
        });

        panels.forEach((panel) => {
            const active = panel.id === activeButton.getAttribute("aria-controls");
            panel.hidden = !active;
            panel.classList.toggle("hidden", !active);
            panel.classList.toggle("opacity-0", !active);
            panel.classList.toggle("opacity-100", active);
        });

        if (updateHash) history.replaceState(null, "", `#${tabId}`);
        if (ensureVisible) {
            activeButton.scrollIntoView({
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
                block: "nearest",
                inline: "nearest",
            });
        }
        if (focus) activeButton.focus();
        return true;
    }

    buttons.forEach((button, index) => {
        button.addEventListener("click", () => {
            activateTab(button.dataset.tab, {updateHash: true, ensureVisible: true});
        });

        button.addEventListener("keydown", (event) => {
            const rtl = getComputedStyle(button).direction === "rtl";
            let targetIndex = null;
            if (event.key === "Home") targetIndex = 0;
            if (event.key === "End") targetIndex = buttons.length - 1;
            if (event.key === "ArrowRight") {
                targetIndex = (index + (rtl ? -1 : 1) + buttons.length) % buttons.length;
            }
            if (event.key === "ArrowLeft") {
                targetIndex = (index + (rtl ? 1 : -1) + buttons.length) % buttons.length;
            }
            if (targetIndex === null) return;
            event.preventDefault();
            activateTab(buttons[targetIndex].dataset.tab, {
                focus: true,
                updateHash: true,
                ensureVisible: true,
            });
        });
    });

    function activateTabFromHash({scroll = false} = {}) {
        const hash = window.location.hash.slice(1);
        if (!hash || !activateTab(hash, {ensureVisible: scroll})) return false;
        if (scroll) {
            requestAnimationFrame(() => {
                document.getElementById(hash)?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                });
            });
        }
        return true;
    }

    window.addEventListener("hashchange", () => {
        activateTabFromHash({scroll: true});
    });

    if (!activateTabFromHash({scroll: Boolean(window.location.hash)})) {
        activateTab(buttons[0].dataset.tab);
    }
});
