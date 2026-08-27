from django.core.management import call_command
from django.core.management.base import BaseCommand


CATEGORIES = (
    ("orders-delivery", "Orders & Delivery", "سفارش و ارسال", "package"),
    ("payments", "Payments", "پرداخت", "credit-card"),
    ("returns-refunds", "Returns & Refunds", "مرجوعی و بازپرداخت", "rotate-ccw"),
    ("account-security", "Account & Security", "حساب کاربری و امنیت", "shield-check"),
    ("shopping-products", "Shopping & Products", "خرید و محصولات", "shopping-bag"),
    ("support", "Support", "پشتیبانی", "headset"),
)


ARTICLES = (
    ("orders-delivery", "How do I track my order?", "چگونه سفارشم را پیگیری کنم؟", "Open My Orders from your account to view an order and its current status."),
    ("orders-delivery", "What do the order statuses mean?", "وضعیت‌های سفارش چه معنایی دارند؟", "Order status is shown on the order detail page and is updated as the order moves through its recorded workflow."),
    ("orders-delivery", "Can I change my delivery address after placing an order?", "آیا پس از ثبت سفارش می‌توانم نشانی ارسال را تغییر دهم؟", "Saved addresses can be managed in your account. For an existing order, create a support ticket so the team can review the request."),
    ("orders-delivery", "What happens while my order is being prepared and shipped?", "هنگام آماده‌سازی و ارسال سفارش چه اتفاقی می‌افتد؟", "Check My Orders for the latest recorded status and any order-specific information."),
    ("payments", "Which payment methods are available?", "چه روش‌های پرداختی در دسترس هستند؟", "Checkout shows the payment methods available for the current order and environment, including Cash on Delivery where offered."),
    ("payments", "What should I do if my payment fails?", "اگر پرداخت ناموفق بود چه کار کنم؟", "Return to the unpaid order and use its available payment action. If the problem continues, create a support ticket with the order reference."),
    ("payments", "How do I complete payment for an unpaid order?", "چگونه پرداخت سفارش پرداخت‌نشده را کامل کنم؟", "Open the unpaid order from My Orders and continue with the payment option shown for that order."),
    ("payments", "How does Cash on Delivery work?", "پرداخت در محل چگونه کار می‌کند؟", "For an eligible Cash on Delivery order, payment is due on delivery. The order remains unpaid until collection is recorded."),
    ("returns-refunds", "How do I request a refund?", "چگونه درخواست بازپرداخت ثبت کنم؟", "Open an eligible delivered order and use Request a refund. The form records the reason, your message, and optional supporting files."),
    ("returns-refunds", "What information should I provide with a refund request?", "در درخواست بازپرداخت چه اطلاعاتی ارائه کنم؟", "Choose the available reason and explain the issue clearly. Attach supporting files when they help explain the request."),
    ("returns-refunds", "Why might support ask for more information?", "چرا پشتیبانی ممکن است اطلاعات بیشتری بخواهد؟", "A refund can be marked as needing information. Support then continues the clarification in its linked ticket while the refund remains under review."),
    ("returns-refunds", "How do I follow the status of my refund request?", "چگونه وضعیت درخواست بازپرداخت را پیگیری کنم؟", "Open the related order to see its refund status. If more information is needed, follow and reply in the linked support ticket."),
    ("account-security", "How do I update my profile information?", "چگونه اطلاعات پروفایل را به‌روزرسانی کنم؟", "Open Profile from My Account, update the available fields, and save your changes."),
    ("account-security", "How do I manage my saved addresses?", "چگونه نشانی‌های ذخیره‌شده را مدیریت کنم؟", "Open Addresses from My Account to add, edit, or remove saved delivery addresses."),
    ("account-security", "How do I reset my password?", "چگونه گذرواژه‌ام را بازنشانی کنم؟", "Use Forgot password on the sign-in page and follow the reset link sent through the existing password-reset flow."),
    ("account-security", "Where can I see account notifications?", "اعلان‌های حساب کاربری را کجا ببینم؟", "Open Notifications from My Account to review your account and support updates."),
    ("shopping-products", "How do product options and variants work?", "گزینه‌ها و گونه‌های محصول چگونه کار می‌کنند؟", "Select the available product options on a product page. Price, availability, and purchase choices follow the selected variant when one exists."),
    ("shopping-products", "What does “Only N left” mean?", "«فقط N عدد باقی مانده» چه معنایی دارد؟", "A low-stock message is shown only for sellable stock levels from one to four. It is not shown when five or more are available."),
    ("shopping-products", "How does Wishlist work?", "فهرست علاقه‌مندی‌ها چگونه کار می‌کند؟", "Add or remove products from Wishlist while signed in. Wishlist saves products, not individual variants."),
    ("shopping-products", "How are sale prices and discounts shown?", "قیمت فروش و تخفیف چگونه نمایش داده می‌شود؟", "When a product or variant has a current sale price, the storefront presents the current price alongside the relevant reference price."),
    ("support", "How do I create a support ticket?", "چگونه تیکت پشتیبانی ایجاد کنم؟", "Choose Create support ticket, select a category, add a clear subject and message, and optionally link one of your orders."),
    ("support", "What do support ticket statuses mean?", "وضعیت‌های تیکت پشتیبانی چه معنایی دارند؟", "Open is a new ticket. Waiting for you needs your reply; Waiting for support is being reviewed. Resolved and Closed show completed states; closed tickets cannot receive replies."),
    ("support", "How do I reply to a support ticket?", "چگونه به تیکت پشتیبانی پاسخ دهم؟", "Open the ticket from My Support Tickets and use Reply. A customer reply moves the conversation to Waiting for support."),
    ("support", "How do I attach files to a support request?", "چگونه به درخواست پشتیبانی فایل پیوست کنم؟", "Use the attachment field when creating or replying to a ticket. JPEG, PNG, WEBP, and PDF files are accepted up to 5 MB per file."),
)

