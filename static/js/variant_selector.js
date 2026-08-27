(function (root) {
    "use strict";

    const engine = {
        matches(variant, selection) {
            return Object.entries(selection).every(([code, value]) => !value || variant.attributes?.[code] === value);
        },
        exact(variants, selection, groupCodes) {
            if (!groupCodes.every((code) => Boolean(selection[code]))) return null;
            return variants.find((variant) => engine.matches(variant, selection)) || null;
        },
        best(variants, selection, anchorCode) {
            const candidates = variants.filter((variant) => !anchorCode || variant.attributes?.[anchorCode] === selection[anchorCode]);
            if (!candidates.length) return null;
            const rank = (variant) => ({
                preserved: Object.entries(selection).reduce(
                    (total, [code, value]) => total + (
                        code !== anchorCode && value && variant.attributes?.[code] === value ? 1 : 0
                    ), 0
                ),
                priced: variant.is_priced ? 1 : 0,
                order: Number(variant.sort_order || 0),
                id: Number(variant.id),
            });
            return candidates.reduce((best, variant) => {
                if (!best) return variant;
                const candidate = rank(variant);
                const current = rank(best);
                if (candidate.preserved !== current.preserved) return candidate.preserved > current.preserved ? variant : best;
                if (candidate.priced !== current.priced) return candidate.priced > current.priced ? variant : best;
                if (candidate.order !== current.order) return candidate.order < current.order ? variant : best;
                return candidate.id < current.id ? variant : best;
            }, null);
        },
        optionAvailable(variants, _selection, _groupCodes, code, value) {
            return variants.some((variant) => variant.attributes?.[code] === value);
        },
        optionContextCompatible(variants, selection, code, value) {
            const other = Object.fromEntries(
                Object.entries(selection).filter(([selectedCode, selectedValue]) => (
                    selectedCode !== code && Boolean(selectedValue)
                ))
            );
            return variants.some((variant) => (
                variant.attributes?.[code] === value && engine.matches(variant, other)
            ));
        },
    };
    root.variantSelectorEngine = engine;

    document.addEventListener("DOMContentLoaded", () => {
        const dataScript = document.getElementById("variants-data");
        if (!dataScript) return;
        const variants = JSON.parse(dataScript.textContent || "[]").filter((variant) => variant.is_active !== false);
        if (!variants.length) return;
        const groups = Array.from(document.querySelectorAll(".variant-group"));
        const groupCodes = groups.map((group) => group.dataset.groupCode);
        const buttons = Array.from(document.querySelectorAll(".variant-btn"));
        const translations = document.getElementById("productI18n")?.dataset || {};
        const elements = {
            price: document.getElementById("productPrice"), originalPrice: document.getElementById("productOriginalPrice"),
            discount: document.getElementById("productDiscount"), priceUnit: document.getElementById("productPriceUnit"),
            stock: document.getElementById("productStock"), qty: document.getElementById("quantityInput"),
            hiddenVariant: document.querySelector("[data-selected-variant]"), mainImage: document.querySelector("#mainImage img"),
            purchaseButtons: document.querySelectorAll("#addToCartButton, #buyNowButton"),
            variantSection: document.querySelector("[data-selected-variant-specifications]"),
            variantSkus: document.querySelectorAll("[data-selected-variant-sku], [data-product-variant-sku]"),
            variantRows: document.querySelector("[data-selected-variant-rows]"),
            operationalSection: document.querySelector("[data-variant-operational-section]"),
            operationalRows: document.querySelector("[data-variant-operational-rows]"),
            configuration: document.querySelector("[data-selected-configuration]"),
            configurationValues: document.querySelector("[data-selected-configuration-values]"),
            quantityUnit: document.querySelector("[data-quantity-unit]"),
        };
        const preselected = JSON.parse(document.getElementById("preselected-data")?.textContent || "{}");
        let selected = {...preselected};
        let currentVariant = null;

        function money(amount, currency) {
            if (amount === null || amount === undefined) return translations.priceUnavailable || "Price unavailable";
            const formatted = new Intl.NumberFormat(document.documentElement.lang || "en", {
                minimumFractionDigits: currency === "IRT" ? 0 : 2, maximumFractionDigits: currency === "IRT" ? 0 : 2,
            }).format(Number(amount));
            return currency === "USD" ? `$${formatted}` : `${formatted} ${currency === "IRT" ? (document.documentElement.lang.startsWith("fa") ? "تومان" : "Toman") : currency}`;
        }
        const number = (value) => new Intl.NumberFormat(document.documentElement.lang || "en", {maximumFractionDigits: 0}).format(Number(value));
        const quantityNoun = (variant, quantity) => quantity === 1
            ? variant.selling_unit?.singular_noun
            : variant.selling_unit?.plural_noun;

        function renderRows(container, rows) {
            if (!container) return;
            container.replaceChildren(...rows.map((row) => {
                const wrapper = document.createElement("div");
                wrapper.className = "grid gap-1 py-3 sm:grid-cols-[minmax(0,32%)_minmax(0,68%)] sm:gap-6 sm:py-3.5";
                wrapper.dataset.specificationRow = "";
                if (row.definition_code) wrapper.dataset.variantSpecification = row.definition_code;
                const label = document.createElement("dt");
                label.className = "text-sm font-medium text-text-muted";
                label.textContent = row.label;
                const value = document.createElement("dd");
                value.className = "min-w-0 text-sm leading-6 text-text-primary sm:text-start";
                value.textContent = row.display_value ?? row.value ?? "";
                wrapper.append(label, value);
                return wrapper;
            }));
        }

        function updateVariantDetails(variant) {
            if (elements.variantSection) elements.variantSection.hidden = !variant;
            elements.variantSkus.forEach((element) => { element.textContent = variant?.sku || "—"; });
            const detailSpecifications = variant?.detail_specifications || [];
            if (elements.variantRows) {
                elements.variantRows.hidden = !detailSpecifications.length;
            }
            renderRows(elements.variantRows, detailSpecifications);
            const operationalFacts = variant?.operational_facts || [];
            if (elements.operationalSection) {
                elements.operationalSection.hidden = !operationalFacts.length;
            }
            renderRows(elements.operationalRows, operationalFacts);
            const configuration = detailSpecifications
                .filter((item) => item.is_variant_dimension && item.display_value)
                .map((item) => item.display_value);
            if (elements.configuration) elements.configuration.hidden = !configuration.length;
            if (elements.configurationValues) elements.configurationValues.textContent = configuration.join(" • ");
        }

        function updateQuantityUnit() {
            if (!elements.quantityUnit) return;
            const quantity = Number(elements.qty?.value || 0);
            elements.quantityUnit.textContent = currentVariant ? (quantityNoun(currentVariant, quantity) || "") : "";
        }

        function publishResolvedVariant(variant) {
            document.dispatchEvent(new CustomEvent("storefront:variant-resolved", {
                detail: {variant},
            }));
        }

        function clearCommerce() {
            currentVariant = null;
            if (elements.hiddenVariant) elements.hiddenVariant.value = "";
            updateVariantDetails(null);
            elements.purchaseButtons.forEach((button) => { button.disabled = true; button.classList.add("opacity-50", "cursor-not-allowed"); });
            if (elements.price) elements.price.textContent = translations.priceUnavailable || "Price unavailable";
            if (elements.priceUnit) elements.priceUnit.textContent = "";
            if (elements.qty) {
                elements.qty.min = 0; elements.qty.max = 0; elements.qty.value = 0;
                elements.qty.dispatchEvent(new Event("change", {bubbles: true}));
            }
            updateQuantityUnit();
            publishResolvedVariant(null);
        }
        function updateCommerce(variant, updateImage = true) {
            currentVariant = variant;
            const priced = Boolean(variant.is_priced);
            if (elements.price) elements.price.textContent = money(variant.amount, variant.currency);
            const onSale = priced && variant.compare_at_amount && Number(variant.compare_at_amount) > Number(variant.amount);
            if (elements.originalPrice) { elements.originalPrice.textContent = onSale ? money(variant.compare_at_amount, variant.currency) : ""; elements.originalPrice.classList.toggle("hidden", !onSale); }
            if (elements.discount) { elements.discount.textContent = onSale ? `${translations.save || "Save"} ${number(variant.discount_percent)}%` : ""; elements.discount.classList.toggle("hidden", !onSale); }
            if (elements.priceUnit) elements.priceUnit.textContent = priced ? variant.price_unit_display : "";
            elements.purchaseButtons.forEach((button) => { button.disabled = !priced || variant.stock < 1; button.classList.toggle("opacity-50", button.disabled); button.classList.toggle("cursor-not-allowed", button.disabled); });
            if (elements.stock) {
                const status = document.createElement("span");
                status.className = `font-medium ${variant.stock > 0 ? "text-success" : "text-error"}`;
                status.textContent = variant.stock > 0
                    ? `${number(variant.stock)} ${quantityNoun(variant, variant.stock) || ""} ${translations.available || "available"}`
                    : (translations.outOfStock || "Out of stock");
                elements.stock.replaceChildren(status);
            }
            if (elements.qty) {
                elements.qty.min = variant.stock < 1 ? 0 : 1;
                elements.qty.max = variant.stock;
                elements.qty.value = variant.stock < 1 ? 0 : Math.min(variant.stock, Math.max(1, Number(elements.qty.value) || 1));
                elements.qty.dispatchEvent(new Event("change", {bubbles: true}));
            }
            updateQuantityUnit();
            if (elements.hiddenVariant) elements.hiddenVariant.value = variant.id;
            updateVariantDetails(variant);
            if (updateImage && variant.image && elements.mainImage) elements.mainImage.src = variant.image;
            publishResolvedVariant(variant);
        }
        function paint() {
            buttons.forEach((button) => {
                const active = selected[button.dataset.group] === button.dataset.value;
                const available = engine.optionAvailable(variants, selected, groupCodes, button.dataset.group, button.dataset.value);
                const contextCompatible = available && engine.optionContextCompatible(
                    variants, selected, button.dataset.group, button.dataset.value
                );
                const contextIncompatible = available && !active && !contextCompatible;
                button.setAttribute("aria-pressed", String(active));
                button.setAttribute("aria-disabled", String(!available));
                button.dataset.contextIncompatible = String(contextIncompatible);
                button.classList.toggle("opacity-60", contextIncompatible);
                button.disabled = !available;
            });
        }
        function resolve(updateImage = true) {
            const exact = engine.exact(variants, selected, groupCodes);
            paint();
            if (exact) updateCommerce(exact, updateImage); else clearCommerce();
        }
        buttons.forEach((button) => button.addEventListener("click", () => {
            if (!engine.optionAvailable(variants, selected, groupCodes, button.dataset.group, button.dataset.value)) return;
            selected[button.dataset.group] = button.dataset.value;
            const best = engine.best(variants, selected, button.dataset.group);
            if (best) selected = {...best.attributes};
            resolve(true);
        }));
        elements.qty?.addEventListener("input", updateQuantityUnit);
        elements.qty?.addEventListener("change", updateQuantityUnit);
        const hiddenId = Number(elements.hiddenVariant?.value || 0);
        const initial = variants.find((variant) => variant.id === hiddenId) || engine.exact(variants, selected, groupCodes);
        if (initial) { selected = {...initial.attributes}; paint(); updateCommerce(initial, false); }
        else resolve(false);
    });
})(window);
