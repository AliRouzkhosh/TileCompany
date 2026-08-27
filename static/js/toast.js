(function () {

    window.showToast = function (
        message,
        type = "success",
        duration = 3000
    ) {

        const container =
            document.getElementById("toastContainer");

        if (!container)
            return;

        const toast = document.createElement("div");

        const colors = {
            success:
                "bg-green-600 border-green-700",
            error:
                "bg-red-600 border-red-700",
            warning:
                "bg-amber-500 border-amber-600",
            info:
                "bg-stone-800 border-stone-900"
        };

        toast.className = `
            pointer-events-auto
            min-w-[280px]
            max-w-sm
            rounded-2xl
            border
            px-5
            py-4
            text-white
            shadow-xl
            transition-all
            duration-300
            toast-offscreen
            opacity-0
            ${colors[type] || colors.info}
        `;

        toast.textContent = message;

        container.appendChild(toast);

        requestAnimationFrame(() => {

            toast.classList.remove(
                "toast-offscreen",
                "opacity-0"
            );

        });

        setTimeout(() => {

            toast.classList.add(
                "toast-offscreen",
                "opacity-0"
            );

            setTimeout(() => {

                toast.remove();

            }, 300);

        }, duration);

    };

})();
