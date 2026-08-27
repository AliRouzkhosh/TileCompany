(() => {
    const localizedSeconds = (seconds) => {
        const language = document.documentElement.lang || "en";
        return new Intl.NumberFormat(language.startsWith("fa") ? "fa-IR" : language).format(seconds);
    };

    const initialiseCooldown = (root = document) => {
        const forms = root.matches?.("[data-comment-cooldown]")
            ? [root]
            : root.querySelectorAll?.("[data-comment-cooldown]") || [];

        forms.forEach((form) => {
            if (form.dataset.cooldownInitialised === "true") return;
            let remaining = Number.parseInt(form.dataset.commentCooldown || "0", 10);
            if (!Number.isFinite(remaining) || remaining <= 0) return;

            form.dataset.cooldownInitialised = "true";
            const submit = form.querySelector("[data-comment-submit]");
            if (!submit) return;

            const label = () => submit.dataset.currentLabel || submit.dataset.defaultLabel;
            const render = () => {
                submit.disabled = remaining > 0;
                submit.textContent = remaining > 0
                    ? `${label()} — ${localizedSeconds(remaining)}`
                    : label();
            };
            render();

            const timer = window.setInterval(() => {
                remaining -= 1;
                if (remaining <= 0) {
                    remaining = 0;
                    window.clearInterval(timer);
                }
                render();
            }, 1000);
        });
    };

    document.addEventListener("DOMContentLoaded", () => initialiseCooldown());
    document.addEventListener("htmx:afterSwap", (event) => initialiseCooldown(event.detail.target));
})();
