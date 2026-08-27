(() => {
    const form = () => document.querySelector("[data-comment-form]");
    const updateSubmitLabel = (isReply) => {
        const button = document.querySelector("[data-comment-submit]");
        if (!button || button.disabled) return;
        const label = isReply ? button.dataset.replyLabel : button.dataset.defaultLabel;
        button.dataset.currentLabel = label;
        button.textContent = label;
    };
    const clearReply = () => {
        const current = form();
        if (!current) return;
        current.querySelector("[data-reply-to-id]").value = "";
        document.querySelector("[data-replying-container]")?.classList.add("hidden");
        const replyingTo = document.querySelector("[data-replying-to]");
        if (replyingTo) replyingTo.textContent = "";
        updateSubmitLabel(false);
    };
    document.addEventListener("click", (event) => {
        const cancel = event.target.closest("[data-clear-comment-reply]");
        if (cancel) {
            clearReply();
            return;
        }
        const reply = event.target.closest("[data-comment-reply]");
        if (!reply) return;
        const current = form();
        if (!current) return;
        current.querySelector("[data-reply-to-id]").value = reply.dataset.commentId;
        document.querySelector("[data-replying-container]")?.classList.remove("hidden");
        const replyingTo = document.querySelector("[data-replying-to]");
        if (replyingTo) replyingTo.textContent = `${current.dataset.replyingToLabel} ${reply.dataset.author}`;
        updateSubmitLabel(true);
        document.getElementById("comment-form-title")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
})();
