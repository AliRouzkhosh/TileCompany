document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-quantity]").forEach((container) => {
        const input = container.querySelector("[data-quantity-input]");
        const plus = container.querySelector("[data-quantity-plus]");
        const minus = container.querySelector("[data-quantity-minus]");
        if (!input) return;

        function bounds() {
            const minimum = Number.isFinite(Number(input.min)) ? Number(input.min) : 1;
            const maximum = Number.isFinite(Number(input.max)) ? Number(input.max) : 999;
            return {minimum, maximum};
        }

        function syncButtons() {
            const {minimum, maximum} = bounds();
            const value = Number(input.value);
            if (minus) minus.disabled = value <= minimum || maximum < 1;
            if (plus) plus.disabled = value >= maximum || maximum < 1;
        }

        function clampValue() {
            const {minimum, maximum} = bounds();
            let value = Number.parseInt(input.value, 10);
            if (Number.isNaN(value)) value = minimum;
            input.value = Math.max(minimum, Math.min(maximum, value));
            syncButtons();
            input.dispatchEvent(new Event("input", {bubbles: true}));
        }

        plus?.addEventListener("click", () => {
            input.value = (Number.parseInt(input.value, 10) || 0) + 1;
            clampValue();
        });
        minus?.addEventListener("click", () => {
            input.value = (Number.parseInt(input.value, 10) || 1) - 1;
            clampValue();
        });
        input.addEventListener("change", clampValue);
        input.addEventListener("blur", clampValue);
        document.addEventListener("storefront:variant-resolved", syncButtons);
        syncButtons();
    });
});
