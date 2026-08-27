from django import forms
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from TileCompany.uploads import validate_raster_image
from blog.models import Article, ArticleStatus
from products.models import HomepageEditorialSelection, HomepageHero, HomepageMerchandisingMedia


INPUT_CLASS = (
    "w-full rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm "
    "focus:border-stone-900 focus:outline-none focus:ring-2 focus:ring-stone-200"
)


class HomepageHeroForm(forms.ModelForm):
    remove_image = forms.BooleanField(
        label=_("Remove current image"),
        required=False,
    )

    class Meta:
        model = HomepageHero
        fields = ("image", "image_alt", "image_alt_fa", "is_enabled")
        labels = {
            "image": _("Hero image"),
            "image_alt": _("English alt text"),
            "image_alt_fa": _("Persian alt text"),
            "is_enabled": _("Use configured Hero image"),
        }
        widgets = {
            "image": forms.FileInput(attrs={"accept": "image/*", "class": INPUT_CLASS}),
            "image_alt": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "image_alt_fa": forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "rtl"}),
            "is_enabled": forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-stone-300"}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("remove_image") and self.files.get("image"):
            raise forms.ValidationError(_("Upload a replacement or remove the current image, not both."))
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get("image")
        return validate_raster_image(image) if image else image


class HomepageEditorialSelectionForm(forms.ModelForm):
    class Meta:
        model = HomepageEditorialSelection
        fields = ("article",)
        labels = {"article": _("Featured article")}
        widgets = {"article": forms.Select(attrs={"class": INPUT_CLASS})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["article"].required = False
        self.fields["article"].empty_label = _("Automatic selection")
        self.fields["article"].queryset = Article.objects.filter(
            status=ArticleStatus.PUBLISHED,
            published_at__lte=timezone.now(),
        ).order_by("-published_at", "-pk")
        self.fields["article"].label_from_instance = lambda article: f"{article.title} ({article.slug})"


class CategoryStorefrontImageForm(forms.Form):
    image = forms.ImageField(
        label=_("Storefront image"),
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/*", "class": INPUT_CLASS}),
    )
    remove_image = forms.BooleanField(
        label=_("Remove current image"),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("remove_image") and cleaned.get("image"):
            raise forms.ValidationError(_("Upload a replacement or remove the current image, not both."))
        if not cleaned.get("remove_image") and not cleaned.get("image"):
            raise forms.ValidationError(_("Choose an image to upload or select remove."))
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get("image")
        return validate_raster_image(image) if image else image


class HomepageMerchandisingMediaForm(forms.Form):
    """Create a discovery-media record from exactly one canonical target."""

    category = forms.ModelChoiceField(queryset=None, required=False)
    attribute_option = forms.ModelChoiceField(queryset=None, required=False)
    brand = forms.ModelChoiceField(queryset=None, required=False)
    image = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "class": INPUT_CLASS}),
    )
    image_alt = forms.CharField(required=False, max_length=200, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    image_alt_fa = forms.CharField(required=False, max_length=200, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "rtl"}))
    is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-stone-300"}))

    def __init__(self, *args, **kwargs):
        from products.models import AttributeOption, Brand, Category

        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.order_by("sort_order", "name", "id")
        self.fields["attribute_option"].queryset = AttributeOption.objects.filter(is_active=True).order_by("definition__name", "sort_order", "name", "id")
        self.fields["brand"].queryset = Brand.objects.order_by("name", "id")

    def clean(self):
        cleaned = super().clean()
        selected = [
            cleaned.get(name)
            for name in ("category", "attribute_option", "brand")
            if cleaned.get(name) is not None
        ]
        if len(selected) != 1:
            raise forms.ValidationError(_("Choose exactly one homepage merchandising target."))
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get("image")
        return validate_raster_image(image) if image else image


class HomepageMerchandisingMediaUpdateForm(forms.Form):
    """Edit presentation fields only; canonical target identity is immutable."""

    remove_image = forms.BooleanField(label=_("Remove current image"), required=False)
    image = forms.ImageField(
        label=_("Storefront image"),
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "class": INPUT_CLASS}),
    )
    image_alt = forms.CharField(label=_("English alt text"), required=False, max_length=200, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    image_alt_fa = forms.CharField(label=_("Persian alt text"), required=False, max_length=200, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "rtl"}))
    is_active = forms.BooleanField(label=_("Active"), required=False, widget=forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-stone-300"}))

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                "image_alt": instance.image_alt,
                "image_alt_fa": instance.image_alt_fa,
                "is_active": instance.is_active,
            }
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("remove_image") and cleaned.get("image"):
            raise forms.ValidationError(_("Upload a replacement or remove the current image, not both."))
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get("image")
        return validate_raster_image(image) if image else image
