# F1A — Framework Catalog Architecture Contract

**Project:** TileCompany  
**Date:** 2026-08-10  
**Status:** Proposed architecture; requires approval before implementation  
**Authority:** Based on `Framework_Catalog_Audit.md` and the current repository  
**Scope:** Design only. No production model, migration, form, selector, service, template, JavaScript, test, or data change is part of F1A.

## Contract language and evidence conventions

- **Confirmed current behavior** describes repository behavior established by the audit.
- **Proposed contract** is the recommended future architecture.
- **Unresolved decision** identifies an item that needs human approval or later evidence.
- **Must** and **must not** are architectural requirements for implementation phases.
- Conceptual model and field names are precise enough to define responsibilities, but are not final Django code or migration instructions.

## 1. Architectural principles

### 1.1 Add metadata around stable commerce identities

**Confirmed current behavior:** `ProductVariant.id` is the cart identity, `ProductVariant.sku` is the purchase/price-import identity, inventory is owned by the variant, and `VariantPrice` is owned by `(variant, currency)`. Checkout re-resolves price and stock, and `OrderItem` freezes purchase facts.

**Proposed contract:**

- `Product` and `ProductVariant` remain the concrete catalog and purchasable entities.
- Product type, attribute schema, and typed values attach to those entities.
- The framework must preserve Product IDs, ProductVariant IDs, both SKU fields, stock, low-stock thresholds, `VariantPrice`, `VariantImage`, cart keys, checkout validation, orders, payments, refunds, and historical order data.
- Generic attributes describe and differentiate products; they do not become the owner of price, inventory, media, or order lines.

### 1.2 Product type is schema; category is taxonomy

- A `ProductType` defines which attributes a product family supports and which attributes differentiate its variants.
- A `Category` organizes products for navigation, merchandising, and discovery.
- A product type must not be inferred from category ancestry.
- Products of one type may appear in many category branches, and one category may contain products of several types when the store permits it.

### 1.3 Definitions are intrinsic; applicability is contextual

- `AttributeDefinition` owns the stable meaning and intrinsic value type of an attribute such as `color`, `material`, or `battery_capacity`.
- The ProductType-to-Attribute binding owns how that definition behaves for one product type: product-level or variant-level, requiredness, cardinality, ordering, filtering, search, and presentation.
- Therefore `color` is not permanently a variant attribute. It can be a Tile variant dimension and a product-level attribute for another type.

### 1.4 Typed values, not arbitrary blobs

- Searchable/filterable catalog values must be stored in typed, indexable columns.
- JSON may hold small validation or presentation metadata, but must not be the primary value store.
- Initial types are deliberately limited to `option`, `text`, `integer`, `decimal`, and `boolean`.

### 1.5 Compatibility before cleanup

- Legacy fields and legacy feature tables remain during F1.
- Backfill and compatibility adapters must prove parity before any source-of-truth transition.
- Old snapshots are immutable and continue to render through a permanent legacy reader.

### 1.6 Schema integrity and commerce safety are separate

- SKU remains the commerce identity.
- A canonical variant-combination identity prevents duplicate configurations and validates schema completeness.
- It must never replace SKU, variant ID, price, or stock.

### 1.7 Prefer retirement over deletion

- Stable codes become external and historical references.
- Referenced product types, definitions, options, and categories are retired/deactivated rather than deleted.
- Retirement stops new selection without invalidating existing catalog or order history.

### 1.8 Read contracts isolate storage

- Selectors prepare typed DTOs for storefront, dashboard, filtering, snapshots, and exports.
- Templates and JavaScript must consume those contracts rather than query or know storage fields.
- The existing selectors/services/thin-view pattern remains the organizing principle.

## 2. Proposed conceptual domain diagram

```text
Category (self hierarchy)
  parent 0..1 -> Category
  children 0..M
       ^
       | Product.categories (existing M2M, preserved)
       |
Product M ------------------------------- M Category
  | 0..1 primary_category ------------------^
  | M..1 product_type
  |
  +-- M ProductAttributeValue
  |       M..1 ProductTypeAttribute
  |       0..1 AttributeOption
  |       one typed scalar column otherwise
  |
  +-- M ProductVariant (existing, preserved)
          | sku, stock, threshold, active, sort order (preserved)
          | combination_key (additive, nullable during compatibility/draft)
          |
          +-- M VariantAttributeValue
          |       M..1 ProductTypeAttribute
          |       1 AttributeOption for F1 variant dimensions
          |
          +-- M VariantPrice (unchanged)
          +-- M VariantImage (unchanged)
          +-- M OrderItem (existing nullable live FK; unchanged)

ProductType
  +-- M ProductTypeAttribute (schema binding)
          M..1 AttributeDefinition

AttributeDefinition
  +-- M AttributeOption (definition-scoped)

OrderItem.variant_snapshot
  legacy unversioned dictionary OR versioned generic snapshot
```

The value tables reference the schema binding, not only the definition. This makes validation of product type, level, cardinality, and visibility unambiguous.

## 3. ProductType contract

### Responsibility

`ProductType` identifies a stable product family and owns its schema through applicability bindings. It does not own products' categories, prices, inventory, or order behavior.

### Conceptual fields

| Field | Contract |
|---|---|
| `code` | Globally unique, machine-safe, stable, immutable after first reference; examples `tile`, `mobile`, `laptop`. Not localized and not reused. |
| `name` / `name_fa` | Localized administrative/storefront names. Labels may change without changing identity. |
| `is_active` | Active types may be chosen for new products. Inactive types remain readable for existing products. |
| `sort_order` | Stable dashboard/presentation order. |
| `default_category` | Optional convenience FK to an active Category. It preselects/suggests category membership; it does not define schema and must not silently force permanent membership. |
| timestamps | Creation/update audit facts. |

### Product relationship

