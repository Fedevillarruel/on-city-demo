"""
Scraper para Fravega.com - v3 (API GraphQL directa)
Usa la API interna /api/v1 con GraphQL para obtener el catálogo completo
con paginación real. No requiere Playwright. Extrae:
  - Nombre, SKU, EAN, Marca, Categoría, Precio oferta, Precio lista,
    Descuento %, URL, Imagen
"""

import requests
import json
import os
import re
import time
import logging
import math
import pandas as pd
from tqdm import tqdm


def _parse_cuotas_count(name: str) -> int:
    """Extrae el número de cuotas de un texto como '9 cuotas sin interés'."""
    m = re.search(r'(\d+)\s+cuota', name, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _extract_ean(item: dict) -> str:
    """Obtiene EAN con fallbacks desde la estructura GraphQL de Fravega."""
    gtin = item.get("gtin") or {}
    for key in ("number", "ean", "value"):
        candidate = str(gtin.get(key, "") or "").strip()
        if candidate:
            return candidate

    skus_data = (item.get("skus") or {}).get("results", [])
    for sku in skus_data:
        for key in ("ean", "gtin", "barcode"):
            candidate = str(sku.get(key, "") or "").strip()
            if candidate:
                return candidate

    return ""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Fravega] %(message)s")
logger = logging.getLogger(__name__)

API_URL = "https://www.fravega.com/api/v1"
PAGE_SIZE = 48
MAX_RETRIES = 4
POSTAL_CODE = "C1406"

# Cargar query GraphQL desde el archivo capturado
_QUERY_FILE = os.path.join(os.path.dirname(__file__), "fravega_graphql_query.json")
with open(_QUERY_FILE) as _f:
    _CAPTURED = json.load(_f)
GRAPHQL_QUERY = _CAPTURED["query"]
_BASE_VARS = _CAPTURED["variables"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.fravega.com",
    "Referer": "https://www.fravega.com/",
}

# Categorías: (slug para el filtro, nombre legible)
CATEGORIES = [
    ("celulares/celulares-liberados", "Celulares"),
    ("celulares", "Celulares"),
    ("tv-y-video/tv", "TV y Video"),
    ("tv-y-video", "TV y Video"),
    ("informatica/notebooks", "Informática"),
    ("informatica/tablets", "Informática"),
    ("informatica", "Informática"),
    ("audio", "Audio"),
    ("heladeras-freezers-y-cavas/heladeras", "Heladeras y Freezers"),
    ("heladeras-freezers-y-cavas/freezers", "Heladeras y Freezers"),
    ("heladeras-freezers-y-cavas", "Heladeras y Freezers"),
    ("lavado/lavarropas", "Lavado"),
    ("lavado/secarropas", "Lavado"),
    ("lavado/lavasecarropas", "Lavado"),
    ("lavado", "Lavado"),
    ("cocina/cocinas", "Cocina"),
    ("cocina/hornos-y-microondas", "Cocina"),
    ("cocina", "Cocina"),
    ("pequenos-electrodomesticos/cocina", "Pequeños Electro"),
    ("pequenos-electrodomesticos/cuidado-personal", "Pequeños Electro"),
    ("pequenos-electrodomesticos", "Pequeños Electro"),
    ("climatizacion/aire-acondicionado", "Climatización"),
    ("climatizacion/ventiladores", "Climatización"),
    ("climatizacion/calefaccion", "Climatización"),
    ("climatizacion", "Climatización"),
    ("termotanques-y-calefones", "Termotanques"),
    ("hogar/bazar", "Hogar"),
    ("hogar/colchones-y-sommiers", "Hogar"),
    ("hogar", "Hogar"),
    ("muebles/dormitorio/placards", "Muebles"),
    ("muebles/dormitorio", "Muebles"),
    ("muebles/living", "Muebles"),
    ("muebles", "Muebles"),
    ("herramientas-y-construccion/herramientas/herramientas-electricas", "Herramientas"),
    ("herramientas-y-construccion/herramientas", "Herramientas"),
    ("herramientas-y-construccion", "Herramientas"),
    ("deportes-y-fitness/bicicletas", "Deportes"),
    ("deportes-y-fitness", "Deportes"),
    ("juguetes", "Juguetes"),
    ("relojes-y-accesorios", "Relojes"),
    ("gaming", "Gaming"),
    ("fotografia", "Fotografía"),
    ("iluminacion", "Iluminación"),
    ("seguridad", "Seguridad"),
]


def build_payload(category_slug: str, offset: int = 0, size: int = PAGE_SIZE) -> dict:
    """Construye el payload GraphQL para una categoría con paginación."""
    parts = category_slug.split("/")
    is_single = len(parts) == 1
    return {
        "operationName": "listProducts_Shopping",
        "variables": {
            **_BASE_VARS,
            "isSingleCategory": is_single,
            "filtering": {
                **_BASE_VARS.get("filtering", {}),
                "categories": [category_slug],
            },
            "size": size,
            "offset": offset,
        },
        "query": GRAPHQL_QUERY,
    }


def fetch_page(session: requests.Session, category_slug: str, offset: int) -> tuple[list, int]:
    """Llama a la API GraphQL y retorna (productos, total)."""
    payload = build_payload(category_slug, offset)
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(API_URL, json=payload, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                body = resp.json()
                items = (body.get("data") or {}).get("items") or {}
                total = int(items.get("total") or 0)
                results = list(items.get("results") or [])
                return results, total
            elif resp.status_code == 429:
                wait = 15 * (attempt + 1)
                logger.warning(f"Rate limit. Esperando {wait}s...")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code} en {category_slug} offset={offset}")
                return [], 0
        except Exception as e:
            wait = 5 * (attempt + 1)
            logger.warning(f"Error intento {attempt+1}: {e}. Reintentando en {wait}s...")
            time.sleep(wait)
    return [], 0


