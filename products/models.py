######### product/models.py ###############

from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from .currency import Currency
from .catalog_domain import exactly_one_typed_value_condition
from .storage import private_inventory_import_storage


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    show_in_navigation = models.BooleanField(default=True)
    storefront_image = models.ImageField(
        upload_to="storefront/categories/",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name_plural = "Categories"
        constraints = [
            models.CheckConstraint(
                condition=~Q(parent=F("pk")),
                name="category_parent_not_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["parent", "is_active", "show_in_navigation", "sort_order"],
                name="category_navigation_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.parent_id is None:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": _("A category cannot be its own parent.")})

        ancestor = self.parent
        seen = {self.pk} if self.pk else set()
        while ancestor is not None:
            if ancestor.pk in seen:
                raise ValidationError({"parent": _("Category hierarchy cannot contain a cycle.")})
            seen.add(ancestor.pk)
            ancestor = ancestor.parent

    def __str__(self):
        return self.name


class HomepageHero(models.Model):
    """The single operator-managed homepage hero presentation record."""

    PRIMARY_KEY = "primary"

    key = models.CharField(
        max_length=32,
        unique=True,
        default=PRIMARY_KEY,
        editable=False,
    )
    image = models.ImageField(
        upload_to="storefront/homepage/",
        blank=True,
        null=True,
    )
    image_alt = models.CharField(max_length=200, blank=True)
    image_alt_fa = models.CharField(max_length=200, blank=True)
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Homepage hero"
        verbose_name_plural = "Homepage hero"
        constraints = [
            models.CheckConstraint(
                condition=Q(key="primary"),
                name="homepage_hero_primary_key_only",
            ),
        ]

    def __str__(self):
        return "Homepage hero"


class HomepageEditorialSelection(models.Model):
    """The single optional operator-selected Article for the editorial slot."""

    PRIMARY_KEY = "primary"

    key = models.CharField(max_length=32, unique=True, default=PRIMARY_KEY, editable=False)
    article = models.ForeignKey(
        "blog.Article",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homepage_editorial_selections",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Homepage editorial selection"
        verbose_name_plural = "Homepage editorial selection"
        constraints = [
            models.CheckConstraint(
                condition=Q(key="primary"),
                name="homepage_editorial_primary_key_only",
            ),
        ]

    def __str__(self):
        return "Homepage editorial selection"


class HomepageMerchandisingMedia(models.Model):
    """Optional homepage-only imagery for canonical discovery targets."""

    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.CASCADE,
        related_name="homepage_merchandising_media",
    )
    attribute_option = models.ForeignKey(
        "AttributeOption", null=True, blank=True, on_delete=models.CASCADE,
        related_name="homepage_merchandising_media",
    )
    brand = models.ForeignKey(
        "Brand", null=True, blank=True, on_delete=models.CASCADE,
        related_name="homepage_merchandising_media",
    )
    image = models.ImageField(upload_to="storefront/discovery/", blank=True, null=True)
    image_alt = models.CharField(max_length=200, blank=True)
    image_alt_fa = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Homepage merchandising media"
        verbose_name_plural = "Homepage merchandising media"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(category__isnull=False, attribute_option__isnull=True, brand__isnull=True)
                    | Q(category__isnull=True, attribute_option__isnull=False, brand__isnull=True)
                    | Q(category__isnull=True, attribute_option__isnull=True, brand__isnull=False)
                ),
                name="homepage_merchandising_media_exactly_one_target",
            ),
            models.UniqueConstraint(fields=["category"], condition=Q(category__isnull=False), name="homepage_merchandising_media_one_category"),
            models.UniqueConstraint(fields=["attribute_option"], condition=Q(attribute_option__isnull=False), name="homepage_merchandising_media_one_option"),
            models.UniqueConstraint(fields=["brand"], condition=Q(brand__isnull=False), name="homepage_merchandising_media_one_brand"),
        ]

    def clean(self):
        super().clean()
        if sum(bool(value) for value in (self.category_id, self.attribute_option_id, self.brand_id)) != 1:
            raise ValidationError(_("Choose exactly one homepage merchandising target."))

    def __str__(self):
        return self.image.name if self.image else _("Homepage merchandising media")