- `Product` gains an additive nullable ProductType relationship during compatibility.
- All existing TileCompany products are eventually bound to the `tile` type.
- After migration, new products must have a type before type-specific values or variants can be finalized.
- A product type is not derived from `Product.categories` or `primary_category`.

### Type changes

- A new draft product with no generic values and no variants may change type freely.
- A product with values but no variants may change type only through a controlled service that validates/migrates or removes incompatible values explicitly.
- A product with any variant, price, stock history, cart exposure, or order reference must not change type through ordinary editing.
- A genuine reclassification after variants exist is a migration operation requiring an explicit mapping, integrity report, and combination-key rebuild; it is not a dashboard field edit.

### Retirement and deletion

- A type with products cannot be hard-deleted.
- Deactivation prevents assigning it to new products while preserving existing rendering/editing according to policy.
- A type code cannot be renamed after any product is assigned.
- If a type is retired, its products remain valid; the storefront product's own `is_active` remains the publication control.

## 4. AttributeDefinition contract

### Responsibility

An `AttributeDefinition` expresses one reusable intrinsic concept and how its values are physically represented. It does not decide whether that concept is product-level or variant-level for every store/product type.

### Conceptual fields

| Field | Contract |
|---|---|
| `code` | Globally unique, stable, machine-safe, immutable after reference. Examples: `brand`, `color`, `ram`, `storage`, `screen_size`, `material`. |
| `label` / `label_fa` | Default localized labels. A future binding-level label override is not needed in F1 unless a real terminology conflict appears. |
| `data_type` | One of `option`, `text`, `integer`, `decimal`, `boolean`. Immutable once values exist. |
| `unit` | Optional stable unit code/symbol used for scalar display and validation, such as `mm`, `gb`, `mah`, or `inch`. It is metadata, not part of the stored numeric value. Option labels may already include units when the option is a commercial choice (`128 GB`). |
| `validation_rules` | Small validated metadata object appropriate to the type: numeric min/max/step, text min/max length and optional safe regex. No executable expressions or arbitrary validation language. |
| `is_active` | Active definitions may be added to schemas. Inactive definitions remain readable where already bound. |
| timestamps | Audit facts. |

### What does not belong here

The following are contextual and belong on `ProductTypeAttribute`: requiredness, cardinality, product/variant level, display order, filterability, searchability, storefront/card/spec visibility, snapshot inclusion, and facet mode.

### Initial type policy

- `option`: controlled choices; required for F1 variant dimensions.
- `text`: short or moderate plain text only. Rich text remains a product description concern.
- `integer`: discrete numeric facts such as battery capacity when integral.
- `decimal`: measured facts such as screen size or thickness.
- `boolean`: yes/no facts.
- Date, datetime, file, relation, color primitive, money, and arbitrary JSON are outside F1. Images remain Product/Variant media; money remains `VariantPrice`.

### Definition lifecycle

- `code` and `data_type` are immutable once bound or valued.
- Labels, unit presentation, validation rules, and active state may change under compatibility checks.
- Tightening validation does not retroactively invalidate published data silently; the dashboard must report existing nonconforming values before activation.

## 5. ProductType applicability/schema contract

The recommended name is `ProductTypeAttribute`. “Applicability” describes its role, but the entity is also the ordered schema binding.

### Conceptual fields

| Field | Contract |
|---|---|
| `product_type` | Required ProductType. |
| `definition` | Required AttributeDefinition. |
| `role` | `product_attribute` or `variant_dimension`. Determines owning value table and filter semantics. |
| `is_required` | Whether a publishable product or complete variant must supply a value. |
| `cardinality` | `single` or `multiple`. F1 permits `multiple` only for option-valued product attributes. Variant dimensions must be `single`. |
| `sort_order` | Order within its role for forms, specifications, selectors, and snapshots. |
| `is_filterable` | Generates a catalog facet when applicable to the current scoped result set. |
| `facet_mode` | `terms` for option/boolean; `range` for integer/decimal. Text is not filterable in F1. |
| `is_searchable` | Includes canonical/localized values in catalog search policy. |
| `show_on_storefront` | May be included in general storefront attribute bundles. |
| `show_on_product_card` | Eligible for the compact card DTO. Cards apply a strict configured limit; this flag does not guarantee unlimited rendering. |
| `show_in_specifications` | Included in the product-detail specification DTO. |
| `include_in_purchase_snapshot` | Freezes this value on purchase. Required for every variant dimension; selectively enabled for product attributes such as Tile material. |
| `is_active` | Stops new use while preserving existing values and schema history. |

### Uniqueness and validation

- Unique binding per `(product_type, definition)` in F1.
- A definition therefore has one role within one ProductType, preventing ambiguous duplicate display/filter semantics.
- The same definition may have different roles across product types.
- `variant_dimension` requires definition type `option`, cardinality `single`, snapshot inclusion true, and a deterministic sort order.
- `multiple` requires `option` in F1.
- `range` requires integer or decimal.
- `terms` requires option or boolean.
- Product-level `is_required` is validated when publishing/finalizing the product, not necessarily while saving a draft.
- Variant requiredness is validated when a variant becomes complete/purchasable.

### Schema mutation

- Cosmetic changes (labels, storefront flags, order) are allowed with cache/query impacts understood.
- Adding an optional product attribute is safe.
- Adding a required product attribute requires a backfill/default plan before enforcement.
- Adding/removing/replacing a variant dimension when the ProductType has variants is a blocked structural change in ordinary dashboard use.
- A structural dimension change requires a catalog migration operation that maps every existing variant, detects duplicate combinations, and preserves IDs/SKUs.

## 6. AttributeOption contract

### Responsibility

An option is a controlled value belonging to exactly one option-valued definition.

### Conceptual fields

