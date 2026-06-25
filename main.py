"""
Script principal: ejecuta el scraping completo de Fravega, Cetrogar y OnCity
y guarda los resultados en archivos Excel.

Uso:
    python main.py                  # Ejecutar todos
    python main.py --cetrogar       # Solo Cetrogar
    python main.py --fravega        # Solo Fravega
    python main.py --oncity         # Solo OnCity
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"scraping_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
    ],
)
logger = logging.getLogger("main")


def run_cetrogar(output_dir: Path):
    """Ejecuta el scraper de Cetrogar."""
    from scraper_cetrogar import scrape_cetrogar, save_to_excel

    logger.info("=" * 60)
    logger.info("INICIANDO SCRAPING DE CETROGAR.COM.AR")
    logger.info("=" * 60)

    start = time.time()
    df = scrape_cetrogar()
    elapsed = time.time() - start

    filename = output_dir / f"cetrogar_precios_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    save_to_excel(df, str(filename))

    logger.info(f"Cetrogar completado en {elapsed:.0f}s | {len(df)} SKUs | {filename.name}")
    return df


def run_fravega(output_dir: Path):
    """Ejecuta el scraper de Fravega."""
    from scraper_fravega import scrape_fravega, save_to_excel

    logger.info("=" * 60)
    logger.info("INICIANDO SCRAPING DE FRAVEGA.COM")
    logger.info("=" * 60)

    start = time.time()
    df = scrape_fravega()
    elapsed = time.time() - start

    filename = output_dir / f"fravega_precios_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    save_to_excel(df, str(filename))

    logger.info(f"Fravega completado en {elapsed:.0f}s | {len(df)} productos | {filename.name}")
    return df


def run_oncity(output_dir: Path):
    """Ejecuta el scraper de OnCity."""
    from scraper_oncity import scrape_oncity, save_to_excel

    logger.info("=" * 60)
    logger.info("INICIANDO SCRAPING DE ONCITY.COM.AR")
    logger.info("=" * 60)

    start = time.time()
    df = scrape_oncity()
    elapsed = time.time() - start

    filename = output_dir / f"oncity_precios_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    save_to_excel(df, str(filename))

    logger.info(f"OnCity completado en {elapsed:.0f}s | {len(df)} SKUs | {filename.name}")
    return df


def print_summary(df_cetrogar=None, df_fravega=None, df_oncity=None):
    """Imprime un resumen de los resultados."""
    print("\n" + "=" * 60)
    print("RESUMEN DEL SCRAPING")
    print("=" * 60)

    if df_cetrogar is not None:
        print(f"\n📦 CETROGAR:")
        print(f"   Total SKUs:        {len(df_cetrogar):,}")
        print(f"   Productos únicos:  {df_cetrogar['product_id'].nunique():,}")
        print(f"   Con EAN:           {(df_cetrogar['ean'] != '').sum():,}")
        print(f"   Con precio:        {df_cetrogar['price'].notna().sum():,}")
        if df_cetrogar['price'].notna().any():
            print(f"   Precio mín:        ${df_cetrogar['price'].min():,.0f}")
            print(f"   Precio máx:        ${df_cetrogar['price'].max():,.0f}")

    if df_fravega is not None:
        print(f"\n🛒 FRAVEGA:")
        print(f"   Total productos:   {len(df_fravega):,}")
        print(f"   Con EAN:           {(df_fravega['ean'].fillna('') != '').sum():,}")
        print(f"   Con precio:        {df_fravega['price'].notna().sum():,}")
        if df_fravega['price'].notna().any():
            print(f"   Precio mín:        ${df_fravega['price'].min():,.0f}")
            print(f"   Precio máx:        ${df_fravega['price'].max():,.0f}")

    if df_oncity is not None:
        print(f"\n🏪 ONCITY:")
        print(f"   Total SKUs:        {len(df_oncity):,}")
        print(f"   Productos únicos:  {df_oncity['product_id'].nunique():,}")
        print(f"   Con EAN:           {(df_oncity['ean'] != '').sum():,}")
        print(f"   Con precio:        {df_oncity['price'].notna().sum():,}")
        if df_oncity['price'].notna().any():
            print(f"   Precio mín:        ${df_oncity['price'].min():,.0f}")
            print(f"   Precio máx:        ${df_oncity['price'].max():,.0f}")

    print("\n" + "=" * 60)


def check_dependencies():
    """Verifica que las dependencias estén instaladas."""
    missing = []
    for pkg in ["requests", "playwright", "pandas", "openpyxl", "tqdm"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        logger.error(f"Dependencias faltantes: {', '.join(missing)}")
        logger.error("Ejecutá: pip install -r requirements.txt")
        logger.error("Y luego: playwright install chromium")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Scraper de precios Cetrogar, Fravega y OnCity")
    parser.add_argument("--cetrogar", action="store_true", help="Solo Cetrogar")
    parser.add_argument("--fravega", action="store_true", help="Solo Fravega")
    parser.add_argument("--oncity", action="store_true", help="Solo OnCity")
    parser.add_argument("--output-dir", default=".", help="Directorio de salida (default: .)")
    args = parser.parse_args()

    check_dependencies()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_all = not args.cetrogar and not args.fravega and not args.oncity

    df_cetrogar = None
    df_fravega = None
    df_oncity = None

    total_start = time.time()

    if args.cetrogar or run_all:
        try:
            df_cetrogar = run_cetrogar(output_dir)
        except Exception as e:
            logger.error(f"Error en Cetrogar: {e}", exc_info=True)

    if args.fravega or run_all:
        try:
            df_fravega = run_fravega(output_dir)
        except Exception as e:
            logger.error(f"Error en Fravega: {e}", exc_info=True)

    if args.oncity or run_all:
        try:
            df_oncity = run_oncity(output_dir)
        except Exception as e:
            logger.error(f"Error en OnCity: {e}", exc_info=True)

    total_elapsed = time.time() - total_start
    logger.info(f"Tiempo total: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")

    print_summary(df_cetrogar, df_fravega, df_oncity)


if __name__ == "__main__":
    main()
