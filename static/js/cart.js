document.addEventListener("DOMContentLoaded", () => {
    const cart = document.getElementById("cartPage");
    if (!cart || cart.dataset.initialized === "true") return;

    cart.dataset.initialized = "true";
    const i18n = document.getElementById("cart-i18n")?.dataset || {};

    initializeQuantityControls(i18n);
    initializeRemoveButtons(i18n);
});

function initializeQuantityControls(i18n) {
    document.querySelectorAll("[data-cart-form]").forEach((form) => {
        const input = form.querySelector("[data-quantity]");
        const increase = form.querySelector("[data-increase]");
        const decrease = form.querySelector("[data-decrease]");
        if (!input) return;

        form.dataset.confirmedQuantity = form.dataset.confirmedQuantity || input.value;
        syncQuantityButtons(form);

        increase?.addEventListener("click", () => stepQuantity(form, 1, i18n));
        decrease?.addEventListener("click", () => stepQuantity(form, -1, i18n));
        input.addEventListener("change", () => {
            if (form.dataset.pending === "true") return;
            if (!input.checkValidity()) {
                restoreConfirmedQuantity(form);
                syncQuantityButtons(form);
                return;
            }
            updateCart(form, i18n);
        });
    });
}

function stepQuantity(form, delta, i18n) {
    if (form.dataset.pending === "true") return;
    const input = form.querySelector("[data-quantity]");
    if (!input) return;

    const minimum = Number.parseInt(input.min || "1", 10);
    const maximum = Number.parseInt(input.max || "999999", 10);
    const current = Number.parseInt(input.value || String(minimum), 10);
    input.value = String(Math.max(minimum, Math.min(maximum, current + delta)));
    input.dispatchEvent(new Event("change"));
}

async function updateCart(form, i18n) {
    if (form.dataset.pending === "true") return;
    const input = form.querySelector("[data-quantity]");
    const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]")?.value;
    if (!input || !csrfToken) return;

    setFormPending(form, true);
    showFeedback(form, "");

    try {
        const response = await fetch(form.action, {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": csrfToken,
            },
            body: new FormData(form),
        });
        if (!response.ok) throw new Error(`Cart update failed with ${response.status}`);

        const data = await response.json();
        if (!data.success) throw new Error("Cart update was rejected");

        input.value = String(data.quantity);
        form.dataset.confirmedQuantity = String(data.quantity);
        updateCartTotals(data);
        updateCartRow(form, data);
    } catch (error) {
        restoreConfirmedQuantity(form);
        showFeedback(form, i18n.updateFailed || "We could not update this quantity. Please try again.");
        console.error(error);
    } finally {
        setFormPending(form, false);
    }
}

function updateCartTotals(data) {
    document.querySelectorAll("[data-cart-total], [data-cart-subtotal]").forEach((element) => {
        element.textContent = data.cart_total_display || fallbackMoney(data.cart_total, data.currency);
    });
    document.querySelectorAll("[data-cart-quantity], [data-cart-count], [data-cart-page-quantity]").forEach((element) => {
        element.textContent = data.cart_quantity_display || String(data.cart_quantity);
    });
    document.querySelectorAll("[data-cart-reference-subtotal]").forEach((element) => { element.textContent = data.reference_subtotal_display || fallbackMoney(data.reference_subtotal, data.currency); });
    document.querySelectorAll("[data-cart-product-savings]").forEach((element) => { element.textContent = `-${data.product_savings_display || fallbackMoney(data.product_savings, data.currency)}`; });
}

function updateCartRow(form, data) {
    const row = form.closest("[data-cart-item]");
    if (!row) return;

    const subtotal = row.querySelector("[data-subtotal]");
    const quantity = row.querySelector("[data-line-quantity]");
    if (subtotal) subtotal.textContent = data.subtotal_display || fallbackMoney(data.subtotal, data.currency);
    if (quantity) quantity.textContent = data.quantity_phrase || String(data.quantity);
}

function fallbackMoney(amount, currency) {
    const value = new Intl.NumberFormat(document.documentElement.lang || undefined, {
        maximumFractionDigits: 2,
    }).format(Number(amount));
    return `${value} ${currency || ""}`.trim();
}

function restoreConfirmedQuantity(form) {
    const input = form.querySelector("[data-quantity]");
    if (input) input.value = form.dataset.confirmedQuantity || input.min || "1";
}

function setFormPending(form, pending) {
    form.dataset.pending = String(pending);
    form.setAttribute("aria-busy", String(pending));
    const input = form.querySelector("[data-quantity]");
    if (input) input.readOnly = pending;
    syncQuantityButtons(form);
}

function syncQuantityButtons(form) {
    const input = form.querySelector("[data-quantity]");
    if (!input) return;

    const pending = form.dataset.pending === "true";
    const value = Number.parseInt(input.value || "0", 10);
    const minimum = Number.parseInt(input.min || "1", 10);
    const maximum = Number.parseInt(input.max || "999999", 10);
    const decrease = form.querySelector("[data-decrease]");
    const increase = form.querySelector("[data-increase]");
    if (decrease) decrease.disabled = pending || value <= minimum;
    if (increase) increase.disabled = pending || value >= maximum;
}

function showFeedback(form, message) {
    const feedback = form.querySelector("[data-cart-feedback]");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.classList.toggle("hidden", !message);
}

function initializeRemoveButtons(i18n) {
    document.querySelectorAll("[data-remove]").forEach((button) => {
        button.addEventListener("click", (event) => {
            if (!confirm(i18n.removeConfirmation)) event.preventDefault();
        });
    });
}
