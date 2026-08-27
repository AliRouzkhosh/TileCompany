document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("[data-destructive-dialog]");
  if (!dialog) return;
  const title = dialog.querySelector("#destructiveConfirmTitle");
  const body = dialog.querySelector("#destructiveConfirmBody");
  const confirm = dialog.querySelector("[data-destructive-confirm-action]");
  const cancel = dialog.querySelector("[data-destructive-cancel]");
  let trigger = null;

  const reset = () => {
    trigger = null;
    title.textContent = "";
    body.textContent = "";
    confirm.disabled = false;
    confirm.removeAttribute("aria-busy");
  };
  const open = (element) => {
    trigger = element;
    title.textContent = element.dataset.confirmTitle || "";
    body.textContent = element.dataset.confirmBody || "";
    confirm.textContent = element.dataset.confirmLabel || "";
    dialog.showModal();
    cancel.focus();
  };
  document.addEventListener("click", (event) => {
    const element = event.target.closest("[data-destructive-confirm]");
    if (!element || dialog.contains(element)) return;
    event.preventDefault();
    open(element);
  });
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-destructive-confirm]");
    if (!form || form.dataset.confirmed === "true") return;
    event.preventDefault();
    open(form);
  });
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    const focusTarget = trigger;
    reset();
    if (focusTarget?.isConnected) focusTarget.focus();
  });
  confirm.addEventListener("click", () => {
    const active = trigger;
    if (!active) return;
    confirm.disabled = true;
    confirm.setAttribute("aria-busy", "true");
    dialog.close();
    if (active.tagName === "FORM") {
      active.dataset.confirmed = "true";
      active.requestSubmit();
    } else {
      active.dataset.confirmed = "true";
      active.click();
    }
  });
});
