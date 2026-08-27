document.addEventListener("change", (event) => {
    const select = event.target.closest("[data-submit-on-change]");
    if (select?.form) select.form.requestSubmit();
});
