#### dashboard/forms.py

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from orders.models import Order
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


def catalog_choice_label(instance):
    """Keep management choices bilingual without changing model ``__str__``."""
    persian_name = getattr(instance, "name_fa", "")
    return f"{instance.name} — {persian_name}" if persian_name and persian_name.strip() else instance.name


class RefundReviewForm(forms.ModelForm):
    """
    Form used by staff to review a customer refund request.
    The response is shown to the customer after the review.
    """

    refund_response = forms.CharField(
        label=_("Response to Customer"),
        required=True,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": _(
                    "Explain the decision to the customer. "
                    "For example:\n\n"
                    "Your refund has been approved. "
                    "The payment will be returned to your original "
                    "payment method within 3–5 business days."
                ),
                "class": (
                    "w-full rounded-xl border border-stone-300 "
                    "px-4 py-3 focus:border-amber-600 "
                    "focus:outline-none"
                ),
            }
        ),
        help_text=_("This message will be visible to the customer."),
    )

    class Meta:
        model = Order
        fields = (
            "refund_response",
        )

    def clean_refund_response(self):
        response = self.cleaned_data["refund_response"].strip()

        if not response:
            raise forms.ValidationError(
                _("Please provide a response for the customer.")
            )

        return response


class DashboardProductForm(forms.ModelForm):
    """
    Dashboard form for creating and editing products.
    """

    class Meta:
        model = Product

        labels = {
            "name": _("English Name"),
            "name_fa": _("Persian Name"),
            "slug": _("Slug"),
            "sku": _("SKU"),
            "categories": _("Categories"),
            "description": _("English Description"),
            "description_fa": _("Persian Description"),
            "brand": _("Brand"),
            "series_name": _("English Series Name"),
            "series_name_fa": _("Persian Series Name"),
            "designs": _("Designs"),
            "materials": _("Materials"),
            "usages": _("Usages"),
            "tiles_per_box": _("Tiles per Box"),
            "coverage_per_box_m2": _("Coverage per Box (m²)"),
            "image": _("Product Image"),
            "is_active": _("Active"),
            "is_featured": _("Featured"),
        }

        fields = [
            # Core
            "name",
            "name_fa",
            "slug",
            "sku",
            "categories",
            "description",
            "description_fa",

            # Catalog
            "brand",
            "series_name",
            "series_name_fa",
            "designs",
            "materials",
            "usages",

            # Catalog-level packaging only. Selling prices live on variants.
            "tiles_per_box",
            "coverage_per_box_m2",


            # Merchandising
            "image",
            "is_active",
            "is_featured",
        ]

        widgets = {
            "description": forms.Textarea(attrs={"rows": 6}),
            "description_fa": forms.Textarea(attrs={"rows": 6}),
            "categories": forms.CheckboxSelectMultiple(),
            "designs": forms.CheckboxSelectMultiple(),
            "materials": forms.CheckboxSelectMultiple(),
            "usages": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, can_publish=True, **kwargs):
        super().__init__(*args, **kwargs)

        self.can_publish = can_publish
        if not can_publish:
            if not self.instance.pk:
                self.instance.is_active = False
                self.initial["is_active"] = False
            self.fields["is_active"].disabled = True

        for name in ("brand", "categories", "designs", "materials", "usages"):
            self.fields[name].label_from_instance = catalog_choice_label

        for name, field in self.fields.items():

            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue

            field.widget.attrs.setdefault(
                "class",
                (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-4 py-2.5 text-sm "
                    "focus:border-slate-900 "
                    "focus:outline-none "
                    "focus:ring-2 focus:ring-slate-200"
                ),
            )

        for checkbox in ("is_active", "is_featured"):
            self.fields[checkbox].widget.attrs["class"] = (
                "h-4 w-4 rounded border-slate-300 text-slate-900 "
                "focus:ring-slate-900"
            )

        for name in ("name", "description", "series_name", "slug", "sku"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "ltr")
        for name in ("name_fa", "description_fa", "series_name_fa"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "rtl")

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip()

        queryset = Product.objects.filter(slug=slug)

        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise forms.ValidationError(
                _("A product with this slug already exists.")
            )

        return slug

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()

        queryset = Product.objects.filter(sku=sku)

        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise forms.ValidationError(
                _("A product with this SKU already exists.")
            )

        return sku

    def clean(self):
        cleaned_data = super().clean()

        if not self.can_publish and self.is_bound and "is_active" in self.data:
            self.add_error(
                "is_active",
                _("You do not have permission to change this field."),
            )

        return cleaned_data