class Brand(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Design(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Usage(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Material(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Color(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Size(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["length_mm", "width_mm", "name"]

    def __str__(self):
        return self.name


class Finish(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductType(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    name_fa = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]

    def clean(self):
        super().clean()
        if self.pk:
            previous_code = ProductType.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if previous_code != self.code and (
                self.products.exists() or self.attribute_bindings.exists()
            ):
                raise ValidationError({"code": _("Code cannot change after the product type is referenced.")})

    def __str__(self):
        return self.name


class AttributeDefinition(models.Model):
    class DataType(models.TextChoices):
        OPTION = "option", _("Option")
        TEXT = "text", _("Text")
        INTEGER = "integer", _("Integer")
        DECIMAL = "decimal", _("Decimal")
        BOOLEAN = "boolean", _("Boolean")

    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    name_fa = models.CharField(max_length=100, blank=True)
    data_type = models.CharField(max_length=12, choices=DataType.choices)
    unit = models.CharField(max_length=30, blank=True)
    validation_rules = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def clean(self):
        super().clean()
        from .catalog_domain import validate_definition_rules

        validate_definition_rules(self)
        if self.pk:
            previous_code = AttributeDefinition.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if previous_code != self.code and (
                self.options.exists() or self.product_type_bindings.exists()
            ):
                raise ValidationError({"code": _("Code cannot change after the definition is referenced.")})

    def __str__(self):
        return self.name


class AttributeOption(models.Model):
    definition = models.ForeignKey(
        AttributeDefinition,
        on_delete=models.PROTECT,
        related_name="options",
    )
    code = models.SlugField(max_length=100)
    name = models.CharField(max_length=100)
    name_fa = models.CharField(max_length=100, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    presentation_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "code"],
                name="unique_attribute_option_code",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.definition_id and self.definition.data_type != AttributeDefinition.DataType.OPTION:
            errors["definition"] = _("Only option attributes can own controlled options.")
        if not isinstance(self.presentation_metadata, dict):
            errors["presentation_metadata"] = _("Presentation metadata must be an object.")
        if self.pk:
            previous_code = AttributeOption.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if previous_code != self.code and (
                self.product_values.exists()
                or self.variant_values.exists()
                or self.applicability_restrictions.exists()
            ):
                errors["code"] = _("Code cannot change after the option is referenced.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class ProductTypeAttribute(models.Model):
    class Scope(models.TextChoices):
        PRODUCT = "product", _("Product")
        VARIANT = "variant", _("Variant")

    class Cardinality(models.TextChoices):
        SINGLE = "single", _("Single")
        MULTIPLE = "multiple", _("Multiple")

    class FacetMode(models.TextChoices):
        TERMS = "terms", _("Terms")
        RANGE = "range", _("Range")

    product_type = models.ForeignKey(
        ProductType,
        on_delete=models.PROTECT,
        related_name="attribute_bindings",
    )
    definition = models.ForeignKey(
        AttributeDefinition,
        on_delete=models.PROTECT,
        related_name="product_type_bindings",
    )
    scope = models.CharField(max_length=10, choices=Scope.choices)
    cardinality = models.CharField(
        max_length=10,
        choices=Cardinality.choices,
        default=Cardinality.SINGLE,
    )
    is_required = models.BooleanField(default=False)
    is_variant_dimension = models.BooleanField(default=False)
    is_filterable = models.BooleanField(default=False)
    facet_mode = models.CharField(
        max_length=10,
        choices=FacetMode.choices,
        null=True,
        blank=True,
    )
    is_searchable = models.BooleanField(default=False)
    show_on_storefront = models.BooleanField(default=True)
    show_on_product_card = models.BooleanField(default=False)
    show_in_specifications = models.BooleanField(default=True)
    include_in_purchase_snapshot = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_type", "definition"],
                name="unique_product_type_attribute",
            ),
            models.CheckConstraint(
                condition=Q(scope="variant") | Q(is_variant_dimension=False),
                name="variant_dimension_requires_variant_scope",
            ),
            models.CheckConstraint(
                condition=Q(is_filterable=True) | Q(facet_mode__isnull=True),
                name="facet_mode_requires_filterable",
            ),
            models.CheckConstraint(
                condition=Q(is_variant_dimension=False) | Q(include_in_purchase_snapshot=True),
                name="dimension_requires_purchase_snapshot",
            ),
        ]
        indexes = [
            models.Index(
                fields=["product_type", "scope", "is_active", "sort_order"],
                name="product_type_schema_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.is_variant_dimension:
            if self.scope != self.Scope.VARIANT:
                errors["scope"] = _("Variant dimensions require variant scope.")
            if self.cardinality != self.Cardinality.SINGLE:
                errors["cardinality"] = _("Variant dimensions must be single-valued.")
            if not self.is_required:
                errors["is_required"] = _("Variant dimensions must be required.")
            if self.definition_id and self.definition.data_type != AttributeDefinition.DataType.OPTION:
                errors["definition"] = _("Variant dimensions must use an option attribute.")
            if not self.include_in_purchase_snapshot:
                errors["include_in_purchase_snapshot"] = _("Variant dimensions must be included in purchase snapshots.")
        if self.cardinality == self.Cardinality.MULTIPLE and self.definition_id and self.definition.data_type != AttributeDefinition.DataType.OPTION:
            errors["cardinality"] = _("Multiple cardinality is supported only for option attributes.")
        if self.scope == self.Scope.VARIANT and self.cardinality != self.Cardinality.SINGLE:
            errors["cardinality"] = _("Variant attributes are single-valued in F1.")
        if self.facet_mode and not self.is_filterable:
            errors["facet_mode"] = _("Facet mode requires a filterable attribute.")
        if self.facet_mode == self.FacetMode.RANGE and self.definition_id and self.definition.data_type not in {
            AttributeDefinition.DataType.INTEGER,
            AttributeDefinition.DataType.DECIMAL,
        }:
            errors["facet_mode"] = _("Range facets require an integer or decimal attribute.")
        if self.facet_mode == self.FacetMode.TERMS and self.definition_id and self.definition.data_type not in {
            AttributeDefinition.DataType.OPTION,
            AttributeDefinition.DataType.BOOLEAN,
        }:
            errors["facet_mode"] = _("Terms facets require an option or boolean attribute.")
        if self.is_filterable and self.definition_id and self.definition.data_type == AttributeDefinition.DataType.TEXT:
            errors["is_filterable"] = _("Text attributes are not filterable in F1.")
        if self.is_filterable and not self.facet_mode:
            errors["facet_mode"] = _("Filterable attributes require a facet mode.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.product_type}: {self.definition}"


class ProductTypeAttributeOption(models.Model):
    binding = models.ForeignKey(
        ProductTypeAttribute,
        on_delete=models.CASCADE,
        related_name="option_restrictions",
    )
    option = models.ForeignKey(
        AttributeOption,
        on_delete=models.PROTECT,
        related_name="applicability_restrictions",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["binding", "option"],
                name="unique_binding_option_restriction",
            ),
        ]

    def clean(self):
        super().clean()
        if self.binding_id and self.option_id and self.binding.definition_id != self.option.definition_id:
            raise ValidationError({"option": _("Restricted option belongs to a different definition.")})

    def __str__(self):
        return f"{self.binding}: {self.option}"

class Product(models.Model):
    # Core
    name = models.CharField(max_length=200)
    name_fa = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(unique=True)
    sku = models.CharField(
        max_length=50,
        unique=True,
        help_text="Must match the SKU column in the pricing sheet"
    )
    product_type = models.ForeignKey(
        ProductType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
    )
    categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name="categorized_products",
    )
    primary_category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="primary_products",
    )
    materials = models.ManyToManyField(
        Material,
        blank=True,
        related_name="products",
    )
    usages = models.ManyToManyField(
        Usage,
        blank=True,
        related_name="products",
    )
    description = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)

    # Brand / catalog
    brand = models.ForeignKey(
        Brand,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
    )
    series_name = models.CharField(max_length=100, blank=True)
    series_name_fa = models.CharField(max_length=100, blank=True)
    designs = models.ManyToManyField(
        Design,
        blank=True,
        related_name="products",
    )

    # Packaging
    tiles_per_box = models.PositiveIntegerField(null=True, blank=True)
    coverage_per_box_m2 = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Square meters covered by one box"
    )

    # Merchandising
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("change_inventory", "Can change product inventory"),
            ("change_pricing", "Can change product pricing"),
            ("publish_product", "Can publish or unpublish products"),
            ("manage_storefront_media", "Can manage storefront media"),
            ("manage_catalog_schema", "Can manage catalog schema"),
            ("run_inventory_price_import", "Can run inventory and pricing imports"),
            ("apply_inventory_price_import", "Can apply inventory and pricing imports"),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def get_absolute_url(self):
        return reverse("products:product_detail", kwargs={"slug": self.slug})

    @property
    def average_rating(self):
        """Fallback for isolated products; storefront lists use annotations."""
        agg = self.reviews.filter(is_approved=True).aggregate(models.Avg("rating"))
        return round(agg["rating__avg"] or 0, 1)

    @property
    def active_variants(self):
        return self.variants.filter(is_active=True)

    @property
    def total_stock(self):
        return sum(v.stock for v in self.active_variants)

    @property
    def in_stock(self):
        return self.total_stock > 0

class ProductVariant(models.Model):
    class PriceUnit(models.TextChoices):
        UNIT = "unit", _("Per Unit")
        BOX = "box", _("Per Box")
        M2 = "m2", _("Per m²")
        TILE = "tile", _("Per Tile")
        BAG = "bag", _("Per Bag")
        BUCKET = "bucket", _("Per Bucket")
        PACK = "pack", _("Per Pack")

    @property
    def order_snapshot(self):
        """
        Immutable snapshot of this variant at purchase time.
        Used for order history.
        """

        return {
            "sku": self.sku,
            "size": self.display_size,
            "material": ", ".join(
                material.name for material in self.product.materials.all()
            ),
            "finish": self.finish.name if self.finish else "",
            "color": self.color.name if self.color else "",
            "thickness": self.display_thickness,
            "coverage": self.display_coverage,
            "tiles_per_box": self.display_tiles_per_box,
        }

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")

    # identity
    sku = models.CharField(max_length=50, unique=True)
    combination_signature = models.CharField(max_length=512, null=True, blank=True)

    # variant attributes
    color = models.ForeignKey(
        Color,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variants",
    )
    size = models.ForeignKey(
        Size,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variants",
    )
    finish = models.ForeignKey(
        Finish,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variants",
    )
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)
    thickness_mm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # packaging
    tiles_per_box = models.PositiveIntegerField(null=True, blank=True)
    coverage_per_box_m2 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    # selling unit and stock
    price_unit = models.CharField(max_length=10, choices=PriceUnit.choices, default=PriceUnit.BOX)
    stock = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Show an operational alert when active stock is at or below this value.",
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "combination_signature"],
                condition=Q(combination_signature__isnull=False),
                name="unique_product_variant_combination",
            ),
        ]

    def clean(self):
        super().clean()
        if self.combination_signature is not None:
            from .catalog_domain import validate_variant_combination_signature

            validate_variant_combination_signature(self)

    def __str__(self):
        attrs = " / ".join([str(x) for x in [self.size, self.color, self.finish] if x])
        return f"{self.product.name} - {attrs or self.sku}"

    @property
    def in_stock(self):
        return self.is_active and self.stock > 0

    @property
    def is_low_stock(self):
        return self.is_active and 0 < self.stock <= self.low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.is_active and self.stock == 0

    @property
    def display_size(self):
        if self.size:
            return self.size.name
        if self.length_mm and self.width_mm:
            return f"{self.length_mm}x{self.width_mm} mm"
        return ""

    @property
    def display_thickness(self):
        if self.thickness_mm:
            return f"{self.thickness_mm} mm"
        return ""

    @property
    def display_coverage(self):
        if self.coverage_per_box_m2:
            return f"{self.coverage_per_box_m2} m² / box"
        return ""

    @property
    def display_tiles_per_box(self):
        if self.tiles_per_box:
            return f"{self.tiles_per_box} tiles / box"
        return ""

    @property
    def specification_chips(self):
        chips = []

        if self.display_size:
            chips.append(self.display_size)

        chips.extend(material.name for material in self.product.materials.all())

        if self.finish:
            chips.append(self.finish.name)

        if self.color:
            chips.append(self.color.name)

        if self.thickness_mm:
            chips.append(f"{self.thickness_mm} mm")

        return chips


