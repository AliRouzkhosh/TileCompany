document.addEventListener("click", (event) => {
  const dismiss = event.target.closest(".ts-callout-dismiss");
  if (dismiss) dismiss.closest(".ts-callout")?.remove();
  const ownReview = event.target.closest(".review-own-edit");
  if (ownReview) {
    const card = document.querySelector(`.review-card[data-review-id="${ownReview.dataset.reviewId}"]`);
    card?.querySelector(".review-edit-btn")?.click();
    card?.scrollIntoView({ block: "center" });
    card?.querySelector(".review-edit-form textarea")?.focus();
  }
  const edit = event.target.closest(".comment-edit-btn");
  if (edit) {
    event.preventDefault();
    const card = edit.closest("[data-comment-card]");
    card.querySelector(".comment-display")?.classList.add("hidden");
    const form = card.querySelector(".comment-edit-form");
    form?.classList.remove("hidden");
    form?.querySelector("textarea")?.focus();
  }
  const cancel = event.target.closest(".comment-edit-cancel");
  if (cancel) {
    const card = cancel.closest("[data-comment-card]");
    card.querySelector(".comment-edit-form")?.classList.add("hidden");
    card.querySelector(".comment-display")?.classList.remove("hidden");
  }
});
