/*
 * Small global behaviors only.
 * Feature modules are loaded independently by base.html or their owning page.
 */
const closeAccountNavigationSelector = (surface, { restoreFocus = false } = {}) => {
    const button = surface?.querySelector("[data-account-nav-selector-button]");
    const menu = surface?.querySelector("[data-account-nav-selector-menu]");
    if (!button || !menu) return;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (restoreFocus) button.focus();
};

document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-account-nav-selector-button]");
    if (!button) return;
    const surface = button.closest("[data-account-nav-selector]");
    const menu = surface?.querySelector("[data-account-nav-selector-menu]");
    if (!surface || !menu) return;
    if (button.getAttribute("aria-expanded") === "true") closeAccountNavigationSelector(surface);
    else {
        menu.hidden = false;
        button.setAttribute("aria-expanded", "true");
    }
});

document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const surface = event.target.closest?.("[data-account-nav-selector]");
    if (surface?.querySelector("[data-account-nav-selector-button]")?.getAttribute("aria-expanded") === "true") {
        event.preventDefault();
        closeAccountNavigationSelector(surface, { restoreFocus: true });
    }
});

document.addEventListener("pointerdown", (event) => {
    document.querySelectorAll("[data-account-nav-selector]").forEach((surface) => {
        if (!surface.contains(event.target)) closeAccountNavigationSelector(surface);
    });
});

document.addEventListener("htmx:beforeRequest", (event) => {
    if (event.detail.requestConfig?.elt?.closest?.('[data-htmx-intent="navigation"]')) {
        closeAccountNavigationSelector(event.detail.requestConfig.elt.closest("[data-account-nav-selector]"));
    }
});

document.addEventListener("DOMContentLoaded", () => {

    const logoutDialog = document.querySelector("[data-logout-dialog]");
    if (logoutDialog) {
        let logoutOpener = null;
        const closeLogoutDialog = ({ restoreFocus = true } = {}) => {
            if (!logoutDialog.open) return;
            logoutDialog.close();
            if (restoreFocus && logoutOpener?.isConnected) logoutOpener.focus();
            logoutOpener = null;
        };

        document.querySelectorAll("[data-logout-trigger]").forEach((trigger) => {
            trigger.addEventListener("click", () => {
                logoutOpener = trigger;
                logoutDialog.showModal();
                logoutDialog.querySelector("[data-logout-cancel]")?.focus();
            });
        });
        logoutDialog.querySelector("[data-logout-cancel]")?.addEventListener("click", () => closeLogoutDialog());
        logoutDialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            closeLogoutDialog();
        });
        logoutDialog.addEventListener("click", (event) => {
            if (event.target === logoutDialog) closeLogoutDialog();
        });
    }

    document.querySelectorAll("[data-dismiss]").forEach(button => {

        button.addEventListener("click", () => {

            button.closest(".alert").remove();

        });

    });

    const formatFileSize = (bytes) => {
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    document.querySelectorAll("[data-file-upload]").forEach((upload) => {
        const input = upload.querySelector("input[type='file']");
        const selection = upload.querySelector("[data-file-upload-selection]");
        const removeButton = upload.querySelector("[data-file-upload-remove]");
        if (!input || !selection) return;

        const updateSelection = () => {
            const files = Array.from(input.files || []);
            selection.textContent = files.length
                ? `${selection.dataset.selected}: ${files.map(file => `${file.name} (${formatFileSize(file.size)})`).join(", ")}`
                : selection.dataset.empty;
            if (removeButton) removeButton.hidden = files.length === 0;
        };

        input.addEventListener("change", updateSelection);
        removeButton?.addEventListener("click", () => {
            input.value = "";
            updateSelection();
        });
    });

});