class ProductAttributeValue(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attribute_values")
    binding = models.ForeignKey(
        ProductTypeAttribute, on_delete=models.PROTECT, related_name="product_values"
    )
    option_value = models.ForeignKey(
        AttributeOption,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="product_values",
    )
    text_value = models.TextField(null=True, blank=True)
    integer_value = models.BigIntegerField(null=True, blank=True)
    decimal_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=exactly_one_typed_value_condition(),
                name="product_attribute_exactly_one_value",
            ),
            models.UniqueConstraint(
                fields=["product", "binding", "option_value"],
                condition=Q(option_value__isnull=False),
                name="unique_product_attribute_option_value",
            ),
        ]
        indexes = [
            models.Index(fields=["binding", "option_value", "product"], name="product_attr_option_idx"),
            models.Index(fields=["binding", "integer_value"], name="product_attr_integer_idx"),
            models.Index(fields=["binding", "decimal_value"], name="product_attr_decimal_idx"),
            models.Index(fields=["binding", "boolean_value"], name="product_attr_boolean_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.binding_id and self.binding.scope != ProductTypeAttribute.Scope.PRODUCT:
            errors["binding"] = _("Product values require a product-scoped binding.")
        if self.product_id and self.binding_id and self.product.product_type_id != self.binding.product_type_id:
            errors["binding"] = _("Binding does not belong to the product's type.")
        if (
            self.product_id
            and self.binding_id
            and self.binding.cardinality == ProductTypeAttribute.Cardinality.SINGLE
            and ProductAttributeValue.objects.filter(product=self.product, binding=self.binding)
            .exclude(pk=self.pk)
            .exists()
        ):
            errors["binding"] = _("This product attribute is single-valued.")
        if errors:
            raise ValidationError(errors)

        from .catalog_domain import validate_typed_value

        validate_typed_value(self)

    def __str__(self):
        return f"{self.product}: {self.binding.definition}"


class VariantAttributeValue(models.Model):
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="attribute_values"
    )
    binding = models.ForeignKey(
        ProductTypeAttribute, on_delete=models.PROTECT, related_name="variant_values"
    )
    option_value = models.ForeignKey(
        AttributeOption,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="variant_values",
    )
    text_value = models.TextField(null=True, blank=True)
    integer_value = models.BigIntegerField(null=True, blank=True)
    decimal_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=exactly_one_typed_value_condition(),
                name="variant_attribute_exactly_one_value",
            ),
            models.UniqueConstraint(
                fields=["variant", "binding"],
                name="unique_variant_attribute_value",
            ),
        ]
        indexes = [
            models.Index(fields=["binding", "option_value", "variant"], name="variant_attr_option_idx"),
            models.Index(fields=["binding", "integer_value"], name="variant_attr_integer_idx"),
            models.Index(fields=["binding", "decimal_value"], name="variant_attr_decimal_idx"),
            models.Index(fields=["binding", "boolean_value"], name="variant_attr_boolean_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.binding_id and self.binding.scope != ProductTypeAttribute.Scope.VARIANT:
            errors["binding"] = _("Variant values require a variant-scoped binding.")
        if self.variant_id and self.binding_id and self.variant.product.product_type_id != self.binding.product_type_id:
            errors["binding"] = _("Binding does not belong to the variant product's type.")
        if errors:
            raise ValidationError(errors)

        from .catalog_domain import validate_typed_value

        validate_typed_value(self)

    def __str__(self):
        return f"{self.variant}: {self.binding.definition}"


class VariantPrice(models.Model):
    """An independently managed sell price for one SKU in one currency."""

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    currency = models.CharField(max_length=3, choices=Currency.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    compare_at_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Optional list price; must be greater than the selling price."),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "currency"],
                name="unique_variant_currency_price",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="variant_price_amount_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(compare_at_amount__isnull=True)
                | Q(compare_at_amount__gt=F("amount")),
                name="variant_price_compare_at_gt_amount",
            ),
        ]
        indexes = [models.Index(fields=["currency", "variant"])]

    def clean(self):
        super().clean()
        errors = {}

        if self.amount is not None and self.amount < 0:
            errors["amount"] = _("Price amount cannot be negative.")

        if self.currency == Currency.TOMAN:
            for field_name in ("amount", "compare_at_amount"):
                value = getattr(self, field_name)
                if value is not None and value != value.to_integral_value():
                    errors[field_name] = _("Toman prices must be whole Toman amounts.")

        if (
            self.compare_at_amount is not None
            and self.amount is not None
            and self.compare_at_amount <= self.amount
        ):
            errors["compare_at_amount"] = (
                _("Compare-at price must be greater than the selling price.")
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.variant.sku} — {self.currency} {self.amount}"


class InventoryPriceImportBatch(models.Model):
    """Durable, authoritative staging record for a non-mutating import preview."""

    class SourceFormat(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "XLSX"

    class Status(models.TextChoices):
        VALIDATING = "validating", "Validating"
        VALIDATED = "validated", "Validated"
        INVALID = "invalid", "Invalid"
        APPLYING = "applying", "Applying"
        APPLIED = "applied", "Applied"
        STALE = "stale", "Stale"
        FAILED = "failed", "Failed"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_price_import_batches")
    created_at = models.DateTimeField(auto_now_add=True)
    original_filename = models.CharField(max_length=255)
    source_format = models.CharField(max_length=4, choices=SourceFormat.choices)
    source_sha256 = models.CharField(max_length=64)
    parser_version = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.VALIDATING)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    changed_rows = models.PositiveIntegerField(default=0)
    unchanged_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    warning_rows = models.PositiveIntegerField(default=0)
    stock_changes = models.PositiveIntegerField(default=0)
    threshold_changes = models.PositiveIntegerField(default=0)
    irt_changes = models.PositiveIntegerField(default=0)
    usd_changes = models.PositiveIntegerField(default=0)
    failure_summary = models.JSONField(default=dict, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="applied_inventory_price_import_batches")
    source_file = models.FileField(upload_to="products/inventory-price-imports/%Y/%m/", storage=private_inventory_import_storage, blank=True)
    source_expires_at = models.DateTimeField()

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("status", "-created_at"))]


