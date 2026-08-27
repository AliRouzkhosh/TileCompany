# Persian design language

## Typography

Persian UI uses the self-hosted Vazirmatn family (Regular 400, Medium 500,
SemiBold 600, and Bold 700). `html[lang="fa"]` selects it centrally; English
continues to use the existing Tailwind system font stack. Persian body copy
uses a slightly roomier line height, while headings, controls, and labels use
a tighter value to preserve the existing hierarchy.

## Canonical terminology

| English                                  | Persian                                |
|------------------------------------------|----------------------------------------|
| Store / Shop                             | فروشگاه                                |
| Home                                     | صفحهٔ اصلی                             |
| Products                                 | محصولات                                |
| Categories                               | دسته‌بندی‌ها                           |
| Brands                                   | برندها                                 |
| Search                                   | جست‌وجو                                |
| Filters                                  | فیلترها                                |
| Sort                                     | مرتب‌سازی                              |
| Price                                    | قیمت                                   |
| Discount                                 | تخفیف                                  |
| Availability                             | موجودی                                 |
| In stock / Out of stock                  | موجود / ناموجود                        |
| Variant                                  | گونه                                   |
| Color / Size / Material / Finish / Usage | رنگ / اندازه / جنس / پوشش سطح / کاربرد |
| Quantity                                 | تعداد                                  |
| Add to cart / Cart                       | افزودن به سبد خرید / سبد خرید          |
| Checkout                                 | تسویه‌حساب                             |
| Shipping / Payment                       | ارسال / پرداخت                         |
| Order / Orders / Order status            | سفارش / سفارش‌ها / وضعیت سفارش         |
| Refund                                   | بازگشت وجه                             |
| Account / Profile / Addresses            | حساب کاربری / پروفایل / نشانی‌ها       |
| Wishlist                                 | علاقه‌مندی‌ها                          |
| Support / Ticket                         | پشتیبانی / تیکت                        |
| Blog / Article / Review                  | وبلاگ / مقاله / دیدگاه                 |
| Dashboard / Inventory                    | داشبورد / موجودی کالا                  |
| View all/ View offers/Browse All         | دیدن همه/ دیدن تخفیفات/ مشاهده همه     |

## Writing conventions

- Use Persian `ی` and `ک`, never Arabic `ي` or `ك`.
- Use نیم‌فاصله in established compounds, including «می‌شود»، «صفحهٔ اصلی»،
  «تسویه‌حساب»، and «دسته‌بندی».
- Persian punctuation attaches to the preceding word. Keep spaces outside
  parentheses; isolate Latin content in mixed-direction parentheses with
  `bdi` or `dir="ltr"`.
- Use `٪` after a Persian percentage with no separating space. Use `×` for
  dimensions and `:` with one regular space after it when it introduces a
  value.
- Prefer Persian quotation marks «...» in future localized prose.

## Values, identifiers, currency, and measurements

- Domain values stay numeric; Phase 6 owns comprehensive Persian-digit and
  date formatting. UI labels may localize, but storage and calculations never
  do.
- SKU, order, tracking, payment-reference, email, URL, and model-code values
  are rendered as LTR-isolated content (`<bdi dir="ltr">…</bdi>` or an
  equivalent reusable component).
- USD remains `$1,250.00` in every language. IRT is presented as `Toman` in
  English and `تومان` in Persian. Language never selects currency.
- Discounts use `Save 20%` in English and the future Persian equivalent
  `۲۰٪ تخفیف`; calculations and sale-price ordering remain unchanged.
- Use `mm`, `cm`, `m²`, `kg`, and `×` consistently. Mixed values such as
  `60 × 120 cm`, `1.44 m²`, and `SKU-1045` must be isolated LTR in Persian
  UI.

## Phase boundaries

Phase 4 applies gettext to storefront UI and adopts this vocabulary. Database
content (product/catalog/blog fields) needs an explicit localization strategy;
gettext does not translate it. Phase 6 will introduce deeper Persian digit,
date, and locale-sensitive numeric formatting.
