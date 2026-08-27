import re

def normalize_size(text):

    if not text:
        return text

    text = text.lower()

    text = text.replace("×", "x")
    text = text.replace(" ", "")

    text = text.replace("millimeter", "mm")
    text = text.replace("millimeters", "mm")
    text = text.replace("centimeter", "cm")
    text = text.replace("centimeters", "cm")

    m = re.search(
        r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(cm|mm)?",
        text
    )

    if not m:
        return text

    a = float(m.group(1))
    b = float(m.group(2))
    unit = m.group(3)

    if unit == "mm":
        a /= 10
        b /= 10

    if a > 200:
        a /= 10

    if b > 200:
        b /= 10

    if a.is_integer():
        a = int(a)

    if b.is_integer():
        b = int(b)

    return f"{a}x{b}"


def variant_matches_size(variant, search_term):
    size = variant.size.name if variant.size else ""
    return normalize_size(size) == normalize_size(search_term)
