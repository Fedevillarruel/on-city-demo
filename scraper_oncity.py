"""
Scraper para OnCity.com
Utiliza la API pública de VTEX para obtener el catálogo completo.
"""

import logging
import time
from urllib.parse import urlparse

import pandas as pd
import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [OnCity] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://www.oncity.com"
SEARCH_API = f"{BASE_URL}/api/catalog_system/pub/products/search/"
CATEGORY_API = f"{BASE_URL}/api/catalog_system/pub/category/tree/10"
PAGE_SIZE = 50  # máximo permitido por VTEX

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
}


def extract_ean(item: dict) -> str:
    """Obtiene EAN con fallbacks para distintos formatos VTEX."""
    ean = (item.get("ean") or "").strip()
    if ean:
        return ean

    ean_list = item.get("eanList") or item.get("eanlist") or []
    if isinstance(ean_list, list) and ean_list:
        candidate = str(ean_list[0]).strip()
        if candidate:
            return candidate

    refs = item.get("referenceId", [])
    if refs:
        candidate = str(refs[0].get("Value", "")).strip()
        if candidate.isdigit() and len(candidate) in (8, 12, 13, 14):
            return candidate

    return ""


def extract_total_from_headers(response: requests.Response) -> int:
    """Extrae el total de productos desde headers estándar de VTEX."""
    for header in ("resources", "Content-Range", "X-Total-Count"):
        value = response.headers.get(header, "")
        if "/" in value:
            return int(value.split("/")[-1].strip())
        if value.isdigit():
            return int(value)
    return 0


def normalize_category_path(raw_path: str) -> str:
    """Normaliza categorías a path relativo para construir URLs VTEX válidas."""
    if not raw_path:
        return ""

    value = raw_path.strip()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.path

    return value.strip("/")


def fetch_page(session: requests.Session, start: int, end: int, category_path: str = None) -> list:
    """Obtiene una página de productos desde la API de VTEX, opcionalmente por categoría."""
    normalized_path = normalize_category_path(category_path) if category_path else ""

    if normalized_path:
        url = f"{SEARCH_API}{normalized_path}/?_from={start}&_to={end}"
    else:
        url = f"{SEARCH_API}?_from={start}&_to={end}"

    for attempt in range(4):
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            if response.status_code in (200, 206):
                return response.json()
            if response.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning(f"Rate limit. Esperando {wait}s...")
                time.sleep(wait)
                continue

            logger.error(f"Error {response.status_code} en {category_path or '/'} pos={start}")
            return []
        except requests.RequestException as e:
            wait = 5 * (attempt + 1)
            logger.warning(f"Intento {attempt + 1} fallido: {e}. Reintentando en {wait}s...")
            time.sleep(wait)
    return []


def get_category_total(session: requests.Session, category_path: str) -> int:
    """Obtiene el total de productos de una categoría vía path URL."""
    normalized_path = normalize_category_path(category_path)
    if not normalized_path:
        return 0

    url = f"{SEARCH_API}{normalized_path}/?_from=0&_to=0"
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        return extract_total_from_headers(response)
    except Exception as e:
        logger.warning(f"No se pudo obtener total de {category_path}: {e}")
    return 0


def get_global_total(session: requests.Session) -> int:
    """Obtiene el total global de productos de OnCity según la API."""
    url = f"{SEARCH_API}?_from=0&_to=0"
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        return extract_total_from_headers(response)
    except Exception as e:
        logger.warning(f"No se pudo obtener total global: {e}")
        return 0


def get_leaf_categories(session: requests.Session) -> list[dict]:
    """Obtiene categorías hoja con su URL path relativo."""
    try:
        resp = session.get(CATEGORY_API, headers=HEADERS, timeout=30)
        tree = resp.json()
    except Exception as e:
        logger.warning(f"No se pudo obtener árbol de categorías: {e}")
        return []

    leaves = []

    def walk(nodes: list[dict]):
        for node in nodes:
            children = node.get("children", [])
            if children:
                walk(children)
                continue

            path = normalize_category_path(node.get("url", ""))
            if not path:
                continue
            leaves.append({"id": node.get("id"), "name": node.get("name", ""), "path": path})

    walk(tree)
    return leaves


