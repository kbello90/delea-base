"""showcase/products.yaml: parse and validate the hand-edited product list.

Every problem is collected and reported at once, so a hand edit can be fixed in one pass.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

METALS = ("gold_14k", "gold_18k", "rose_gold", "white_gold", "silver_925", "platinum", "other")
MAX_IMAGES = 10  # the API's per-product limit
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_SKU = re.compile(r"^DL-[A-Z0-9-]{2,60}$")
_MONEY = re.compile(r"^\d{1,7}(?:\.\d{1,2})?$")


class CatalogError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("showcase/products.yaml has problems:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ProductEntry:
    sku: str
    folder: str
    name: str
    description: str
    metal: str
    price_cents: int
    sale_price_cents: int | None
    stock: int  # on hand today, after the loaded sales
    low_stock_threshold: int
    published: bool
    images: tuple[str, ...]  # display order; the first is primary


def money_to_cents(text: object) -> int:
    """ "38.00" or "38" -> 3800. Strings only (YAML would read 38.10 as a float)."""
    if not isinstance(text, str) or not _MONEY.match(text.strip()):
        raise ValueError(f'must be a quoted amount like "38.00", got {text!r}')
    whole, _, fraction = text.strip().partition(".")
    return int(whole) * 100 + int(fraction.ljust(2, "0") or 0)


def _entry(raw: object, index: int, problems: list[str]) -> ProductEntry | None:
    where = f"product {index + 1}"
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be a mapping")
        return None
    where = f"product {index + 1} ({raw.get('sku', 'no sku')})"
    start = len(problems)

    def need(key: str, kind: type) -> object:
        value = raw.get(key)
        if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
            problems.append(f"{where}: {key} must be a {kind.__name__}")
        return value

    sku, folder, name = need("sku", str), need("folder", str), need("name", str)
    description, metal = need("description", str), need("metal", str)
    stock, threshold = need("stock", int), need("low_stock_threshold", int)
    published, images = need("published", bool), need("images", list)
    if isinstance(sku, str) and not _SKU.match(sku):
        problems.append(f"{where}: sku must look like DL-XX-NAME-01 (capitals, digits, dashes)")
    if isinstance(name, str) and not 1 <= len(name.strip()) <= 200:
        problems.append(f"{where}: name must be 1 to 200 characters")
    if isinstance(metal, str) and metal not in METALS:
        problems.append(f"{where}: metal must be one of {', '.join(METALS)}")
    for key, value in (("stock", stock), ("low_stock_threshold", threshold)):
        if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value <= 100_000:
            problems.append(f"{where}: {key} must be between 0 and 100000")
    try:
        price = money_to_cents(raw.get("price"))
    except ValueError as exc:
        problems.append(f"{where}: price {exc}")
        price = 0
    sale = None
    if raw.get("sale_price") is not None:
        try:
            sale = money_to_cents(raw.get("sale_price"))
            if sale >= price:
                problems.append(f"{where}: sale_price must be lower than price")
        except ValueError as exc:
            problems.append(f"{where}: sale_price {exc}")
    if isinstance(images, list):
        if not 1 <= len(images) <= MAX_IMAGES:
            problems.append(f"{where}: images must list 1 to {MAX_IMAGES} files")
        if any(not isinstance(i, str) or not i.strip() for i in images):
            problems.append(f"{where}: every image must be a filename")
        elif len(set(images)) != len(images):
            problems.append(f"{where}: an image is listed twice")
        elif any(not i.lower().endswith(IMAGE_EXTENSIONS) for i in images):
            problems.append(f"{where}: images must be {', '.join(IMAGE_EXTENSIONS)} files")
    if len(problems) > start:
        return None
    return ProductEntry(
        sku=sku,
        folder=folder,
        name=name.strip(),
        description=" ".join(description.split()),
        metal=metal,
        price_cents=price,
        sale_price_cents=sale,
        stock=stock,
        low_stock_threshold=threshold,
        published=published,
        images=tuple(images),
    )


def load_catalog(path: Path) -> list[ProductEntry]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError([f"can't read {path.name}: {exc}"]) from exc
    raw_products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(raw_products, list) or not raw_products:
        raise CatalogError(["the file needs a non-empty 'products:' list"])
    problems: list[str] = []
    entries = [_entry(raw, i, problems) for i, raw in enumerate(raw_products)]
    skus = [e.sku for e in entries if e]
    for sku in sorted({s for s in skus if skus.count(s) > 1}):
        problems.append(f"sku {sku} is used twice")
    if problems:
        raise CatalogError(problems)
    return [e for e in entries if e]


def check_photos(entries: list[ProductEntry], photos_dir: Path) -> None:
    """Every listed folder and file exists under photos_dir (nothing is read or copied)."""
    problems = []
    if not photos_dir.is_dir():
        raise CatalogError([f"SHOWCASE_DIR is not a folder: {photos_dir}"])
    for entry in entries:
        folder = photos_dir / entry.folder
        if not folder.is_dir():
            problems.append(f"{entry.sku}: folder not found: {entry.folder}")
            continue
        for image in entry.images:
            if not (folder / image).is_file():
                problems.append(f"{entry.sku}: image not found: {entry.folder}/{image}")
    if problems:
        raise CatalogError(problems)