| Field | Contract |
|---|---|
| `definition` | Required FK to an `option` AttributeDefinition. Options are not global across definitions. |
| `code` | Stable machine-safe identifier unique within the definition; examples `black`, `16gb`, `porcelain`. |
| `label` / `label_fa` | Frozen only when copied to purchase snapshots; live labels may otherwise change. |
| `sort_order` | Dashboard, facet, and selector ordering. |
| `is_active` | Active options may be newly assigned; retired options remain attached/readable. |
| `presentation_metadata` | Optional small validated UI-hint object, initially limited to safe keys such as swatch color or icon token. It must not contain price, stock, validation, or business rules. |
| timestamps | Audit facts. |

### Uniqueness

- Unique `(definition, code)`.
- A label need not be unique; two options can display similarly while retaining distinct stable codes if the business requires it.
- Do not use one global slug namespace. Filter URLs use definition code plus option code, for example `attr.color=black`.
- Option codes become immutable after first value reference.

### Retirement

- Retired options are hidden from new-choice widgets by default.
- They remain visible on existing products/variants and in historical displays.
- A variant using a retired option remains purchasable if otherwise active; retirement alone must not disable inventory or price.
- Dashboard editing must preserve the selected retired option and clearly mark it retired, while requiring an explicit replacement if the user changes that dimension.

## 7. ProductAttributeValue contract

### Responsibility and ownership

`ProductAttributeValue` stores a typed value for an existing Product under a `ProductTypeAttribute` whose role is `product_attribute`.

### Conceptual fields

| Field | Contract |
|---|---|
| `product` | Required existing Product FK. |
| `applicability` | Required ProductTypeAttribute FK; its type must equal `product.product_type` and role must be product attribute. |
| `option` | Populated only for option definitions. |
| `text_value` | Populated only for text definitions. |
| `integer_value` | Populated only for integer definitions. |
| `decimal_value` | Populated only for decimal definitions. |
| `boolean_value` | Populated only for boolean definitions; null means missing, not false. |
| `sort_order` | Ordering of multiple option values. Single values use zero. |
| timestamps | Useful for dashboard audit and reconciliation. |

### Invariants

- Exactly one value carrier is populated, and it must match the definition's data type.
- An option must belong to the applicability's definition.
- Scalar values are normalized before save and checked against definition validation rules.
- Single cardinality permits at most one value row per `(product, applicability)`.
- Multiple cardinality permits multiple distinct option rows, unique by `(product, applicability, option)`.
- Multiple text/numeric/boolean values are outside F1.
- Requiredness is a publish/finalization rule, allowing incomplete draft editing.
- Values cannot be attached under a binding from another ProductType.

### Current Tile mappings

- `Product.brand` → one `brand` option value.
- `Product.designs` → multiple `design` option values.
- `Product.materials` → multiple `material` option values.
- `Product.usages` → multiple `usage` option values.

## 8. VariantAttributeValue contract

### Responsibility and ownership

`VariantAttributeValue` stores one differentiating value for an existing ProductVariant under a `ProductTypeAttribute` whose role is `variant_dimension`.

### F1 shape

| Field | Contract |
|---|---|
| `variant` | Required existing ProductVariant FK. |
| `applicability` | Required binding matching `variant.product.product_type` and role `variant_dimension`. |
| `option` | Required active-or-retired AttributeOption belonging to the binding's definition. |
| timestamps | Audit/reconciliation facts. |

F1 variant dimensions are option-valued and single-cardinality. This covers Tile Color/Size/Finish, Mobile Color/RAM/Storage, Laptop Color/RAM/SSD, and Shoe Color/Size/Width without introducing free-form combination ambiguity.

### Invariants

- Unique `(variant, applicability)`; one value per dimension.
- Option must belong to the binding's definition.
- A complete variant has exactly one value for every active required variant dimension and no value for an inapplicable binding.
- Optional dimensions may be omitted, but the omission itself participates in combination canonicalization so duplicates remain detectable.
- Variant values do not store price, stock, SKU, images, packaging, or active state.

### Future scalar dimensions

Scalar variant dimensions are intentionally outside F1. If a demonstrated domain requires them, the same typed carrier strategy can be added with canonical normalization rules. It should not be enabled preemptively.

## 9. Category hierarchy and canonical-category contract

### Confirmed current behavior

Category IDs and globally unique slugs already exist; Product.categories is an optional M2M used by filtering, related products, homepage cards, dashboard management, and URLs.

### Additive Category fields

| Field | Contract |
|---|---|
| `parent` | Nullable self-FK. Root categories have no parent. Cycles are forbidden. |
| `sort_order` | Ordering among siblings, with stable name/ID tie-breakers. |
| `is_active` | Inactive category remains resolvable for management/history but is excluded from normal storefront navigation/filter choices. |
| `show_in_navigation` | Controls navigation inclusion independently of active state. |

### Hierarchy policy

- Preserve Category IDs and the existing product-category M2M table.
- Retain global slug uniqueness in F1. It matches current URLs and avoids introducing ambiguous legacy routes. Sibling-scoped duplicate slugs may be reconsidered only with a full path/canonical redirect contract.
- Depth and ancestry are derived through selector/service logic in F1; do not add a second tree library, materialized path, or nested-set columns until measured scale requires it.
- Parent assignment must reject self-parenting and descendant cycles.
- Descendant-aware catalog behavior must be explicit: browsing a parent category includes products directly assigned to that category or any active descendant.
- Direct membership remains preserved; hierarchy must not rewrite M2M rows.

### Product primary category

- Add an optional `Product.primary_category` relationship.
- When set, it must also be present in `Product.categories`.
- It provides the canonical category path for breadcrumbs, canonical URLs/SEO, and deterministic merchandising context.
- It does not limit secondary categories and does not define ProductType.
- If a primary category becomes inactive, existing links may remain resolvable, but the dashboard must require a replacement before republishing when policy demands an active canonical path.
- The ProductType default category is only a creation-time suggestion; it does not automatically become primary without an explicit product assignment.

### Deletion