def parse_product(item: dict, category_name: str) -> dict:
    """Extrae campos normalizados de un producto de la API GraphQL."""
    # EAN
    ean = _extract_ean(item)

    # Precios (en centavos / valores enteros en ARS)
    sale_price_data = item.get("salePrice") or {}
    list_price_data = item.get("listPrice") or {}
    sale_amounts = (sale_price_data.get("amounts") or [{}])
    list_amounts = (list_price_data.get("amounts") or [{}])
    sale_discounts = (sale_price_data.get("discounts") or [{}])

    sale_price = sale_amounts[0].get("min") if sale_amounts else None
    list_price = list_amounts[0].get("min") if list_amounts else None
    discount_pct = sale_discounts[0].get("min") if sale_discounts else None

    # SKU principal (primer SKU del primer bucket)
    skus_data = (item.get("skus") or {}).get("results", [])
    sku_code = skus_data[0].get("code", "") if skus_data else ""

    # Marca
    brand_data = item.get("brand") or {}
    brand = brand_data.get("name", "")

    # Imágenes
    images = item.get("images") or []
    image_url = f"https://images.fravega.com/f500/{images[0]}" if images else ""

    # Vendedor
    sellers = item.get("sellers") or [{}]
    seller = sellers[0].get("commercialName", "Fravega") if sellers else "Fravega"

    # Stock/disponibilidad (best effort, depende de payload)
    stock_qty = None
    available = None
    availability = item.get("availability")
    if isinstance(availability, bool):
        available = availability
    elif isinstance(availability, str):
        available = availability.lower() in ("available", "in_stock", "instock", "true")

    if skus_data:
        for key in ("stockQuantity", "availableQuantity", "stock"):
            candidate = skus_data[0].get(key)
            if candidate is not None:
                stock_qty = candidate
                break

    # URL
    slug = item.get("slug", "")
    url = f"https://www.fravega.com/p/{slug}-{sku_code}/" if slug and sku_code else ""

    # Cuotas / promos bancarias
    installments_data = item.get("installments") or {}
    installments_values = installments_data.get("values") or []
    mejor_promo = ""
    cuotas_min = None
    cuotas_max = None
    if installments_values:
        counts = []
        for v in installments_values:
            name = v.get("name", "")
            n = _parse_cuotas_count(name)
            counts.append((n, name))
        counts.sort()
        valid = [(n, name) for n, name in counts if n > 0]
        if valid:
            cuotas_min = valid[0][0]
            cuotas_max = valid[-1][0]
            mejor_promo = valid[-1][1]
        elif counts:
            mejor_promo = counts[-1][1]

    return {
        "product_id": item.get("id", ""),
        "sku_id": sku_code,
        "product_name": item.get("title", ""),
        "brand": brand,
        "category": category_name,
        "ean": ean,
        "price": sale_price,
        "list_price": list_price,
        "discount_pct": discount_pct,
        "available": available,
        "stock_qty": stock_qty,
        "mejor_promo": mejor_promo,
        "cuotas_sin_interes_min": cuotas_min,
        "cuotas_sin_interes_max": cuotas_max,
        "seller": seller,
        "url": url,
        "image_url": image_url,
        "competencia": "fravega",
        "ventas_periodo": None,
    }


def scrape_fravega() -> pd.DataFrame:
    """Descarga el catálogo completo de Fravega via API GraphQL."""
    session = requests.Session()
    all_products: dict[str, dict] = {}  # keyed por sku_id para deduplicar

    logger.info(f"Iniciando scraping de {len(CATEGORIES)} categorías...")

    for category_slug, category_name in tqdm(CATEGORIES, desc="Fravega categorías", unit="cat"):
        # Primera página: obtener total
        results0, total = fetch_page(session, category_slug, 0)
        if not results0 and total == 0:
            continue

        for item in results0:
            p = parse_product(item, category_name)
            if p["sku_id"] and p["sku_id"] not in all_products:
                all_products[p["sku_id"]] = p

        pages = math.ceil(total / PAGE_SIZE)
        logger.debug(f"{category_slug}: total={total}, páginas={pages}")

        for pg in range(1, pages):
            offset = pg * PAGE_SIZE
            results, _ = fetch_page(session, category_slug, offset)
            if not results:
                break
            new_count = 0
            for item in results:
                p = parse_product(item, category_name)
                if p["sku_id"] and p["sku_id"] not in all_products:
                    all_products[p["sku_id"]] = p
                    new_count += 1
            time.sleep(0.3)  # pausa respetuosa

    df = pd.DataFrame(list(all_products.values()))
    logger.info(f"Total productos únicos: {len(df)}")
    return df


def save_to_excel(df: pd.DataFrame, filename: str = "fravega_precios.xlsx"):
    logger.info(f"Guardando {len(df)} registros en {filename}...")
    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Catálogo", index=False)
        ws = writer.sheets["Catálogo"]
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)
    logger.info(f"Archivo guardado: {filename}")


if __name__ == "__main__":
    df = scrape_fravega()
    save_to_excel(df)
    print(df.head())
    print(f"\nTotal: {len(df)}")
