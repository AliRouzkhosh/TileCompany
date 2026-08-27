document.addEventListener("DOMContentLoaded", () => {

    const selectAll = document.getElementById("select-all");
    const rowCheckboxes = document.querySelectorAll(".order-checkbox");

    const selectedCount = document.getElementById("selected-count");
    const bulkAction = document.getElementById("bulk-action");
    const bulkButton = document.getElementById("bulk-submit");
    const bulkForm = document.getElementById("bulkActionForm");
    const bulkI18n = document.getElementById("dashboard-bulk-i18n");

    const messages = {
        selectAction: bulkI18n?.dataset.selectAction || "Please select a bulk action.",
        selectOrders: bulkI18n?.dataset.selectOrders || "Please select at least one order.",
        confirmAction: bulkI18n?.dataset.confirmAction || "Are you sure you want to apply this action to the selected orders?",
    };

    function updateSelection() {

        const checked = document.querySelectorAll(
            ".order-checkbox:checked"
        ).length;

        if (selectedCount) {
            selectedCount.textContent = checked;
        }

        if (selectAll) {

            const total = rowCheckboxes.length;

            selectAll.checked =
                total > 0 && checked === total;

            selectAll.indeterminate =
                checked > 0 && checked < total;
        }
    }

    if (selectAll) {

        selectAll.addEventListener("change", () => {

            rowCheckboxes.forEach((checkbox) => {

                checkbox.checked = selectAll.checked;

            });

            updateSelection();

        });

    }

    rowCheckboxes.forEach((checkbox) => {

        checkbox.addEventListener(
            "change",
            updateSelection,
        );

    });

    if (bulkForm) {

        bulkForm.addEventListener("submit", (event) => {

            if (!bulkAction || !bulkAction.value) {

                event.preventDefault();

                alert(messages.selectAction);

                return;

            }

            const checked = document.querySelectorAll(
                ".order-checkbox:checked"
            ).length;

            if (!checked) {

                event.preventDefault();

                alert(messages.selectOrders);

                return;

            }

            const confirmed = confirm(messages.confirmAction);

            if (!confirmed) {
                event.preventDefault();
            }

        });

    }

    document.querySelectorAll(".js-confirm-action").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!confirm(form.dataset.confirmMessage)) {
                event.preventDefault();
            }
        });
    });

    updateSelection();

});