- A category with children, product memberships, or primary-category references cannot be hard-deleted.
- Deactivation/retirement is the normal lifecycle action.
- Moving a category must preserve IDs/slugs and issue canonical redirects if category paths later enter URLs.

## 10. Variant combination identity

### Purpose

The combination identity proves that a Product does not have two variants with the same configured dimension values. It is schema correctness, not commerce identity.

### Canonical representation

For each variant:

1. Load all active variant-dimension bindings for the product's ProductType.
2. Sort by immutable `AttributeDefinition.code`, not display order.
3. Emit one token per binding using `definition_code=option_code`.
4. For an explicitly optional missing dimension, emit `definition_code=~` so omission is canonical.
5. Join tokens using a fixed versioned serialization rule.
6. Store a deterministic fixed-length hash as `combination_key`; keep the canonical token string available to validation/logging without requiring it as a permanent database field.

Example canonical source:

```text
color=black|finish=matte|size=60x60
```

The hash algorithm and serialization version must be fixed in a documented service. Stable codes, not database IDs or localized labels, are used.

### Constraint strategy

- Additive nullable `combination_key` on ProductVariant during compatibility.
- Conditional database uniqueness on `(product, combination_key)` where the key is non-null.
- All complete variants must have a key.
- A null key means compatibility-era unbackfilled data or an incomplete management draft; it must not be interpreted as a valid combination.
- Once migration is verified, a variant cannot become active/purchasable with a null key or incomplete required dimensions.

### Draft variants

- Draft/incomplete variants may temporarily lack values and have `combination_key = null`.
- They must be inactive and excluded from storefront/cart entry.
- SKU may be reserved during draft creation, but stock and prices remain the existing fields/tables and must not imply schema completeness.
- Finalization validates values and atomically computes the key before activation.

### Option retirement and dimension changes

- Retiring an option does not change existing keys because codes are stable.
- Renaming labels or reordering dimensions does not change keys.
- Option code changes are prohibited after use.
- Changing the ProductType's set of variant dimensions is blocked while variants exist in ordinary management.
- A controlled dimension migration must compute proposed keys for all variants, report missing mappings and collisions, and update values/keys without replacing variant rows or SKUs.

## 11. Typed value strategy

### Options considered

#### A. Separate typed columns in each value model

One Product value model and one Variant value model carry nullable typed columns, with exactly one populated according to the definition.

- Strengths: one queryable table per owner level, direct numeric/boolean indexes, simple prefetch, no JSON casting, manageable schema.
- Costs: nullable columns and explicit check/validation logic; cardinality constraints partly depend on applicability metadata.

#### B. Multiple typed value models

Separate tables for option/text/integer/decimal/boolean at each level.

- Strengths: strong physical type purity.
- Costs: many models, unions/prefetches, duplicated owner/applicability logic, more complicated dashboard and facet services. This is disproportionate to the project's initial five data types.

#### C. JSON values

One JSON payload per product/variant or per value.

- Strengths: flexible and easy to prototype.
- Costs: weaker constraints, difficult numeric indexing/ranges, database-specific query behavior, opaque migrations, and a path toward the exact pathological EAV behavior the audit warns about.

### Recommendation

Use **A: separate typed columns in one Product value model and one Variant value model**. In F1, VariantAttributeValue needs only option storage because variant dimensions are restricted to controlled options; the broader typed carrier is required on ProductAttributeValue and can later be mirrored for variants only when justified.

Validation must exist at service/model level and be backed where possible by database checks that exactly one carrier is populated. Indexes target definition/applicability plus the matching typed column. JSON is reserved for definition validation rules and safe option presentation hints, never canonical attribute values.

## 12. Brand decision

### Confirmed current behavior

Brand is a nullable Product FK used for filtering, search, product details, dashboard forms, and admin. There is no current brand-specific business behavior such as legal manufacturer identity, fulfillment rules, warranty service, brand pages, or permissions.

### Proposed decision

Brand becomes a normal configurable option-valued AttributeDefinition with code `brand`, normally bound as a single product attribute.

### Advantages

- Mobile, Laptop, Tile, and Accessory can each opt into Brand without a core schema field.
- Filtering/search/presentation use the same contracts as other attributes.
- The existing Brand rows map cleanly to definition-scoped options.
- Product types that do not need Brand do not inherit an irrelevant field.

### Trade-offs

- A dedicated model would be useful if brands later need rich pages, legal manufacturer data, logos, SEO, ownership, or cross-store governance.
- Minimal swatch/icon-style metadata is acceptable on AttributeOption, but a large brand content system should not be stuffed into option JSON.
- If rich brand behavior is later proven, a separate Brand profile may reference the `brand` option without returning Brand to the Product core schema.

No current repository evidence justifies keeping Brand as a permanent special core field. During compatibility, `Product.brand` remains and is mirrored to the generic value.

## 13. Tile physical and packaging decision

### Classification

| Current fact | Classification | Recommended long-term source of truth |
|---|---|---|
| `Size` | Variant dimension choice | Generic `size` option dimension for Tile. |
| `length_mm`, `width_mm` | Typed physical specifications that describe the chosen variant | Generic decimal/integer variant specifications are conceptually appropriate, but not F1 variant dimensions. During compatibility, preserve legacy fields. |
| `thickness_mm` | Typed physical specification | Generic decimal specification with unit `mm`; preserve legacy field during migration. |
| `tiles_per_box` | Operational packaging fact used in cart/snapshot | Keep as variant-owned operational commerce data during F1; do not make a generic attribute its authoritative source. |
| `coverage_per_box_m2` | Operational packaging/quantity conversion fact | Keep variant-owned operational commerce data during F1. It can later belong to a dedicated packaging contract, not generic descriptive EAV. |
| Product-level tiles/coverage | Defaults/catalog metadata with ambiguous authority | Preserve during compatibility; later decide whether they are defaults for variant packaging or retire them after reconciliation. |
| `PriceUnit` (`box`, `m2`, `tile`) | Operational selling-unit behavior | Keep separate from attributes and `VariantPrice`; later generalize the controlled selling/inventory unit vocabulary. |