class DashboardProductVariantForm(forms.ModelForm):
    """
    Dashboard form for creating and editing product variants.
    """

    class Meta:
        model = ProductVariant

        fields = [
            # Identity
            "sku",

            # Variant attributes
            "color",
            "size",
            "finish",
            "length_mm",
            "width_mm",
            "thickness_mm",

            # Packaging
            "tiles_per_box",
            "coverage_per_box_m2",

            # Selling unit (currency-independent)
            "price_unit",

            # Inventory
            "stock",
            "low_stock_threshold",

            # Display
            "sort_order",
            "is_active",
        ]
        labels = {
            "sku": _("SKU"),
            "color": _("Color"),
            "size": _("Size"),
            "finish": _("Finish"),
            "length_mm": _("Length (mm)"),
            "width_mm": _("Width (mm)"),
            "thickness_mm": _("Thickness (mm)"),
            "tiles_per_box": _("Tiles per Box"),
            "coverage_per_box_m2": _("Coverage per Box (m²)"),
            "price_unit": _("Price Unit"),
            "stock": _("Stock"),
            "low_stock_threshold": _("Low Stock Threshold"),
            "sort_order": _("Sort Order"),
            "is_active": _("Active"),
        }

    def __init__(self, *args, can_change_inventory=True, can_change_pricing=True, can_publish=True, **kwargs):
        super().__init__(*args, **kwargs)

        self.can_change_inventory = can_change_inventory
        self.can_change_pricing = can_change_pricing
        self.can_publish = can_publish

        self.fields["price_unit"].choices = [
            (value, label)
            for value, label in ProductVariant.PriceUnit.choices
            if value != ProductVariant.PriceUnit.UNIT
        ]

        for name in ("color", "size", "finish"):
            self.fields[name].label_from_instance = catalog_choice_label

        for currency in Currency:
            prefix = currency.value.lower()
            currency_label = _("Toman") if currency == Currency.TOMAN else currency.label
            self.fields[f"{prefix}_amount"] = forms.DecimalField(label=_("%(currency)s selling price") % {"currency": currency_label}, required=False, max_digits=14, decimal_places=2)
            self.fields[f"{prefix}_compare_at_amount"] = forms.DecimalField(label=_("%(currency)s compare-at price") % {"currency": currency_label}, required=False, max_digits=14, decimal_places=2)

        if self.instance.pk:
            prices = {price.currency: price for price in self.instance.prices.all()}
            for currency in Currency:
                if price := prices.get(currency):
                    prefix = currency.value.lower()
                    self.initial[f"{prefix}_amount"] = price.amount
                    self.initial[f"{prefix}_compare_at_amount"] = price.compare_at_amount

        for field in self.fields.values():
            field.widget.attrs.setdefault(
                "class",
                (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-4 py-2.5 text-sm "
                    "focus:border-slate-900 "
                    "focus:outline-none "
                    "focus:ring-2 focus:ring-slate-200"
                ),
            )

        self.fields["is_active"].widget.attrs["class"] = (
            "h-4 w-4 rounded border-slate-300 "
            "text-slate-900 focus:ring-slate-900"
        )
        self.fields["sku"].widget.attrs.setdefault("dir", "ltr")
        self.fields["low_stock_threshold"].required = False

        if not can_change_inventory:
            self.fields["stock"].disabled = True
            self.fields["low_stock_threshold"].disabled = True
        if not can_publish:
            if not self.instance.pk:
                self.instance.is_active = False
                self.initial["is_active"] = False
            self.fields["is_active"].disabled = True
        if not can_change_pricing:
            for currency in Currency:
                prefix = currency.value.lower()
                self.fields[f"{prefix}_amount"].disabled = True
                self.fields[f"{prefix}_compare_at_amount"].disabled = True

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()

        queryset = ProductVariant.objects.filter(sku=sku)

        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise forms.ValidationError(
                _("A variant with this SKU already exists.")
            )

        return sku

    def clean(self):
        cleaned_data = super().clean()

        if not self.can_change_inventory:
            for field_name in ("stock", "low_stock_threshold"):
                if self.is_bound and field_name in self.data:
                    self.add_error(
                        field_name,
                        _("You do not have permission to change this field."),
                    )

        if not self.can_change_pricing:
            for currency in Currency:
                prefix = currency.value.lower()
                for field_name in (f"{prefix}_amount", f"{prefix}_compare_at_amount"):
                    if self.is_bound and field_name in self.data:
                        self.add_error(
                            field_name,
                            _("You do not have permission to change this field."),
                        )

        if not self.can_publish and self.is_bound and "is_active" in self.data:
            self.add_error(
                "is_active",
                _("You do not have permission to change this field."),
            )

        for currency in Currency:
            prefix = currency.value.lower()
            amount = cleaned_data.get(f"{prefix}_amount")
            compare_at_amount = cleaned_data.get(f"{prefix}_compare_at_amount")
            if compare_at_amount is not None and amount is None:
                self.add_error(f"{prefix}_amount", _("A selling price is required when compare-at price is set."))
            elif amount is not None:
                candidate = VariantPrice(
                    variant=self.instance if self.instance.pk else None,
                    currency=currency,
                    amount=amount,
                    compare_at_amount=compare_at_amount,
                )
                try:
                    candidate.clean()
                except ValidationError as error:
                    for field, messages in error.message_dict.items():
                        self.add_error(f"{prefix}_{field}", messages)

        length = cleaned_data.get("length_mm")
        width = cleaned_data.get("width_mm")

        if (length and not width) or (width and not length):
            raise forms.ValidationError(
                _("Length and width must both be provided.")
            )

        bulk_tiles = cleaned_data.get("tiles_per_box")
        coverage = cleaned_data.get("coverage_per_box_m2")

        if cleaned_data.get("low_stock_threshold") is None:
            cleaned_data["low_stock_threshold"] = ProductVariant._meta.get_field(
                "low_stock_threshold"
            ).get_default()

        if coverage and not bulk_tiles:
            self.add_error(
                "tiles_per_box",
                _("Tiles per box should be specified when coverage is entered."),
            )

        return cleaned_data

    def currency_price_data(self):
        """Return explicit, independently managed price values for this variant."""

        for currency in Currency:
            prefix = currency.value.lower()
            yield (
                currency,
                self.cleaned_data.get(f"{prefix}_amount"),
                self.cleaned_data.get(f"{prefix}_compare_at_amount"),
            )

    @staticmethod
    def _catalog_choice_label(instance):
        """Keep management choices bilingual without changing model ``__str__``."""
        persian_name = getattr(instance, "name_fa", "")
        return f"{instance.name} — {persian_name}" if persian_name and persian_name.strip() else instance.name

        for name in ("name", "description", "series_name", "slug", "sku"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "ltr")
        for name in ("name_fa", "description_fa", "series_name_fa"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "rtl")


class CatalogTermForm(forms.ModelForm):
    """Shared dashboard presentation and slug behavior for product lookups."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        labels = {
            "name": _("English Name"),
            "name_fa": _("Persian Name"),
            "slug": _("Slug"),
            "length_mm": _("Length (mm)"),
            "width_mm": _("Width (mm)"),
        }
        for name, label in labels.items():
            if name in self.fields:
                self.fields[name].label = label

        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-900")
            else:
                field.widget.attrs.setdefault("class", "w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm transition focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-200")
        for name in ("name", "slug"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "ltr")
        for name in ("name_fa",):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("dir", "rtl")

    def clean_slug(self):
        from django.utils.text import slugify
        slug = self.cleaned_data["slug"].strip() or slugify(self.cleaned_data.get("name", ""))
        if not slug:
            raise forms.ValidationError(_("Provide a name or a URL slug."))
        return slug


def catalog_term_form(model):
    fields = ("name", "name_fa", "slug", "length_mm", "width_mm") if model.__name__ == "Size" else ("name", "name_fa", "slug")
    return forms.modelform_factory(model, form=CatalogTermForm, fields=fields)
