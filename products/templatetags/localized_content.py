from django import template

from products.presentation import get_localized_value


register = template.Library()


@register.filter
def localized(obj, field_name):
    """Render an existing ``*_fa`` field under Persian with safe fallback."""
    if obj is None:
        return ""
    return get_localized_value(obj, field_name)
