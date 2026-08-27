document.addEventListener("DOMContentLoaded", () => {
    const gallery = document.querySelector("[data-product-gallery]");
    if (!gallery) return;

    const main = gallery.querySelector("[data-gallery-main]");
    const rail = gallery.querySelector("[data-gallery-thumbnails]");
    const productName = gallery.dataset.productName || "";
    const imageLabel = gallery.dataset.imageLabel || "View image";
    let images = Array.from(gallery.querySelectorAll(".gallery-thumb"))
        .map((thumb) => thumb.dataset.full)
        .filter(Boolean);
    let current = 0;

    function ensureMainImage() {
        let image = main?.querySelector("[data-gallery-main-image]");
        if (!image && main) {
            main.querySelector("[data-gallery-fallback]")?.remove();
            image = document.createElement("img");
            image.dataset.galleryMainImage = "";
            image.alt = productName;
            image.className = "h-full w-full object-contain transition-opacity duration-200";
            main.prepend(image);
        }
        return image;
    }

    function showFallback() {
        main?.querySelector("[data-gallery-main-image]")?.remove();
        if (!main || main.querySelector("[data-gallery-fallback]")) return;
        const fallback = document.createElement("div");
        fallback.dataset.galleryFallback = "";
        fallback.className = "home-media-fallback flex h-full w-full items-center justify-center text-text-muted";
        fallback.setAttribute("aria-label", productName);
        main.prepend(fallback);
    }

    function activate(index, {focus = false} = {}) {
        if (!images.length) {
            showFallback();
            return;
        }
        current = (index + images.length) % images.length;
        const mainImage = ensureMainImage();
        if (mainImage) mainImage.src = images[current];
        const thumbs = Array.from(rail?.querySelectorAll(".gallery-thumb") || []);
        thumbs.forEach((thumb, thumbIndex) => {
            thumb.setAttribute("aria-current", String(thumbIndex === current));
        });
        if (focus) thumbs[current]?.focus();
    }

    function makeThumb(url, index) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "gallery-thumb group h-16 w-16 shrink-0 overflow-hidden rounded-control border border-border-strong bg-surface-subtle p-0.5 transition hover:border-brand-mid focus-visible:outline-none aria-[current=true]:border-brand aria-[current=true]:ring-2 aria-[current=true]:ring-brand/25";
        button.dataset.full = url;
        button.setAttribute("aria-label", `${imageLabel} ${new Intl.NumberFormat(document.documentElement.lang || "en").format(index + 1)}`);
        button.setAttribute("aria-current", String(index === 0));
        const image = document.createElement("img");
        image.src = url;
        image.alt = "";
        image.setAttribute("aria-hidden", "true");
        image.className = "h-full w-full rounded-[0.6rem] object-cover";
        button.append(image);
        return button;
    }

    function setImages(nextImages) {
        images = Array.from(new Set((nextImages || []).filter(Boolean)));
        current = 0;
        if (rail) {
            rail.replaceChildren(...images.map(makeThumb));
            rail.classList.toggle("hidden", images.length < 2);
        }
        activate(0);
    }

    rail?.addEventListener("click", (event) => {
        const thumb = event.target.closest(".gallery-thumb");
        if (!thumb) return;
        activate(Array.from(rail.querySelectorAll(".gallery-thumb")).indexOf(thumb));
    });

    gallery.addEventListener("keydown", (event) => {
        if (!images.length || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        const rtl = getComputedStyle(gallery).direction === "rtl";
        const forward = event.key === (rtl ? "ArrowLeft" : "ArrowRight");
        event.preventDefault();
        activate(current + (forward ? 1 : -1), {focus: true});
    });

    document.addEventListener("storefront:variant-resolved", (event) => {
        setImages(event.detail?.variant?.images || []);
    });
});
