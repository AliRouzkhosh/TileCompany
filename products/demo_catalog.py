"""Deterministic, generic-only data used by ``seed_demo_catalog``.

This module deliberately contains no I/O.  It is both the seed manifest and a
small, inspectable statement of the demo catalogue's provenance boundaries.

``SOURCE_DERIVED`` facts are supplied by the checkpoint manifest/research.
``CONTROLLED_DEMO`` is limited to non-technical merchandising classifications.
``UNKNOWN`` is an internal required-dimension fallback; it is never a product
claim and the later facet-eligibility UX must suppress it for customers.
"""

SOURCE_DERIVED = "source_derived"
CONTROLLED_DEMO = "controlled_demo"
UNKNOWN = "unknown"


BRANDS = (
    ("marjan-tile", "Marjan Tile", "کاشی مرجان"),
    ("sina-tile", "Sina Tile", "سینا کاشی"),
    ("alvand-tile", "Alvand Tile", "کاشی الوند"),
    ("pars-tile", "Pars Tile", "کاشی پارس"),
    ("eefa-ceram", "EEFA Ceram", "ایفاسرام"),
    ("tabriz-tile-keraben-kergres", "Tabriz Tile / Keraben-Kergres", "کاشی تبریز / کرابن-کرگرس"),
    ("shimi-sakhteman", "Shimi Sakhteman", "شیمی ساختمان"),
    ("zhikava", "Zhikava", "ژیکاوا"),
    ("katic-sarooj-shimi-parseh", "Katic / Sarooj Shimi Parseh", "کاتیک / ساروج شیمی پارسه"),
    ("proofab", "Proofab", "پروفاب"),
)

CATEGORIES = (
    ("tiles-ceramics", "Tiles & Ceramics", "کاشی و سرامیک", None),
    ("porcelain-tiles", "Porcelain Tiles", "پرسلان", "tiles-ceramics"),
    ("wall-floor-tiles", "Wall & Floor Tiles", "کاشی و سرامیک کف و دیوار", "tiles-ceramics"),
    ("tile-tools", "Tile Tools", "ابزار آلات کاشی", None),
    ("tile-adhesives", "Tile Adhesives", "چسب کاشی و سرامیک", "tile-tools"),
    ("grout-joint-fillers", "Grout & Joint Fillers", "مواد و پودر بندکشی", "tile-tools"),
    ("installation-additives", "Installation Additives", "مواد افزودنی نصب", "tile-tools"),
)

PRODUCT_TYPES = (
    ("ceramic-tile", "Tile & Ceramic", "کاشی و سرامیک"),
    ("tile-adhesive", "Tile Adhesive", "چسب کاشی و سرامیک"),
    ("grout", "Grout & Joint Filler", "مواد بندکشی"),
    ("installation-additive", "Installation Additive", "مواد افزودنی نصب"),
)

# ``kind`` is intentionally explicit: attributes remain generic typed schema,
# while the code namespace makes this command's owned definitions unambiguous.
SCHEMA = {
    "ceramic-tile": (("demo-material-body", "Material / Body", "جنس / بدنه", "product", False, True, ("porcelain", "ceramic")),
                      ("demo-usage", "Installation Surface", "سطح نصب", "product", False, True, ("floor", "wall", "both")),
                      ("demo-application-area", "Application Area", "فضای کاربرد", "product", False, True, ("bathroom", "kitchen", "commercial", "residential")),
                      ("demo-size", "Size", "ابعاد", "variant", True, True, ("20x20", "30x90", "60x60", "60x120", "80x160", "20x120")),
                      ("demo-color", "Color", "رنگ", "variant", True, True, ("white", "gold", "gray", "light-gray", "dark-gray", "black", "cream", "light-cream", "dark-cream", "brown", "green", "blue", "beige", "light", "dark", "gris", "grafito", "negro", "light-beige", "dark-beige", "roble", "olive", "grey")),
                      ("demo-finish", "Finish", "پرداخت", "variant", True, True, ("matte", "polished", "nano-polished"))),
    "tile-adhesive": (("demo-adhesive-form", "Adhesive Form", "نوع چسب", "product", False, True, ("powder", "paste")),
                      ("demo-standard-class", "Standard Class", "رده استاندارد", "product", False, True, ("c1e", "c2e")),
                      ("demo-application", "Application", "کاربرد", "product", False, True, ("indoor", "outdoor", "both")),
                      ("demo-package-weight", "Package Weight", "وزن بسته", "variant", True, True, ("1kg", "5kg", "12kg", "12-5kg", "20kg", "25kg")),
                      ("demo-adhesive-color", "Color", "رنگ", "variant", True, True, ("white", "gray"))),
    "grout": (("demo-grout-base", "Grout Form / Base", "نوع / پایه بندکشی", "product", False, True, ("cement-based", "paste", "two-part")),
              ("demo-water-resistance", "Water Resistance", "مقاومت در برابر آب", "product", False, True, ("water-resistant",)),
              ("demo-package-weight", "Package Weight", "وزن بسته", "variant", True, True, ("500g", "1-5kg")),
              ("demo-grout-color", "Color", "رنگ", "variant", True, True, ("white", "colored"))),
    "installation-additive": (("demo-installation-form", "Installation Form", "نوع ماده نصب", "product", False, True, ("additive", "adhesive")),
                              ("demo-package-weight", "Package Weight", "وزن بسته", "variant", True, True, ("3kg", "8kg"))),
}

