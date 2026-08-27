(() => {
    const initialise = (root = document) => {
        const form = root.querySelector?.("[data-resend-deadline]");
        if (!form || form.dataset.resendInitialised === "true") return;
        const button = form.querySelector("[data-resend-button]");
        const deadline = new Date(form.dataset.resendDeadline);
        if (!button || Number.isNaN(deadline.getTime())) return;
        form.dataset.resendInitialised = "true";
        const format = form.dataset.resendFormat || "";
        const readyLabel = form.dataset.resendReadyLabel || "";
        const tick = () => {
            const seconds = Math.max(0, Math.ceil((deadline.getTime() - Date.now()) / 1000));
            if (seconds) {
                button.disabled = true;
                button.textContent = format.replace("%%(seconds)s", seconds).replace("%(seconds)s", seconds);
                window.setTimeout(tick, 1000);
            } else {
                button.disabled = false;
                button.textContent = readyLabel;
            }
        };
        tick();
    };
    document.addEventListener("DOMContentLoaded", () => initialise());
    document.addEventListener("htmx:afterSwap", (event) => initialise(event.detail.target));
})();
