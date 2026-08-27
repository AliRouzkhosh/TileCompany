document.addEventListener("DOMContentLoaded", () => {
    const i18n = document.getElementById("review-i18n")?.dataset || {};

    /* ==========================================================
       Read More
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-toggle");
        if (!btn) return;

        const text = btn.closest(".review-display").querySelector(".review-text");

        const expanded = text.classList.contains("max-h-none");

        text.classList.toggle("max-h-none", !expanded);
        text.classList.toggle("max-h-24", expanded);

        btn.textContent = expanded
            ? i18n.readMore
            : i18n.showLess;

    });


    /* ==========================================================
       Open Review Edit
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-edit-btn");
        if (!btn) return;

        const card = btn.closest(".review-card");

        card.querySelector(".review-display").classList.add("hidden");
        card.querySelector(".review-edit-form").classList.remove("hidden");

    });


    /* ==========================================================
       Cancel Review Edit
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-cancel");
        if (!btn) return;

        const card = btn.closest(".review-card");

        card.querySelector(".review-edit-form").classList.add("hidden");
        card.querySelector(".review-display").classList.remove("hidden");

    });


    /* ==========================================================
       Open Review Create
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-open-btn");
        if (!btn) return;

        const section = btn.closest("section");

        section.querySelector(".review-form-wrapper").classList.remove("hidden");
        btn.classList.add("hidden");

    });


    /* ==========================================================
       Cancel Review Create
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-form-cancel");
        if (!btn) return;

        const section = btn.closest("section");

        section.querySelector(".review-form-wrapper").classList.add("hidden");
        section.querySelector(".review-open-btn").classList.remove("hidden");

    });


    /* ==========================================================
       Submit New Review
    ========================================================== */

    document.addEventListener("submit", async e => {

        const form = e.target.closest(".review-create-form");
        if (!form) return;
        if (window.htmx && form.hasAttribute("hx-post")) return;

        e.preventDefault();

        const submit = form.querySelector("[type=submit]");
        const oldText = submit.textContent;

        submit.disabled = true;
        submit.textContent = i18n.submitting;

        const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {
                "X-CSRFToken":
                    form.querySelector("[name=csrfmiddlewaretoken]").value
            }
        });

        const data = await response.json();

        submit.disabled = false;
        submit.textContent = oldText;

        if (!data.success) {
            alert(data.message || i18n.unableSubmitReview);
            return;
        }

        form.reset();

        const section = form.closest("section");

        section.querySelector(".review-form-wrapper").classList.add("hidden");

        const success = section.querySelector(".review-success-panel");

        if (success) {
            success.classList.remove("hidden");
        }

    });


    /* ==========================================================
       Save Review
    ========================================================== */

    document.addEventListener("submit", async e => {

        const form = e.target.closest(".review-edit-form");
        if (!form) return;
        if (window.htmx && form.hasAttribute("hx-post")) return;

        e.preventDefault();

        const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {
                "X-CSRFToken":
                    form.querySelector("[name=csrfmiddlewaretoken]").value
            }
        });

        const data = await response.json();

        if (!data.success) {
            alert(data.message || i18n.unableUpdateReview);
            return;
        }

        if (data.requires_moderation) {
            form.closest(".review-card").remove();
            alert(i18n.reviewModeration);
            return;
        }

        const card = form.closest(".review-card");

        card.querySelector(".review-text").textContent = data.comment;

        const date = card.querySelector(".review-date");

        if (i18n.edited && !date.textContent.includes(i18n.edited)) {
            date.textContent += ` · ${i18n.edited}`;
        }

        form.classList.add("hidden");
        card.querySelector(".review-display").classList.remove("hidden");

    });


    /* ==========================================================
       Delete Review
    ========================================================== */

    document.addEventListener("click", async e => {

        const btn = e.target.closest(".review-delete-btn");
        if (!btn) return;

        if (btn.dataset.confirmed !== "true") return;
        delete btn.dataset.confirmed;

        const token = document.querySelector(
            "[name=csrfmiddlewaretoken]"
        ).value;

        const response = await fetch(btn.dataset.url, {
            method: "POST",
            headers: {
                "X-CSRFToken": token
            }
        });

        const data = await response.json();

        if (!data.success) {
            alert(data.message || i18n.unableDeleteReview);
            return;
        }

        btn.closest(".review-card").remove();

    });

});
