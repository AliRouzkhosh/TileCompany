document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-search]").forEach(initializeSearch);
});

function initializeSearch(container) {
    const input = container.querySelector("[data-search-input]");
    const suggestions = container.querySelector("[data-search-suggestions]");
    const endpoint = container.dataset.searchEndpoint;
    if (!input || !suggestions || !endpoint) return;
    let selectedIndex = -1;

    function announceSearchOverlay() {
        document.dispatchEvent(new CustomEvent("storefront:header-overlay-open", {
            detail: { name: "search", source: container },
        }));
    }
    function showSuggestions() {
        announceSearchOverlay();
        suggestions.classList.remove("hidden");
        input.setAttribute("aria-expanded", "true");
    }
    function hideSuggestions() {
        suggestions.classList.add("hidden");
        input.setAttribute("aria-expanded", "false");
        selectedIndex = -1;
    }
    function clearSuggestions() {
        suggestions.replaceChildren();
    }
    function sectionTitle(text, withBorder = false) {
        const title = document.createElement("div");
        title.className = `${withBorder ? "border-t border-border bg-surface-subtle " : ""}px-4 pb-2 pt-4 text-xs font-semibold uppercase tracking-wider text-text-muted`;
        title.textContent = text;
        return title;
    }
    function renderGroups(groups) {
        if (!groups.length) return;
        suggestions.appendChild(sectionTitle(container.dataset.filtersLabel));
        groups.forEach((group) => {
            const wrapper = document.createElement("div");
            wrapper.className = "border-t border-border px-4 py-3";
            const heading = document.createElement("div");
            heading.className = "mb-2 text-xs font-semibold uppercase tracking-wider text-text-muted";
            heading.textContent = group.title;
            wrapper.appendChild(heading);
            const chips = document.createElement("div");
            chips.className = "flex flex-wrap gap-2";
            group.items.forEach((item) => {
                const chip = document.createElement("a");
                chip.href = item.url;
                chip.className = "rounded-chip border border-border-strong px-3 py-1.5 text-sm text-text-secondary transition hover:border-brand-mid hover:bg-brand-subtle hover:text-brand-active";
                chip.textContent = item.label;
                chips.appendChild(chip);
            });
            wrapper.appendChild(chips);
            suggestions.appendChild(wrapper);
        });
    }
    function renderProducts(products) {
        if (!products.length) return;
        suggestions.appendChild(sectionTitle(container.dataset.productsLabel, true));
        products.forEach((product) => {
            const link = document.createElement("a");
            link.href = product.url;
            link.className = "search-item block border-t border-border px-4 py-3 transition hover:bg-brand-subtle focus:bg-brand-subtle";
            const name = document.createElement("div");
            name.className = "font-medium text-text-primary";
            name.textContent = product.name;
            const subtitle = document.createElement("div");
            subtitle.className = "mt-1 text-sm text-text-muted";
            subtitle.textContent = product.subtitle || "";
            link.append(name, subtitle);
            suggestions.appendChild(link);
        });
    }
    async function fetchSuggestions() {
        try {
            const url = new URL(endpoint, window.location.origin);
            url.searchParams.set("q", input.value.trim());
            const response = await fetch(url);
            if (!response.ok) throw new Error(`Search suggestions failed: ${response.status}`);
            const data = await response.json();
            clearSuggestions();
            selectedIndex = -1;
            if (!data.groups.length && !data.products.length) {
                hideSuggestions();
                return;
            }
            renderGroups(data.groups);
            renderProducts(data.products);
            input.dataset.autocomplete = data.products.length ? data.products[0].name : "";
            showSuggestions();
        } catch (error) {
            console.error(error);
            hideSuggestions();
        }
    }

    input.addEventListener("focus", () => {
        announceSearchOverlay();
        fetchSuggestions();
    });
    input.addEventListener("input", fetchSuggestions);
    input.addEventListener("keydown", (event) => {
        const items = [...suggestions.querySelectorAll(".search-item")];
        if (event.key === "Escape") {
            hideSuggestions();
            return;
        }
        if (!items.length) return;
        if (event.key === "ArrowDown") {
            event.preventDefault();
            selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            selectedIndex = Math.max(selectedIndex - 1, 0);
        } else if (event.key === "Enter" && selectedIndex >= 0) {
            event.preventDefault();
            items[selectedIndex].click();
            return;
        }
        items.forEach((item) => item.classList.remove("bg-brand-subtle"));
        if (selectedIndex >= 0) items[selectedIndex].classList.add("bg-brand-subtle");
    });
    document.addEventListener("click", (event) => {
        if (!container.contains(event.target)) hideSuggestions();
    });
    document.addEventListener("storefront:header-overlay-open", (event) => {
        if (event.detail.source !== container) hideSuggestions();
    });
}
