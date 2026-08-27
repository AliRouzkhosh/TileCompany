document.addEventListener("DOMContentLoaded", () => {
    const forms = document.querySelectorAll("form[data-wishlist]");

    if (!forms.length) return;

    function setHeart(form, inWishlist) {

        const icon = form.querySelector("[data-wishlist-icon]");
        const button = form.querySelector("[data-wishlist-button]");

        if (!icon) return;

        icon.setAttribute("fill", inWishlist ? "currentColor" : "none");
        icon.classList.toggle("text-brand", inWishlist);

        form.dataset.inWishlist = inWishlist ? "true" : "false";

        if (button) {
            const label = inWishlist
                ? form.dataset.removeLabel
                : form.dataset.addLabel;
            button.setAttribute("aria-label", label);
            button.setAttribute("title", label);
            button.setAttribute("aria-pressed", String(inWishlist));
        }
    }

    forms.forEach(form => {

        // Initial state
        setHeart(
            form,
            form.dataset.inWishlist === "true"
        );

        form.addEventListener("submit", async (event) => {

            event.preventDefault();

            try {

                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken":
                            form.querySelector("[name=csrfmiddlewaretoken]").value,
                    },
                    body: new FormData(form),
                });

                if (!response.ok)
                    throw new Error("Request failed.");

                const data = await response.json();

                // Update heart
                setHeart(form, data.in_wishlist);

                // Toast
                if (typeof showToast === "function") {
                    showToast(data.message);
                }

                // Wishlist page only
                if (
                    form.dataset.removeOnSuccess === "true" &&
                    !data.in_wishlist
                ) {

                    const card = form.closest("[data-wishlist-card]");

                    if (card) {

                        card.style.transition = "all .25s ease";
                        card.style.opacity = "0";
                        card.style.transform = "scale(.96)";

                        setTimeout(() => {
                            card.remove();
                        }, 250);
                    }
                }

                // Navbar counter
                const counter = document.querySelector(
                    "[data-wishlist-count]"
                );

                if (counter) {
                    counter.textContent = data.wishlist_count;
                }

            } catch (error) {

                console.error(error);

                if (typeof showToast === "function") {
                    showToast(form.dataset.errorMessage);
                }

            }

        });

    });

});