### Why this boundary matters

- Descriptive attributes answer “what is this?”
- Variant dimensions answer “which SKU configuration is this?”
- Packaging/selling facts answer “how is this SKU counted, priced, and communicated during purchase?”

Putting tiles-per-box or selling unit into generic attribute values would make cart and inventory behavior depend on a descriptive EAV query and would weaken transactional clarity. Conversely, length/width/thickness are filterable typed specifications and do not need to remain permanent commerce-core columns once a later migration proves generic typed variant specifications.

### F1 limitation

Because F1 variant values are option dimensions only, physical scalar variant specifications should not be moved in F1B merely to claim completeness. They remain legacy/read-adapted until a focused typed variant-specification phase is approved.

## 14. Filtering and facet contract

### Facet eligibility

A binding participates when it is active, `is_filterable`, applicable to at least one ProductType in the scoped catalog, and has a supported facet mode for its data type.

### Canonical request state

- Repeated term values: `attr.<definition_code>=<option_code>`.
- Numeric lower/upper bounds: `attr.<definition_code>.min=<number>` and `.max=<number>`.
- Existing non-attribute filters remain stable: category, price, stock, sale, sort, and search.
- Legacy `brand`, `material`, `finish`, `color`, and `size` parameters remain aliases during compatibility and normalize into the generic state.

The exact punctuation of the canonical URL is a human-approval item because it becomes public API/SEO surface; the semantic contract is fixed regardless of syntax.

### Boolean semantics

- **OR within one definition:** `color=black` plus `color=white` matches either value.
- **AND across definitions:** `brand=apple` and `storage=256gb` must both match.
- **Multiple product values:** a product matches when any of its values for that definition is selected.
- **Variant-level conjunction:** all selected variant definitions, numeric variant ranges, price, sale, and stock constraints must be satisfied by the same ProductVariant. Matching different variants of one product is not sufficient.
- Product-level predicates and the same-variant predicate are ANDed.
- Category scope includes selected category descendants according to the category contract.

### Type/category awareness

- The facet service begins from a base scope: active catalog, optional ProductType(s), category subtree, search, and other non-facet constraints.
- It returns only applicable definitions and values present in that scope.
- A category containing Mobile and Laptop may expose shared and type-specific facets. Each facet result carries the set of applicable type codes or is omitted when meaningless.
- Product-type-specific catalog pages can present a clean ordered schema.
- Facet counts must follow an explicit disjunctive policy: while calculating choices within one definition, ignore that definition's current selections but retain all other constraints. This lets users broaden an OR selection and avoids zeroing sibling choices.

### Numeric ranges

- Integer/decimal values use typed columns and numeric comparison, never label parsing.
- Range DTOs return available min/max in the current scope, configured unit, and selected bounds.
- Option-valued numbers such as RAM/Storage remain term facets in F1 because commercial choices and ordering are controlled.

### Commerce filters

Price, on-sale, and in-stock remain variant commerce predicates. They are not AttributeDefinitions. The existing requirement that card pricing, target variant, discount, sorting, and pagination use the same matching variant set must be retained.

## 15. Storefront presentation contract

Storage models must not be passed to templates as the dynamic schema API. Selectors produce immutable/read-only DTO-shaped data.

### Attribute value DTO

| Property | Meaning |
|---|---|
| `definition_code` | Stable machine identity. |
| `role` | Product attribute or variant dimension. |
| `label` | Already-localized display label. |
| `data_type` | Rendering hint from the controlled type set. |
| `value_code` | Option code or null for scalar values. |
| `raw_value` | Typed scalar or canonical option code; not a localized formatted string. |
| `display_value` | Already-localized, unit-aware display string. |
| `unit` | Stable unit metadata when applicable. |
| `sort_order` | Deterministic ordering. |
| `is_retired` | Allows management/storefront policy to mark legacy choices without losing display. |

### Product presentation DTO

- Core product identity/media and commerce annotations remain explicit.
- `card_attributes`: limited ordered list based on schema flags and a configured maximum.
- `specification_attributes`: ordered product-level specifications.
- `variant_groups`: ordered selection group DTOs.
- `selected_variant`: generic variant presentation DTO.

### Variant group DTO

```text
definition_code
label
sort_order
required
options[]: {code, label, presentation_metadata, available, selected, retired}
```

### Variant presentation DTO

```text
id, sku, combination_key
attributes: {definition_code: option_code}
attribute_labels: ordered AttributeValueDTO[]
stock, is_priced, price facts, selling-unit facts, image facts
```

The browser matching algorithm operates on the generic `attributes` map and ordered groups. It must not contain `size`, `color`, or `finish` branches. Price, stock, SKU, and images remain first-class commerce/presentation fields rather than attributes.

### Localization

DTO creation resolves the active language without changing canonical codes. Templates render `label` and `display_value`; they do not inspect `name_fa` fields dynamically. Purchase snapshots separately freeze both supported language labels.

## 16. Dashboard dynamic-form contract

### Product editor

1. Select or display ProductType.
2. Render fixed core Product fields: name, slug, SKU, descriptions, categories, primary category, media, active/featured state.
3. Load ordered active product-attribute bindings for the selected type.
4. Generate widgets by data type/cardinality:
   - option/single: select;
   - option/multiple: multiselect/checkbox search;
   - text: text input;
   - integer/decimal: numeric input with unit and constraints;
   - boolean: tri-state while optional, checkbox when required with explicit false supported.
5. Validate requiredness on publish/finalize and preserve retired selected options visibly.

Changing type follows the strict ProductType change rules; a casual dropdown POST cannot discard values or variants.

### Variant editor/matrix