def parse_product(product: dict) -> list[dict]:
    """Extrae los datos relevantes de un producto VTEX (puede tener múltiples SKUs)."""
    rows = []

    product_id = product.get("productId", "")
    product_name = product.get("productName", "")
    brand = product.get("brand", "")
    product_reference = product.get("productReference", "")
    category_path = " > ".join(
        [c.strip("/").split("/")[-1] for c in reversed(product.get("categories", [])) if c.strip("/")]
    )
    link = product.get("link", "")
    description = product.get("description", "")

    all_specs = product.get("allSpecifications", [])
    specs_dict = {
        k: ", ".join(v) if isinstance(v, list) else str(v)
        for k, v in product.items()
        if k in all_specs
    }

    for item in product.get("items", []):
        sku_id = item.get("itemId", "")
        ean = extract_ean(item)
        sku_name = item.get("nameComplete", product_name)

        ref_ids = item.get("referenceId", [])
        ref_value = ref_ids[0].get("Value", "") if ref_ids else ""

        price = None
        list_price = None
        available = False
        stock_qty = None
        seller_name = ""

        for seller in item.get("sellers", []):
            offer = seller.get("commertialOffer", {})
            if offer.get("IsAvailable", False):
                price = offer.get("Price")
                list_price = offer.get("ListPrice")
                available = True
                stock_qty = offer.get("AvailableQuantity")
                seller_name = seller.get("sellerName", "")
                break

        if price is None:
            for seller in item.get("sellers", []):
                offer = seller.get("commertialOffer", {})
                price = offer.get("Price")
                list_price = offer.get("ListPrice")
                if stock_qty is None:
                    stock_qty = offer.get("AvailableQuantity")
                seller_name = seller.get("sellerName", "")
                if price:
                    break

        images = item.get("images", [])
        image_url = images[0].get("imageUrl", "") if images else ""

        selected_offer = {}
        for seller in item.get("sellers", []):
            offer = seller.get("commertialOffer", {})
            if offer.get("IsAvailable") and offer.get("Installments"):
                selected_offer = offer
                break
        if not selected_offer:
            for seller in item.get("sellers", []):
                offer = seller.get("commertialOffer", {})
                if offer.get("Installments"):
                    selected_offer = offer
                    break

        installments_list = selected_offer.get("Installments", [])
        sin_interes = [
            i
            for i in installments_list
            if i.get("InterestRate", 1) == 0 and i.get("NumberOfInstallments", 1) > 1
        ]
        mejor_promo = ""
        cuotas_min = None
        cuotas_max = None
        if sin_interes:
            sin_interes_sorted = sorted(sin_interes, key=lambda x: x["NumberOfInstallments"])
            cuotas_min = sin_interes_sorted[0]["NumberOfInstallments"]
            cuotas_max = sin_interes_sorted[-1]["NumberOfInstallments"]
            best = sin_interes_sorted[-1]
            mejor_promo = (
                f"{best['NumberOfInstallments']}x${best['Value']:,.0f} "
                f"sin interés ({best['PaymentSystemName']})"
            )

        row = {
            "product_id": product_id,
            "product_name": product_name,
            "sku_id": sku_id,
            "sku_name": sku_name,
            "ean": ean,
            "ref_id": ref_value or product_reference,
            "brand": brand,
            "category": category_path,
            "price": price,
            "list_price": list_price,
            "available": available,
            "stock_qty": stock_qty,
            "mejor_promo": mejor_promo,
            "cuotas_sin_interes_min": cuotas_min,
            "cuotas_sin_interes_max": cuotas_max,
            "seller": seller_name,
            "url": link,
            "image_url": image_url,
            "description": description[:500] if description else "",
            "competencia": "oncity",
            "ventas_periodo": None,
        }

        for key in [
            "Modelo",
            "Tipo de producto",
            "Tamaño de la pantalla",
            "Tipo de resolución",
            "Origen",
            "Garantía del proveedor",
            "Dimensiones",
            "Peso",
        ]:
            row[key] = specs_dict.get(key, "")

        rows.append(row)

    return rows


def scrape_oncity() -> pd.DataFrame:
    """Descarga el catálogo completo de OnCity paginando por categorías hoja."""
    session = requests.Session()
    seen_product_ids: set[str] = set()
    all_rows = []

    logger.info("Obteniendo árbol de categorías...")
    categories = get_leaf_categories(session)
    logger.info(f"Categorías hoja encontradas: {len(categories)}")
    api_global_total = get_global_total(session)
    if api_global_total:
        logger.info(f"Total global reportado por API: {api_global_total}")

    with tqdm(total=len(categories), desc="OnCity", unit="cat") as pbar:
        for cat in categories:
            cat_path = cat["path"]
            total = get_category_total(session, cat_path)
            if total == 0:
                pbar.update(1)
                continue

            for start in range(0, total + PAGE_SIZE, PAGE_SIZE):
                end = start + PAGE_SIZE - 1
                products = fetch_page(session, start, end, category_path=cat_path)
                if not products:
                    break

                for product in products:
                    pid = product.get("productId", "")
                    if pid in seen_product_ids:
                        continue
                    seen_product_ids.add(pid)
                    all_rows.extend(parse_product(product))

                if len(products) < PAGE_SIZE:
                    break
                time.sleep(0.3)

            pbar.update(1)

    # Fallback defensivo: si por diferencias de indexado de categorías falta cobertura,
    # completar con un barrido global del catálogo.
    if api_global_total and len(seen_product_ids) < api_global_total:
        missing_before = api_global_total - len(seen_product_ids)
        logger.info(
            f"Cobertura incompleta tras categorías. Faltan ~{missing_before} productos. "
            "Iniciando fallback global..."
        )

        for start in range(0, api_global_total + PAGE_SIZE, PAGE_SIZE):
            end = start + PAGE_SIZE - 1
            products = fetch_page(session, start, end)
            if not products:
                break

            for product in products:
                pid = product.get("productId", "")
                if not pid or pid in seen_product_ids:
                    continue
                seen_product_ids.add(pid)
                all_rows.extend(parse_product(product))

            if len(products) < PAGE_SIZE:
                break
            time.sleep(0.3)

    logger.info(f"Total de SKUs descargados: {len(all_rows)} ({len(seen_product_ids)} productos únicos)")
    if api_global_total:
        coverage = (len(seen_product_ids) / api_global_total) * 100 if api_global_total else 0
        logger.info(f"Cobertura vs API global: {len(seen_product_ids)}/{api_global_total} ({coverage:.2f}%)")
    return pd.DataFrame(all_rows)


def save_to_excel(df: pd.DataFrame, filename: str = "oncity_precios.xlsx"):
    """Guarda el DataFrame en Excel con formato simple de columnas."""
    logger.info(f"Guardando {len(df)} registros en {filename}...")

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Catálogo", index=False)
        ws = writer.sheets["Catálogo"]

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    logger.info(f"Archivo guardado: {filename}")


if __name__ == "__main__":
    df = scrape_oncity()
    save_to_excel(df)
    print(df.head())
    print(f"\nTotal registros: {len(df)}")