# (brand, type, category, name, fa, variants).  A variant is
# (suffix, unit, package/color/size/finish values, source-supported product attrs).
TILES = (
    ("marjan-tile", "Ocean", "اوشن", [("1161", "20x20", "gray", "matte"), ("1162", "20x20", "white", "matte"), ("1163", "20x20", "cream", "matte"), ("1164", "20x20", "blue", "matte")]),
    *[("marjan-tile", n, fa, [("STD", None, None, None)]) for n, fa in (("Vision","ویژن"),("Crystal","کریستال"),("Basalt","بازالت"),("Granada","گرانادا"),("Portland","پورتلند"))],
    ("sina-tile","Bismark","بیس مارک",[("GRAY","80x160","gray",None)]),
    ("sina-tile","Lamari","لاماری",[(x,"80x160",x.lower().replace(" ","-"),None) for x in ("Cream","Dark Gray","Light Gray")]),
    ("sina-tile","Galexy","گالکسی",[(x,"60x120",x.lower(),None) for x in ("Brown","Green","Gray","Blue")]),
    ("sina-tile","Jardin","ژاردین",[(x,"60x120",x.lower().replace(" ","-"),None) for x in ("Dark Gray","Dark Cream","Light Cream","Light Gray")]),
    ("sina-tile","Nismo","نیسمو",[(x,"60x120",x.lower().replace(" ","-"),None) for x in ("Dark Gray","Light Gray","Light Cream")]),
    ("sina-tile","Varna","وارنا",[(x,"60x120",x.lower().replace(" ","-"),None) for x in ("Light Gray","Dark Gray")]),
    ("alvand-tile","Kabon","کابون",[("WHITE-GOLD","60x120","white","polished"),("GOLD","60x120","gold","polished")]),
    ("alvand-tile","Canyon","کانیون",[(x,"60x120",x.lower().replace(" ","-"),None) for x in ("Light Gray","Dark Gray","Black")]),
    ("alvand-tile","Bardeo","باردئو",[("WHITE","60x120","white","nano-polished")]),
    ("alvand-tile","Smoky","اسموکی",[(x,"60x120",x.lower().replace(" ","-"),"nano-polished") for x in ("Light Gray","Dark Gray")]),
    ("alvand-tile","Real Stone","رئال استون",[("STD",None,None,None)]),
    ("pars-tile","Agat","آگات",[("MATTE","60x120",None,"matte")]),
    ("pars-tile","Rivoli","ریولی",[(x,"60x120",x.lower(),"matte") for x in ("Cream","Beige","Gray")]),
    ("pars-tile","Dallaho","دالاهو",[("MATTE","60x120",None,"matte")]),
    ("pars-tile","Antimo","آنتیمو",[("BLACK","60x120","black","matte")]),
    ("pars-tile","Levina","لوینا",[(x,"30x90",x,"matte") for x in ("white","gray")]),
    ("eefa-ceram","Pietra A1","پیترا A1",[("MATTE","60x60",None,"matte")]),
    ("eefa-ceram","Pietra A2","پیترا A2",[("MATTE","60x60",None,"matte")]),
    ("eefa-ceram","MT 120","ام‌تی ۱۲۰",[("LIGHT","60x120","light","polished")]),
    ("eefa-ceram","Pablo","پابلو",[(x,"60x120",x.lower(),None) for x in ("Light","Dark")]),
    ("tabriz-tile-keraben-kergres","Sara","سارا",[(x,"60x120",x,"matte") for x in ("gris","grafito","negro")]),
    ("tabriz-tile-keraben-kergres","Soul","سول",[(x,"60x60",x,"matte") for x in ("light-beige","dark-beige","light-gray","dark-gray")]),
    ("tabriz-tile-keraben-kergres","Plus Wood","پلاس وود",[(x,"20x120",x,"matte") for x in ("roble","olive","grey")]),
    ("tabriz-tile-keraben-kergres","Mars","مارس",[("STD",None,None,None)]),
)