- Load ordered variant-dimension bindings for the ProductType.
- Render one controlled option selection per dimension.
- Continue rendering SKU, stock, threshold, active state, sort order, `VariantPrice`, `VariantImage`, and operational packaging separately.
- Compute and validate combination identity in a transaction.
- For matrix generation, propose combinations first, detect existing keys, and require explicit confirmation before creating variants. Matrix generation never synthesizes or replaces SKU silently.
- Retired options appear for existing variants but are unavailable for new combinations by default.
- Incomplete variants remain inactive drafts with null combination keys.

### Schema administration

- ProductType management owns ordered bindings.
- AttributeDefinition management owns type/unit/validation.
- AttributeOption management owns controlled choices.
- Structural changes show impact counts before save and are blocked when lifecycle rules require migration.
- The custom dashboard should be the canonical schema-management surface. Django admin may provide guarded diagnostics, but unrestricted generic schema editing in two independent UIs would undermine invariants.

## 17. Versioned order-snapshot contract

### Confirmed current behavior

Legacy snapshots are unversioned dictionaries with keys such as `sku`, `size`, `material`, `finish`, `color`, `thickness`, `coverage`, and `tiles_per_box`. `OrderItem.unit_price`, order currency, quantity, and product name are already separate immutable facts.

### Version 2 shape

```json
{
  "version": 2,
  "sku": "MOB-001-BLK-12-256",
  "product_type_code": "mobile",
  "combination_key": "<canonical-hash>",
  "attributes": [
    {
      "definition_code": "color",
      "role": "variant_dimension",
      "data_type": "option",
      "option_code": "black",
      "value": null,
      "label": "Color",
      "label_fa": "رنگ",
      "display_value": "Black",
      "display_value_fa": "مشکی",
      "unit": null
    },
    {
      "definition_code": "battery_capacity",
      "role": "product_attribute",
      "data_type": "integer",
      "option_code": null,
      "value": 5000,
      "label": "Battery",
      "label_fa": "باتری",
      "display_value": "5000 mAh",
      "display_value_fa": "۵۰۰۰ میلی‌آمپرساعت",
      "unit": "mah"
    }
  ],
  "commerce_facts": {
    "selling_unit_code": "unit",
    "selling_unit_label": "Per unit",
    "selling_unit_label_fa": "برای هر واحد",
    "packaging": []
  }
}
```

### Snapshot rules

- The root is versioned. Version is an integer with a dedicated reader per supported version.
- `sku` is always included.
- Product type code and combination key explain purchase configuration without becoming live foreign keys.
- Attributes are ordered by schema role/order at purchase time.
- Every variant dimension is included.
- Product attributes are included only when their binding has `include_in_purchase_snapshot`; Tile material is an initial example.
- Stable codes plus frozen English/Persian labels and display values are stored. Snapshot rendering does not require live definitions/options.
- Scalar `value` remains typed; option values use `option_code` and `value: null`.
- Unit is a stable code. Frozen display strings guarantee faithful presentation if unit formatting later changes.
- Operational packaging/selling facts are separated from descriptive attributes. Currency, unit price, quantity, and product name remain authoritative on Order/OrderItem and are not duplicated unnecessarily.
- Snapshot payloads are never rewritten because live labels or schema change.

### Reader contract

- Missing `version` means legacy Tile snapshot and routes to the existing-key compatibility reader.
- Version 2 routes to the generic reader and returns the same generic presentation-chip DTO used by order templates/exports.
- Unknown future versions fail safely by showing SKU and preserved product name, logging diagnostics, and never blocking access to the order itself.
- Live localized values may enhance current-product links, but never override frozen purchase facts.

## 18. Compatibility and migration strategy

No legacy field or table is removed in F1.

### Stage 1 — foundation, legacy authoritative

- Add ProductType, definitions, options, bindings, generic values, category hierarchy metadata, primary category, and nullable combination key.
- Existing application reads and writes remain unchanged.
- Create the `tile` schema configuration and deterministic mappings from legacy model IDs to new option IDs.

### Stage 2 — idempotent backfill and reconciliation

- Bind existing products to Tile.
- Backfill Brand/Design/Material/Usage product values and Color/Size/Finish variant values.
- Compute proposed combination keys without changing IDs/SKUs.
- Report missing options, cardinality violations, mismatched Size/raw dimensions, duplicate combinations, product/category inconsistencies, and snapshot coverage.
- Backfill must be rerunnable and must not overwrite a conflicting generic value silently.

### Stage 3 — dual write through compatibility services

- Existing legacy fields remain authoritative for Tile while approved write services mirror changes into generic values in the same transaction.
- Generic schema dashboard writes for Tile mirror back into legacy fields where a lossless mapping exists.
- Writes that bypass compatibility services (unrestricted admin, scripts, direct ORM) must be restricted or followed by reconciliation; signals are not the preferred primary invariant mechanism because M2M ordering and transaction intent are clearer in services.
- Price, stock, images, cart, checkout, and order lifecycle are not dual-written because they remain on existing entities.

### Stage 4 — generic read adapters with per-record verification

- Storefront/dashboard selectors consume generic DTO contracts.
- During transition, adapters may synthesize missing Tile values from legacy fields only for unverified records.
- A record is considered verified only after parity checks succeed; partial generic data must not be silently mixed with legacy data definition by definition.
- Legacy URL parameters normalize to generic filters.

### Stage 5 — generic authoritative, legacy mirror

- After parity, generic values become the catalog schema source for reads/writes.
- Legacy Tile fields remain compatibility mirrors through F1 and are checked continuously.
- New ProductTypes use only generic values; they never receive fake legacy rows/fields.

### Outside F1

Stopping legacy writes or removing legacy models/columns is a later, separately approved phase after production telemetry, old-cart safety, open-order inventory restoration, analytics/export parity, and permanent legacy snapshot support are proven.

## 19. Indexing and performance strategy

### Required identity/schema indexes

