document.addEventListener("DOMContentLoaded", () => {
    const selectedVariant = document.querySelector("[data-selected-variant]");
    const quantityInput = document.querySelector("[data-quantity-input]");
    const variantInputs = document.querySelectorAll("[data-purchase-variant-input]");
    const quantityInputs = document.querySelectorAll("[data-purchase-quantity-input]");
    const mainAction = document.querySelector("[data-primary-purchase-action]");
    const mobileBar = document.querySelector("[data-mobile-purchase-bar]");
    const mobileButton = document.querySelector("[data-mobile-purchase-button]");
    const mobilePrice = document.querySelector("[data-mobile-purchase-price]");
    const mobileUnit = document.querySelector("[data-mobile-purchase-unit]");
    const desktopMedia = window.matchMedia("(min-width: 1024px)");
    const eligibilityAnchor = document.querySelector("[data-purchase-eligibility]");
    let eligibilityReached = false;
    let mainActionVisible = false;
    let mainActionSeen = false;
    let footerVisible = false;

    function syncForms(variant = null) {
        const variantId = variant?.id ?? selectedVariant?.value ?? "";
        const quantity = quantityInput?.value || 0;
        variantInputs.forEach((input) => { input.value = variantId; });
        quantityInputs.forEach((input) => { input.value = quantity; });
    }

    function syncMobileState() {
        if (mobilePrice) mobilePrice.textContent = document.querySelector("#productPrice")?.textContent || "";
        if (mobileUnit) mobileUnit.textContent = document.querySelector("#productPriceUnit")?.textContent || "";
        if (mobileButton) mobileButton.disabled = Boolean(mainAction?.disabled);
    }

    function paintMobileBar() {
        if (!mobileBar) return;
        const eligible = desktopMedia.matches
            ? eligibilityReached && !mainActionSeen
            : eligibilityReached;
        const visible = eligible && !mainActionVisible && !footerVisible;
        mobileBar.dataset.visible = String(visible);
        mobileBar.setAttribute("aria-hidden", String(!visible));
        mobileBar.toggleAttribute("inert", !visible);
        mobileBar.classList.toggle("translate-y-full", !visible);
        mobileBar.classList.toggle("opacity-0", !visible);
        mobileBar.classList.toggle("pointer-events-none", !visible);
        mobileBar.classList.toggle("translate-y-0", visible);
        mobileBar.classList.toggle("opacity-100", visible);
        mobileBar.classList.toggle("pointer-events-auto", visible);
    }

    syncForms();
    syncMobileState();

    quantityInput?.addEventListener("input", () => syncForms());
    quantityInput?.addEventListener("change", () => syncForms());
    document.addEventListener("storefront:variant-resolved", (event) => {
        syncForms(event.detail?.variant || null);
        syncMobileState();
    });

    if ("IntersectionObserver" in window && eligibilityAnchor && mainAction && mobileBar) {
        const eligibilityObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                eligibilityReached = eligibilityReached
                    || entry.isIntersecting
                    || entry.boundingClientRect.bottom < 80;
            });
            paintMobileBar();
        }, {rootMargin: "0px 0px -50% 0px", threshold: 0});
        const visibilityObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.target === mainAction) {
                    mainActionVisible = entry.isIntersecting;
                    mainActionSeen = mainActionSeen || entry.isIntersecting;
                } else {
                    footerVisible = entry.isIntersecting;
                }
            });
            paintMobileBar();
        }, {threshold: 0.05});
        eligibilityObserver.observe(eligibilityAnchor);
        visibilityObserver.observe(mainAction);
        const footer = document.querySelector("footer");
        if (footer) visibilityObserver.observe(footer);
    }
    desktopMedia.addEventListener?.("change", paintMobileBar);
});
