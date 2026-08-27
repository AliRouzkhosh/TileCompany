(() => {
    const initialise = (root = document) => {
        root.querySelectorAll?.("[data-file-input-label]").forEach((label) => {
            if (label.dataset.fileInputInitialised === "true") return;
            const input = document.getElementById(label.dataset.fileInputLabel);
            if (!input) return;
            label.dataset.fileInputInitialised = "true";
            input.addEventListener("change", () => {
                label.textContent = input.files.length ? `${label.dataset.selected}: ${input.files.length}` : label.dataset.empty;
            });
        });
    };
    document.addEventListener("DOMContentLoaded", () => initialise());
    document.addEventListener("htmx:afterSwap", (event) => initialise(event.detail.target));
})();