- Unique ProductType `code`.
- Unique AttributeDefinition `code`.
- Unique `(AttributeOption.definition, AttributeOption.code)`.
- Unique `(ProductTypeAttribute.product_type, definition)`.
- Ordered lookup index `(product_type, role, is_active, sort_order)`.
- Category index `(parent, is_active, show_in_navigation, sort_order)`; existing slug uniqueness retained.
- Product indexes for `product_type` and `primary_category` as normal FK indexes.

### Product value indexes

- Owner lookup `(product, applicability)`.
- Facet option lookup `(applicability, option, product)`.
- Typed range indexes `(applicability, integer_value)` and `(applicability, decimal_value)`.
- Boolean facet index `(applicability, boolean_value)` if real selectivity/query evidence justifies it.
- Search does not initially require indexing arbitrary text attribute substrings beyond database-supported normal indexes; measure before adding projections.

### Variant value and combination indexes

- Unique `(variant, applicability)`.
- Facet lookup `(applicability, option, variant)`.
- Supporting lookup `(variant, option)` only if query plans require it.
- Conditional unique `(product, combination_key)` on ProductVariant for non-null keys.
- Existing ProductVariant product/active/order fields and VariantPrice indexes remain.

### Query strategy

- Filter one definition at a time using `EXISTS`/subqueries against indexed value rows, avoiding one giant multiplicative join.
- For variant facets, produce a matching variant-ID subquery that applies all selected variant definitions to the same variant, then use that identical set for product existence, scoped price, stock, sale, target variant, count, and sort.
- Prefetch schema bindings/options once per ProductType and values in batches into DTO builders.
- Never fetch one definition/option per rendered chip.
- Paginate products before loading detail-only attribute bundles where semantics allow.
- Do not introduce denormalized search/facet tables in F1B. Add them only after query-count and query-plan evidence shows typed value indexes are insufficient.

## 20. Lifecycle and deletion rules

| Entity/change | Required behavior |
|---|---|
| ProductType with products | Cannot delete; deactivate. Code immutable. |
| ProductType type change on populated product | Block ordinary edit; require controlled migration. |
| AttributeDefinition with bindings or values | Cannot delete; deactivate/retire. Code and type immutable. |
| AttributeDefinition validation tightened | Impact report and explicit remediation; no silent invalidation. |
| AttributeOption used by product | Cannot delete; retire. Existing value remains visible. |
| AttributeOption used by variant | Cannot delete; retire. Existing variant/key remains valid and may remain purchasable. |
| Option label/order changed | Allowed; combination identity unchanged; new snapshots use new labels, old snapshots stay frozen. |
| ProductTypeAttribute with values | Cannot delete in ordinary UI; deactivate after impact review. Existing values remain readable. |
| Required product binding added | Requires backfill/default plan before publication enforcement. |
| Variant dimension added/removed/replaced with variants present | Block; controlled mapping/collision migration required. |
| Variant dimension reordered | Allowed for presentation only; canonical key sorts by definition code and does not change. |
| Category with children/products/primary references | Cannot delete; deactivate or move references explicitly. |
| Category moved | Preserve ID/slug; validate no cycle; maintain redirects if path URLs exist. |
| Product primary category removed from M2M | Reject or atomically clear/reassign primary category by explicit service policy. |

Retirement must be visible in dashboard diagnostics. It must not cascade into product deactivation, variant deactivation, price deletion, stock change, cart removal, or order mutation.

## 21. Worked Tile example

### Product type and schema

```text
ProductType: tile — Tile / کاشی

Product attributes:
1. brand       option, single, optional, filter/search/card/spec
2. material    option, multiple, optional, filter/search/card/spec/snapshot
3. design      option, multiple, optional, filter/search/spec
4. usage       option, multiple, optional, filter/search/spec

Variant dimensions:
1. color       option, single, required, filter/search/spec/snapshot
2. size        option, single, required, filter/search/spec/snapshot
3. finish      option, single, required, filter/search/spec/snapshot
```

### Example product

```text
Product ID: preserved existing ID
ProductType: tile
Categories: Floor Tiles, Collections > Marble Look
Primary category: Floor Tiles
Brand: Ariana
Materials: Porcelain
Designs: Marble-look
Usages: Floor, Indoor
```

### Example variant

```text
ProductVariant ID/SKU/stock/prices/images: preserved
color=beige
size=60x60
finish=polished
combination source: color=beige|finish=polished|size=60x60

Operational facts remain on the variant during F1:
tiles_per_box=4
coverage_per_box_m2=1.44
price_unit=box
```

The category “Floor Tiles” does not create the Tile schema. A Tile product could also appear under Outdoor or a seasonal Collection without changing its ProductType.

## 22. Worked Mobile example

### Product type and schema

```text
ProductType: mobile — Mobile / موبایل

Product attributes:
brand              option, single, required, filter/search/card/spec/snapshot
screen_size        decimal, single, optional, range-filter/search/spec, unit inch
chipset            text or option, single, optional, search/spec
camera             text, single, optional, search/spec
battery_capacity   integer, single, optional, range-filter/search/spec, unit mAh

Variant dimensions:
color              option, single, required, filter/spec/snapshot
ram                option, single, required, filter/spec/snapshot
storage            option, single, required, filter/spec/snapshot
```

### Categories and product

```text
Electronics
└── Mobile
    ├── Flagship
    └── Budget

ProductType: mobile
Categories: Electronics > Mobile > Flagship; Promotions > Summer
Primary category: Flagship
```

The Mobile schema comes from ProductType `mobile`, not from membership in Mobile or Flagship. A Mobile product moved from Flagship to Budget keeps exactly the same attribute schema and variants.

### Variant identity

```text
color=black|ram=12gb|storage=256gb
```

The variant retains its normal SKU, stock, independent USD/IRT VariantPrice rows, images, and cart/order behavior.

## 23. Worked Laptop example

### Product type and schema

