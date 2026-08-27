document.addEventListener("DOMContentLoaded", () => {
    const i18n = document.getElementById("review-i18n")?.dataset || {};

    /* ==========================================================
       Open Comment Form
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-comment-open");
        if (!btn) return;

        const card = btn.closest(".review-card");

        card.querySelector(".review-comment-create")
            .classList.remove("hidden");

        card.querySelector(".review-comment-create textarea")?.focus();

        btn.classList.add("hidden");

    });


    /* ==========================================================
       Cancel Comment Form
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".review-comment-create .comment-cancel");
        if (!btn) return;

        const wrapper = btn.closest(".review-comment-create");

        wrapper.classList.add("hidden");
        wrapper.querySelector("form").reset();

        wrapper
            .closest(".review-card")
            .querySelector(".review-comment-open")
            .classList.remove("hidden");

    });


    /* ==========================================================
       Open Reply
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".reply-open-btn");
        if (!btn) return;

        const card = btn.closest(".comment-card");

        card.querySelector(".reply-form")
            .classList.remove("hidden");

        btn.classList.add("hidden");

    });


    /* ==========================================================
       Cancel Reply
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".reply-cancel-btn");
        if (!btn) return;

        const form = btn.closest(".reply-form");

        form.classList.add("hidden");
        form.reset();

        form.closest(".comment-card")
            .querySelector(".reply-open-btn")
            .classList.remove("hidden");

    });


    /* ==========================================================
       Open Comment Edit
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".comment-edit-btn");
        if (!btn) return;

        const card = btn.closest(".comment-card");

        card.querySelector(".comment-display")
            .classList.add("hidden");

        card.querySelector(".comment-edit-form")
            .classList.remove("hidden");

    });


    /* ==========================================================
       Cancel Comment Edit
    ========================================================== */

    document.addEventListener("click", e => {

        const btn = e.target.closest(".comment-edit-cancel");
        if (!btn) return;

        const card = btn.closest(".comment-card");

        card.querySelector(".comment-edit-form")
            .classList.add("hidden");

        card.querySelector(".comment-display")
            .classList.remove("hidden");

    });


    /* ==========================================================
       Save Comment
    ========================================================== */

    document.addEventListener("submit", async e => {

        const form = e.target.closest(".comment-edit-form");
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
            alert(data.message || i18n.unableUpdateComment);
            return;
        }

        if (data.requires_moderation) {
            form.closest(".comment-card").remove();
            alert(i18n.commentModeration);
            return;
        }

        const card = form.closest(".comment-card");

        card.querySelector(".comment-text").textContent = data.comment;

        if (data.updated_at) {
            const date = card.querySelector(".comment-date");

            if (i18n.edited && !date.textContent.includes(i18n.edited)) {
                date.textContent += ` · ${i18n.edited}`;
            }
        }

        form.classList.add("hidden");
        card.querySelector(".comment-display")
            .classList.remove("hidden");

    });


    /* ==========================================================
       Delete Comment
    ========================================================== */

    document.addEventListener("click", async e => {

        const btn = e.target.closest(".comment-delete-btn");
        if (!btn) return;

        if (btn.dataset.confirmed !== "true") return;
        delete btn.dataset.confirmed;

        const token = btn.closest("form")
            .querySelector("[name=csrfmiddlewaretoken]").value;

        const response = await fetch(btn.dataset.url, {
            method: "POST",
            headers: {
                "X-CSRFToken": token
            }
        });

        const data = await response.json();

        if (!data.success) {
            alert(data.message || i18n.unableDeleteComment);
            return;
        }

        btn.closest(".comment-card").remove();

    });


    /* ==========================================================
       Submit Comment / Reply
    ========================================================== */

    document.addEventListener("submit", async e => {

        const form = e.target;

        if (
            !form.classList.contains("comment-form") &&
            !form.classList.contains("reply-form")
        ) {
            return;
        }

        if (window.htmx && form.hasAttribute("hx-post")) return;

        e.preventDefault();

        const submit = form.querySelector("[type=submit]");
        const oldText = submit.textContent;

        submit.disabled = true;
        submit.textContent = i18n.posting;

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
            alert(data.message || i18n.unablePostComment);
            return;
        }

        form.reset();

        if (form.classList.contains("reply-form")) {

            const container = form.closest(".reply-create");

            form.reset();
            form.classList.add("hidden");

            container
                .querySelector(".reply-success-panel")
                ?.classList.remove("hidden");

            form.closest(".comment-card")
                .querySelector(".reply-open-btn")
                ?.classList.add("hidden");
        } else {

            const wrapper = form.closest(".review-comment-create");

            const card = wrapper.closest(".review-card");

            wrapper.classList.add("hidden");

            card.querySelector(".review-comment-open").classList.add("hidden");

            card.querySelector(".comment-success-panel").classList.remove("hidden");

        }

    });

});
