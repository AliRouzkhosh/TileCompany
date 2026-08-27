"""Project-owned CKEditor normalization and nh3 Article HTML boundary."""
from html import escape
from html.parser import HTMLParser

import nh3

ALLOWED_TAGS = {"p", "h2", "h3", "strong", "em", "u", "s", "ul", "ol", "li", "blockquote", "a", "figure", "figcaption", "img", "table", "thead", "tbody", "tr", "th", "td", "br"}
ALLOWED_CLASSES = {
    "article-lead", "article-small", "article-callout",
    "article-align-start", "article-align-center", "article-align-end", "article-align-justify",
    "article-image-align-start", "article-image-align-center", "article-image-align-end",
    "article-image-small", "article-image-medium", "article-image-content", "article-image-wide",
    # Finite CKEditor/legacy structural classes retained for safe re-editing.
    "image", "table", "image-style-align-left", "image-style-align-center",
    "image-style-align-right", "image-style-side", "image-style-block",
    "text-left", "text-center", "text-right", "text-justify",
}
ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}
CLASS_TAGS = {"p", "h2", "h3", "blockquote", "figure", "img", "table"}

ALIGNMENT_CLASSES = {
    "center": "article-align-center",
    "justify": "article-align-justify",
}
IMAGE_SIZE_CLASSES = {
    "32%": "article-image-small",
    "50%": "article-image-medium",
    "75%": "article-image-content",
    "100%": "article-image-wide",
}
ALL_IMAGE_SIZE_CLASSES = frozenset(IMAGE_SIZE_CLASSES.values())
CKEDITOR_IMAGE_ALIGNMENT_CLASSES = {
    "image-style-align-left": "left",
    "image-style-block-align-left": "left",
    "image-style-align-center": "center",
    "image-style-align-right": "right",
    "image-style-block-align-right": "right",
}
VOID_TAGS = {"br", "img"}


def _style_declarations(value):
    """Parse only enough CSS syntax to recognize exact CKEditor declarations."""
    declarations = {}
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, raw_value = declaration.split(":", 1)
        declarations[name.strip().lower()] = raw_value.strip().lower()
    return declarations


class _ArticleEditorHtmlNormalizer(HTMLParser):
    """Event-parser canonicalizer for the finite CKEditor semantics we own."""

    def __init__(self, direction):
        super().__init__(convert_charrefs=False)
        self.direction = direction
        self.parts = []

    def _attributes(self, tag, attrs):
        class_tokens = []
        retained = []
        styles = {}
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name == "class":
                class_tokens.extend(value.split())
            elif name == "style":
                styles = _style_declarations(value)
            else:
                retained.append((name, value))

        alignment = styles.get("text-align")
        if tag in {"p", "h2", "h3", "blockquote"} and alignment:
            if alignment in ALIGNMENT_CLASSES:
                class_tokens.append(ALIGNMENT_CLASSES[alignment])
            elif alignment == "left":
                class_tokens.append("article-align-start" if self.direction == "ltr" else "article-align-end")
            elif alignment == "right":
                class_tokens.append("article-align-end" if self.direction == "ltr" else "article-align-start")

        if tag in {"figure", "img"}:
            image_size = IMAGE_SIZE_CLASSES.get(styles.get("width"))
            if image_size:
                class_tokens = [token for token in class_tokens if token not in ALL_IMAGE_SIZE_CLASSES]
                class_tokens.append(image_size)
            class_tokens = [token for token in class_tokens if token != "image_resized"]
            physical_alignment = next(
                (CKEDITOR_IMAGE_ALIGNMENT_CLASSES[token] for token in class_tokens if token in CKEDITOR_IMAGE_ALIGNMENT_CLASSES),
                None,
            )
            class_tokens = [token for token in class_tokens if token not in CKEDITOR_IMAGE_ALIGNMENT_CLASSES]
            if physical_alignment == "center":
                class_tokens.append("article-image-align-center")
            elif physical_alignment == "left":
                class_tokens.append("article-image-align-start" if self.direction == "ltr" else "article-image-align-end")
            elif physical_alignment == "right":
                class_tokens.append("article-image-align-end" if self.direction == "ltr" else "article-image-align-start")

        if class_tokens:
            retained.append(("class", " ".join(dict.fromkeys(class_tokens))))
        return retained

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        rendered = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in self._attributes(tag, attrs))
        self.parts.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


def normalize_article_editor_html(html: str, *, direction="ltr") -> str:
    """Convert only recognized CKEditor inline semantics to project classes."""
    html = html or ""
    if (
        "style=" not in html.lower()
        and "image_resized" not in html
        and not any(class_name in html for class_name in CKEDITOR_IMAGE_ALIGNMENT_CLASSES)
    ):
        return html
    normalizer = _ArticleEditorHtmlNormalizer("rtl" if direction == "rtl" else "ltr")
    normalizer.feed(html)
    normalizer.close()
    return "".join(normalizer.parts)

def _filter(tag, attribute, value):
    if attribute == "class" and tag in CLASS_TAGS:
        return " ".join(part for part in value.split() if part in ALLOWED_CLASSES) or None
    return value

def sanitize_article_html(html: str, *, direction="ltr") -> str:
    """Preserve approved CKEditor semantics; strip executable/unknown markup.

    nh3/Ammonia performs parser-based malformed-HTML normalization, protocol
    filtering, and adds noopener noreferrer to retained links.
    """
    normalized = normalize_article_editor_html(html, direction=direction)
    return nh3.clean(normalized, tags=ALLOWED_TAGS, attributes=ATTRIBUTES,
                     allowed_classes={tag: ALLOWED_CLASSES for tag in CLASS_TAGS},
                     attribute_filter=_filter, url_schemes={"http", "https"},
                     url_relative="pass_through", link_rel="noopener noreferrer",
                     clean_content_tags={"script", "style", "iframe", "object", "embed", "svg"})