```text
ProductType: laptop — Laptop / لپ‌تاپ

Product attributes:
brand       option, single, required
cpu         option or text, single, required
gpu         option or text, single, optional
screen_size decimal, single, optional, unit inch
os          option, single, optional

Variant dimensions:
color       option, single, required
ram         option, single, required
ssd         option, single, required
```

### Categories

```text
Electronics
└── Laptop
    ├── Gaming
    ├── Business
    └── Ultrabook
```

A Gaming Laptop and Business Laptop share ProductType `laptop` but have different category memberships. Conversely, a broad “Business” category could contain laptops and accessories while each retains its own type schema.

### Reused definitions

The same global `brand`, `color`, and `ram` definitions can be bound to Mobile and Laptop with different requiredness, order, and presentation flags. Options still belong to their one definition, so `ram:16gb` is reused consistently while `storage:256gb` and `ssd:256gb` remain separate concepts if the business wants distinct facets.

## 24. Unresolved architectural decisions

These items do not block the conceptual architecture, but must be approved or deliberately deferred before their implementation surface is finalized.

1. **Canonical filter URL syntax:** approve `attr.<code>`/`.min`/`.max` or choose an equivalent stable encoding. Semantics are fixed; syntax becomes public URL API.
2. **Type assignment during backfill:** confirm every existing Product is Tile, including the one current product without categories.
3. **Required Tile dimensions:** current schema permits nulls but current data has Color/Size/Finish for all variants. Approve making all three required for complete future Tile variants.
4. **Finish as a dimension:** approve retaining Finish as a Tile variant dimension rather than migrating it to product-level metadata.
5. **Material snapshot semantics:** approve snapshotting all product materials for every purchased Tile variant, matching current behavior, or define a later variant-specific material rule.
6. **Physical scalar timing:** approve keeping length/width/thickness in legacy fields through F1B and postponing generic typed variant specifications.
7. **Operational unit direction:** approve retaining current `PriceUnit` in F1B and scheduling a separate selling/inventory-unit contract rather than expanding it inside generic attributes.
8. **ProductType default category:** approve it as a non-binding creation-time suggestion, not automatic schema/category coupling.
9. **Global category slugs:** approve preserving global uniqueness for compatibility instead of sibling-scoped slugs/path URLs in F1.
10. **Custom dashboard as canonical schema editor:** decide whether Django admin is read-mostly/guarded for new schema entities or must receive full invariant-aware editing later.
11. **Product card attribute limit:** choose a small store/theme-level maximum; schema flags identify eligible values but should not produce unbounded cards.
12. **Inactive ProductType storefront policy:** confirm that type retirement prevents new assignments but does not automatically hide existing active products.
13. **Optional variant dimensions:** F1 supports them conceptually with a canonical missing marker, but approve whether F1B should prohibit them initially for simpler completeness rules.
14. **CPU/GPU/chipset modeling:** use option when controlled faceting matters and text when it does not. This should be configured per actual store rather than decided universally now.

## 25. Recommended F1B implementation scope

F1B should be a narrow additive foundation, not an end-to-end storefront conversion.

### In scope

- Add conceptual foundation entities: ProductType, AttributeDefinition, ProductTypeAttribute, AttributeOption, ProductAttributeValue, VariantAttributeValue.
- Add nullable Product-to-ProductType and Product primary-category relationships.
- Add Category hierarchy/status/order/navigation fields while preserving IDs, slugs, and existing M2M membership.
- Add nullable ProductVariant combination key and its non-null uniqueness rule.
- Implement domain validation/services for typed values, applicability, option ownership, category cycles/primary membership, combination canonicalization, and lifecycle protection.
- Create the Tile ProductType/schema and map legacy feature rows to definition-scoped options through deterministic, idempotent data work.
- Backfill generic values and proposed combination keys with reconciliation reports.
- Add focused model/service/migration tests for identities, constraints, typed values, category hierarchy, backfill parity, and duplicate combinations.
- Define snapshot DTO/writer/reader interfaces and test the legacy reader plus version-2 serialization at the domain layer, without switching checkout/order templates yet.

### Explicitly out of scope for F1B

- Removing or renaming legacy fields/models.
- Switching production storefront filters, product detail, cart, checkout, orders, analytics, or exports to generic reads.
- Dynamic dashboard product/variant forms or a variant matrix.
- Replacing the current JavaScript variant selector.
- Generalizing `PriceUnit`, packaging, VariantPrice, inventory, payment, or order lifecycle.
- Denormalized search/facet projections.
- Sibling-scoped category slugs or hierarchical public URL changes.
- Scalar/free-form variant dimensions.

### F1B completion gate

F1B is complete only when the additive schema can represent all current Tile product/variant values, every existing Product and ProductVariant identity is preserved, duplicate/missing combination reports are clean or explicitly waived, and no existing commerce workflow has changed behavior.

## Architectural decisions requiring human approval before F1B

1. Approve ProductType as a separate schema identity, never inferred from Category.
2. Approve one global stable AttributeDefinition code with contextual behavior on `ProductTypeAttribute`.
3. Approve definition-scoped AttributeOptions and `(definition, code)` uniqueness.
4. Approve typed-column ProductAttributeValue storage and option-only F1 VariantAttributeValue dimensions.
5. Approve Brand as a normal `brand` attribute while retaining `Product.brand` during compatibility.
6. Approve Color, Size, and Finish as required Tile variant dimensions for complete new variants.
7. Approve preserving packaging/selling-unit facts outside generic descriptive attributes.
8. Approve additive Category parent/order/status/navigation fields, global slug uniqueness, and optional Product primary category.
9. Approve a nullable compatibility/draft `combination_key` with non-null uniqueness per Product, derived from stable codes.
10. Approve the version-2 snapshot structure and permanent legacy unversioned reader.
11. Approve the staged authority model: legacy authoritative → reconciled dual write → generic authoritative with legacy mirror, with no F1 removals.
12. Approve the narrow F1B scope and explicit exclusion of storefront/dashboard conversion.