INSTALLATION = (
    ("shimi-sakhteman","tile-adhesive","AK5 Paste Tile Adhesive","چسب کاشی خمیری AK5", "bucket", "paste", [("12KG","12kg",None)]),
    ("shimi-sakhteman","tile-adhesive","AK2 Paste Tile Adhesive","چسب کاشی خمیری AK2", "bucket", "paste", [("STD",None,None)]),
    ("shimi-sakhteman","tile-adhesive","BK1 Powder Tile Adhesive","چسب کاشی پودری BK1", "bag", "powder", [("GRAY-20KG","20kg","gray")]),
    ("shimi-sakhteman","tile-adhesive","BK2 Powder Tile Adhesive","چسب کاشی پودری BK2", "bag", "powder", [("WHITE-20KG","20kg","white"),("GRAY-20KG","20kg","gray")]),
    ("shimi-sakhteman","tile-adhesive","BK2F Porcelain Adhesive","چسب پرسلان BK2F", "bag", "powder", [("20KG","20kg",None)]),
    ("shimi-sakhteman","installation-additive","SA12 Installation Additive","افزودنی نصب SA12", "unit", "additive", [("STD",None,None)]),
    ("zhikava","tile-adhesive","Zhikafix A-4000","ژیکافیکس A-4000", "bag", "powder", [("STD",None,None)]),
    ("zhikava","tile-adhesive","Zhikafix A-6000","ژیکافیکس A-6000", "bag", "powder", [("WHITE-20KG","20kg","white"),("GRAY-20KG","20kg","gray")]),
    ("zhikava","tile-adhesive","Zhikafix A-8000","ژیکافیکس A-8000", "bag", "powder", [("STD",None,None)]),
    ("zhikava","tile-adhesive","Ceramfix C1E","سرام‌فیکس C1E", "bag", "powder", [("STD",None,None)]),
    ("zhikava","tile-adhesive","Ceramfix C2E","سرام‌فیکس C2E", "bag", "powder", [("STD",None,None)]),
    ("zhikava","grout","Nano Zhikapond","نانو ژیکاپوند", "pack", "cement-based", [("WHITE-500G","500g","white"),("COLORED-500G","500g","colored")]),
    ("katic-sarooj-shimi-parseh","tile-adhesive","Katic Paste Tile Adhesive","چسب کاشی خمیری کاتیک", "bucket", "paste", [("12KG","12kg",None),("25KG","25kg",None)]),
    ("katic-sarooj-shimi-parseh","tile-adhesive","Katic Powder Ceramic Adhesive","چسب سرامیک پودری کاتیک", "bag", "powder", [("20KG","20kg",None)]),
    ("katic-sarooj-shimi-parseh","tile-adhesive","Katic BK2F Porcelain Adhesive","چسب پرسلان BK2F کاتیک", "bag", "powder", [("20KG","20kg",None)]),
    ("katic-sarooj-shimi-parseh","installation-additive","Katic Concrete / Installation Adhesive","چسب نصب / بتن کاتیک", "bucket", "adhesive", [("3KG","3kg",None),("8KG","8kg",None)]),
    ("proofab","tile-adhesive","TB21 Paste Tile Adhesive","چسب کاشی خمیری TB21", "bucket", "paste", [("1KG","1kg",None),("5KG","5kg",None),("12-5KG","12-5kg",None)]),
    ("proofab","tile-adhesive","TBP22-G Powder Tile Adhesive","چسب کاشی پودری TBP22-G", "bag", "powder", [("20KG","20kg",None)]),
    ("proofab","grout","RE-NGROUT Nano Two-Part Grout","دوغاب نانو دو جزئی RE-NGROUT", "pack", "two-part", [("1-5KG","1-5kg",None)]),
    ("proofab","grout","Proofab Nano Grout Paste","دوغاب نانو خمیری پروفاب", "pack", "paste", [("STD",None,None)]),
)

assert len(TILES) == 30 and len(INSTALLATION) == 20

# Body facts are SOURCE_DERIVED. Do not infer porcelain from a size alone.
CERAMIC_PORCELAIN_SOURCE_DERIVED = frozenset({
    "Pietra A1", "Pietra A2", "MT 120", "Sara", "Soul", "Plus Wood",
})

