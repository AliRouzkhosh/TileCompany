document.addEventListener("DOMContentLoaded", () => {

    document.querySelectorAll(".swiper").forEach((el) => {

        let config = {
            slidesPerView: "auto",
            spaceBetween: 16,
            watchOverflow: true,

            navigation: {
                nextEl: el.querySelector(".swiper-button-next"),
                prevEl: el.querySelector(".swiper-button-prev"),
            },

            pagination: {
                el: el.querySelector(".swiper-pagination"),
                clickable: true,
            },

            breakpoints: {
                640: { slidesPerView: 2.15, spaceBetween: 20 },
                768: { slidesPerView: 2.5, spaceBetween: 20 },
                1024: { slidesPerView: 3, spaceBetween: 24 },
                1280: { slidesPerView: 4, spaceBetween: 24 }
            }
        };

        if (el.classList.contains("category-swiper")) {
            config.slidesPerView = "auto";
            config.breakpoints = {
                640: { slidesPerView: 2.2, spaceBetween: 20 },
                768: { slidesPerView: 3, spaceBetween: 20 },
                1024: { slidesPerView: 3.5, spaceBetween: 24 },
                1280: { slidesPerView: 4, spaceBetween: 24 }
            };
        }

        if (el.classList.contains("blog-swiper")) {
            config.slidesPerView = "auto";
            config.breakpoints = {
                640: { slidesPerView: 1.6, spaceBetween: 20 },
                768: { slidesPerView: 2, spaceBetween: 20 },
                1024: { slidesPerView: 2.4, spaceBetween: 24 },
                1280: { slidesPerView: 3, spaceBetween: 24 }
            };
        }

        if (el.classList.contains("related-product-swiper")) {
            config.breakpoints = {
                640: { slidesPerView: "auto", spaceBetween: 20 },
                1024: { slidesPerView: "auto", spaceBetween: 24 }
            };
        }

        new Swiper(el, config);

    });

});