class InventoryPriceImportRow(models.Model):
    class Status(models.TextChoices):
        CHANGED = "changed", "Changed"
        UNCHANGED = "unchanged", "Unchanged"
        ERROR = "error", "Error"
        WARNING = "warning", "Warning"

    batch = models.ForeignKey(InventoryPriceImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT, related_name="inventory_price_import_rows")
    sku_snapshot = models.CharField(max_length=50, blank=True)
    row_status = models.CharField(max_length=12, choices=Status.choices)
    normalized_patch = models.JSONField(default=dict, blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    proposed_after_snapshot = models.JSONField(default=dict, blank=True)
    changed_fields = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("row_number", "pk")
        constraints = [models.UniqueConstraint(fields=("batch", "row_number"), name="unique_inventory_price_import_batch_row")]
        indexes = [models.Index(fields=("batch", "row_status")), models.Index(fields=("variant",))]


class ProductOperationalChange(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual Dashboard edit"
        IMPORT = "import", "Inventory and pricing import"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="product_operational_changes")
    actor_username = models.CharField(max_length=150)
    actor_email = models.EmailField(blank=True)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="operational_changes")
    sku_snapshot = models.CharField(max_length=50)
    product_name_snapshot = models.CharField(max_length=255)
    source = models.CharField(max_length=12, choices=Source.choices)
    import_batch = models.ForeignKey(InventoryPriceImportBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="operational_changes")
    changed_fields = models.JSONField(default=list)
    before_snapshot = models.JSONField(default=dict)
    after_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("variant", "-created_at")), models.Index(fields=("import_batch",))]

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="products/gallery/")
    sort_order = models.PositiveIntegerField(default=0)
    alt_text = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]


class VariantImage(models.Model):
    """Additional product photography specific to a purchasable SKU."""
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="products/variants/")
    alt_text = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
    alt_text = models.CharField(max_length=200, blank=True)


class ProductView(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="views_log")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    session_key = models.CharField(max_length=40, blank=True)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["product", "viewed_at"])]


class Wishlist(models.Model):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="wishlist_items",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
    )

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="unique_user_product_wishlist",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.product}"