# The supplied manifest does not establish full manufacturer usage for the
# collections below. These are deliberate CONTROLLED_DEMO storefront groupings,
# based on format/collection context—not certification or technical claims.
CERAMIC_USAGE_CONTROLLED_DEMO = {
    "wall": frozenset({"Ocean", "Vision", "Crystal", "Granada", "Levina", "Bismark", "Lamari", "Jardin", "Nismo"}),
    "floor": frozenset({"Basalt", "Portland", "Canyon", "Bardeo", "Smoky", "Real Stone", "Agat", "Rivoli", "Dallaho"}),
    "both": frozenset({"Galexy", "Varna", "Kabon", "Antimo", "Pietra A1", "Pietra A2", "MT 120", "Pablo", "Sara", "Soul", "Plus Wood", "Mars"}),
}
CERAMIC_APPLICATION_AREA_CONTROLLED_DEMO = {
    "bathroom": frozenset({"Ocean", "Vision", "Crystal", "Granada", "Lamari", "Jardin", "Nismo", "Levina", "Pietra A1", "Pietra A2"}),
    "kitchen": frozenset({"Ocean", "Basalt", "Portland", "Kabon", "Canyon", "Bardeo", "Smoky", "Agat", "Rivoli", "Dallaho", "Antimo", "MT 120", "Pablo", "Sara", "Soul", "Plus Wood"}),
    "commercial": frozenset({"Real Stone", "Canyon", "Bardeo", "Smoky", "Pietra A1", "Pietra A2", "MT 120", "Sara", "Soul", "Mars"}),
    "residential": frozenset({"Ocean", "Vision", "Crystal", "Basalt", "Granada", "Portland", "Bismark", "Lamari", "Galexy", "Jardin", "Nismo", "Varna", "Kabon", "Agat", "Rivoli", "Antimo", "Levina", "Pablo", "Plus Wood"}),
}
WALL_FLOOR_CATEGORY_CONTROLLED_DEMO = (
    CERAMIC_USAGE_CONTROLLED_DEMO["wall"] | CERAMIC_USAGE_CONTROLLED_DEMO["floor"]
)

# Only these Application values are SOURCE_DERIVED; no technical suitability is
# inferred for the remaining adhesive products.
ADHESIVE_APPLICATION_SOURCE_DERIVED = {
    "AK5 Paste Tile Adhesive": "indoor",
    "Zhikafix A-6000": "both",
}

OPTION_FA = {
    "unknown": "نامشخص", "standard": "استاندارد", "porcelain": "پرسلان", "ceramic": "سرامیک", "floor": "کف", "wall": "دیوار", "both": "کف و دیوار", "bathroom": "حمام", "kitchen": "آشپزخانه", "commercial": "تجاری", "residential": "مسکونی",
    "20x20": "۲۰×۲۰ سانتی‌متر", "30x90": "۳۰×۹۰ سانتی‌متر", "60x60": "۶۰×۶۰ سانتی‌متر", "60x120": "۶۰×۱۲۰ سانتی‌متر", "80x160": "۸۰×۱۶۰ سانتی‌متر", "20x120": "۲۰×۱۲۰ سانتی‌متر",
    "white": "سفید", "gold": "طلایی", "gray": "خاکستری", "grey": "خاکستری", "light-gray": "خاکستری روشن", "dark-gray": "خاکستری تیره", "black": "مشکی", "cream": "کرم", "light-cream": "کرم روشن", "dark-cream": "کرم تیره", "brown": "قهوه‌ای", "green": "سبز", "blue": "آبی", "beige": "بژ", "light": "روشن", "dark": "تیره", "gris": "گریس", "grafito": "گرافیتو", "negro": "نگرو", "light-beige": "بژ روشن", "dark-beige": "بژ تیره", "roble": "روبل", "olive": "زیتونی", "matte": "مات", "polished": "پولیش", "nano-polished": "نانو پولیش",
    "powder": "پودری", "paste": "خمیری", "c1e": "C1E", "c2e": "C2E", "indoor": "داخلی", "outdoor": "خارجی",
    "1kg": "۱ کیلوگرم", "3kg": "۳ کیلوگرم", "5kg": "۵ کیلوگرم", "8kg": "۸ کیلوگرم", "12kg": "۱۲ کیلوگرم", "12-5kg": "۱۲٫۵ کیلوگرم", "20kg": "۲۰ کیلوگرم", "25kg": "۲۵ کیلوگرم", "500g": "۵۰۰ گرم", "1-5kg": "۱٫۵ کیلوگرم",
    "cement-based": "پایه سیمانی", "two-part": "دو جزئی", "water-resistant": "مقاوم در برابر آب", "colored": "رنگی", "additive": "افزودنی", "adhesive": "چسب",
}