FA_SUMMARIES = (
    "وضعیت و اقدام‌های مرتبط را از بخش حساب کاربری خود دنبال کنید.",
    "این راهنما مراحل موجود در فروشگاه را به‌صورت کوتاه توضیح می‌دهد.",
)

BODY_EN = {
    "orders-delivery": "## In your account\nOpen My Orders and select the order to see its recorded status and details.\n\n## What to do next\nUse the information shown on the order page. If your request concerns an order already placed, create a support ticket and include the order reference.",
    "payments": "## Check the order first\nOpen the unpaid order from My Orders. A failed or cancelled online payment leaves the order unpaid, and the order page shows the available recovery action.\n\n## Important\nOnly one retryable unpaid online order is kept for a customer. Cash on Delivery is not paid at checkout; payment is due on delivery.",
    "returns-refunds": "## Requesting a refund\nEligible paid delivered orders can request a refund within the recorded 72-hour delivery window. Add the available reason and a clear message.\n\n## If more information is needed\nSupport may request evidence through the linked ticket. Replying supplies information; it does not itself approve the refund. Refund evidence follows the order upload rules: JPEG, PNG, WEBP, or PDF up to 10 MB per file.",
    "account-security": "## Use My Account\nProfile, Addresses, and Notifications each have their own Account destination. Update saved information there and save the available changes.\n\n## Keep access secure\nUse the password-reset path from sign in if you cannot access your account. Do not share password-reset links.",
    "shopping-products": "## Product information\nProduct pages show the purchase choices, current availability, and current price for the selected product or variant where applicable.\n\n## Availability and saved products\nLow-stock wording is shown only when total sellable stock across eligible variants is one to four; five is not a low-stock message. Wishlist saves products rather than variants.",
    "support": "## Keep the conversation together\nCreate a ticket with a clear subject and message, then follow it from My Support Tickets. Open tickets are new; Waiting for you needs your reply; Waiting for support is being reviewed. Closed tickets cannot receive replies.\n\n## Files\nTicket uploads accept JPEG, PNG, WEBP, and PDF files up to 5 MB each.",
}
BODY_FA = {
    "orders-delivery": "## در حساب کاربری\nاز بخش سفارش‌ها، سفارش موردنظر را باز کنید تا وضعیت ثبت‌شده و جزئیات آن را ببینید.\n\n## گام بعدی\nاز اطلاعات همان صفحه استفاده کنید. اگر درخواست شما مربوط به سفارش ثبت‌شده است، یک تیکت پشتیبانی ایجاد کنید و شمارهٔ سفارش را بنویسید.",
    "payments": "## ابتدا سفارش را بررسی کنید\nسفارش پرداخت‌نشده را از بخش سفارش‌ها باز کنید. پرداخت ناموفق یا لغوشده سفارش را پرداخت‌نشده نگه می‌دارد و اقدام موجود در صفحهٔ سفارش نمایش داده می‌شود.\n\n## نکتهٔ مهم\nبرای هر مشتری فقط یک سفارش آنلاین پرداخت‌نشدهٔ قابل‌پیگیری نگه داشته می‌شود. پرداخت در محل در زمان ثبت سفارش پرداخت‌شده محسوب نمی‌شود.",
    "returns-refunds": "## ثبت درخواست بازپرداخت\nبرای سفارش پرداخت‌شده و تحویل‌شدهٔ واجد شرایط، تا ۷۲ ساعت پس از تحویل می‌توانید درخواست بازپرداخت ثبت کنید. دلیل و توضیح روشن بنویسید.\n\n## اگر اطلاعات بیشتری لازم باشد\nپشتیبانی از طریق تیکت پیوست‌شده مدرک بیشتری درخواست می‌کند. پاسخ شما فقط اطلاعات را تکمیل می‌کند و به‌تنهایی بازپرداخت را تأیید نمی‌کند. فایل‌های JPEG، PNG، WEBP و PDF تا ۱۰ مگابایت برای هر فایل پذیرفته می‌شوند.",
    "account-security": "## از حساب کاربری استفاده کنید\nپروفایل، نشانی‌ها و اعلان‌ها هر کدام بخش جداگانه‌ای در حساب کاربری دارند. اطلاعات ذخیره‌شده را همان‌جا ویرایش و ثبت کنید.\n\n## امنیت دسترسی\nاگر به حساب خود دسترسی ندارید، از مسیر بازنشانی گذرواژه در صفحهٔ ورود استفاده کنید و پیوند بازنشانی را در اختیار دیگران نگذارید.",
    "shopping-products": "## اطلاعات محصول\nصفحهٔ محصول، انتخاب‌های خرید، موجودی فعلی و قیمت فعلی محصول یا گونهٔ انتخاب‌شده را در صورت وجود نشان می‌دهد.\n\n## موجودی و علاقه‌مندی‌ها\nپیام کمبود موجودی فقط وقتی مجموع موجودی قابل‌فروش گونه‌های واجد شرایط بین ۱ تا ۴ باشد نمایش داده می‌شود؛ عدد ۵ پیام کمبود ندارد. فهرست علاقه‌مندی‌ها محصول را ذخیره می‌کند، نه گونه را.",
    "support": "## گفت‌وگو را یک‌جا نگه دارید\nتیکت را با عنوان و پیام روشن ایجاد کنید و آن را از بخش تیکت‌های پشتیبانی من دنبال کنید. باز یعنی تیکت تازه است؛ در انتظار پاسخ شما یعنی پاسخ لازم است؛ در انتظار پشتیبانی یعنی در حال بررسی است. تیکت بسته پاسخ نمی‌پذیرد.\n\n## فایل‌ها\nبرای تیکت، فایل‌های JPEG، PNG، WEBP و PDF تا ۵ مگابایت برای هر فایل پذیرفته می‌شوند.",
}


class Command(BaseCommand):
    help = "Deprecated compatibility alias for seed_launch_help --apply."

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("seed_help_center is deprecated; use seed_launch_help (dry run by default)."))
        call_command("seed_launch_help", "--apply", stdout=self.stdout, stderr=self.stderr)
