from django.utils.translation import gettext_lazy as _

HELP_TOPICS = (
    ("account", _("Account & security"), "shield-check", ("account-security",)),
    ("orders", _("Orders & delivery"), "package", ("orders-delivery",)),
    ("payments", _("Payments"), "credit-card", ("payments",)),
    ("refunds", _("Returns & refunds"), "rotate-ccw", ("returns-refunds",)),
    ("shopping", _("Products & shopping"), "shopping-bag", ("shopping-products",)),
    ("support", _("Support"), "headset", ("support",)),
)

def topic_for_slug(slug):
    return next((topic for topic in HELP_TOPICS if slug in topic[3]), HELP_TOPICS[-1])
