import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

from cron_store import DB_PATH

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


CREATE_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    competencia TEXT NOT NULL,
    ean TEXT NOT NULL,
    product_name TEXT,
    brand TEXT,
    category TEXT,
    seller TEXT,
    price REAL,
    list_price REAL,
    stock_qty REAL,
    UNIQUE(run_at, competencia, ean)
);
"""


CREATE_ALERTS_SQL = """
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    ean TEXT NOT NULL,
    product_name TEXT,
    category TEXT,
    best_company TEXT,
    worst_company TEXT,
    precio_min REAL,
    precio_max REAL,
    brecha_pct REAL,
    threshold_pct REAL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_intelligence_db() -> None:
    with closing(_connect()) as conn:
        conn.execute(CREATE_SNAPSHOTS_SQL)
        conn.execute(CREATE_ALERTS_SQL)
        conn.commit()


def _find_latest_file(base_dir: Path, pattern: str) -> Path | None:
    files = sorted(base_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load_source(path: Path | None, source_name: str) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    try:
        df = pd.read_excel(path)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df

    if "competencia" not in df.columns:
        df["competencia"] = source_name
    else:
        df["competencia"] = df["competencia"].fillna(source_name).astype(str).str.lower()

    for col in ["ean", "product_name", "brand", "category", "seller"]:
        if col not in df.columns:
            df[col] = ""

    for col in ["price", "list_price", "stock_qty"]:
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ean"] = df["ean"].fillna("").astype(str).str.strip()
    return df


def load_latest_catalog(base_dir: Path) -> pd.DataFrame:
    oncity_file = _find_latest_file(base_dir, "oncity_precios*.xlsx")
    fravega_file = _find_latest_file(base_dir, "fravega_precios*.xlsx")
    cetrogar_file = _find_latest_file(base_dir, "cetrogar_precios*.xlsx")

    df_oncity = _load_source(oncity_file, "oncity")
    df_fravega = _load_source(fravega_file, "fravega")
    df_cetrogar = _load_source(cetrogar_file, "cetrogar")

    return pd.concat([df_oncity, df_fravega, df_cetrogar], ignore_index=True)


def _build_comparison(base_df: pd.DataFrame) -> pd.DataFrame:
    df = base_df.copy()
    df = df[(df["ean"].astype(str).str.strip() != "") & (df["price"].notna())].copy()
    if df.empty:
        return pd.DataFrame()

    prices = df.groupby(["ean", "competencia"], as_index=False)["price"].min()
    pivot = prices.pivot(index="ean", columns="competencia", values="price").reset_index()

    meta = (
        df.sort_values(["ean", "product_name"])
        .groupby("ean", as_index=False)
        .agg(product_name=("product_name", "first"), category=("category", "first"), brand=("brand", "first"))
    )

    comp = meta.merge(pivot, on="ean", how="left")
    company_cols = [c for c in ["oncity", "fravega", "cetrogar"] if c in comp.columns]
    if not company_cols:
        return pd.DataFrame()

    comp["empresas_con_precio"] = comp[company_cols].notna().sum(axis=1)
    comp = comp[comp["empresas_con_precio"] >= 2].copy()
    if comp.empty:
        return pd.DataFrame()

    comp["precio_min"] = comp[company_cols].min(axis=1)
    comp["precio_max"] = comp[company_cols].max(axis=1)
    comp["brecha_pct"] = ((comp["precio_max"] / comp["precio_min"]) - 1) * 100
    comp["best_company"] = comp[company_cols].idxmin(axis=1)
    comp["worst_company"] = comp[company_cols].idxmax(axis=1)
    return comp


def process_post_run(base_dir: Path, run_at_iso: str, alert_threshold_pct: float = 25.0) -> dict:
    init_intelligence_db()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_latest_catalog(base_dir)
    if raw.empty:
        return {"snapshots": 0, "alerts": 0, "report": ""}

    # Snapshot consolidado por EAN + empresa para no inflar volumen.
    snaps = (
        raw[(raw["ean"].astype(str).str.strip() != "") & (raw["price"].notna())]
        .groupby(["ean", "competencia"], as_index=False)
        .agg(
            product_name=("product_name", "first"),
            brand=("brand", "first"),
            category=("category", "first"),
            seller=("seller", "first"),
            price=("price", "min"),
            list_price=("list_price", "min"),
            stock_qty=("stock_qty", "max"),
        )
    )

    with closing(_connect()) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO price_snapshots (
                run_at, competencia, ean, product_name, brand, category,
                seller, price, list_price, stock_qty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_at_iso,
                    str(r["competencia"]),
                    str(r["ean"]),
                    str(r.get("product_name", "")),
                    str(r.get("brand", "")),
                    str(r.get("category", "")),
                    str(r.get("seller", "")),
                    float(r["price"]) if pd.notna(r["price"]) else None,
                    float(r["list_price"]) if pd.notna(r["list_price"]) else None,
                    float(r["stock_qty"]) if pd.notna(r["stock_qty"]) else None,
                )
                for _, r in snaps.iterrows()
            ],
        )

        comp = _build_comparison(raw)
        alerts = comp[comp["brecha_pct"] >= float(alert_threshold_pct)].copy() if not comp.empty else pd.DataFrame()

        if not alerts.empty:
            conn.executemany(
                """
                INSERT INTO alert_events (
                    run_at, ean, product_name, category, best_company, worst_company,
                    precio_min, precio_max, brecha_pct, threshold_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_at_iso,
                        str(r["ean"]),
                        str(r.get("product_name", "")),
                        str(r.get("category", "")),
                        str(r.get("best_company", "")),
                        str(r.get("worst_company", "")),
                        float(r["precio_min"]) if pd.notna(r["precio_min"]) else None,
                        float(r["precio_max"]) if pd.notna(r["precio_max"]) else None,
                        float(r["brecha_pct"]) if pd.notna(r["brecha_pct"]) else None,
                        float(alert_threshold_pct),
                    )
                    for _, r in alerts.iterrows()
                ],
            )

        conn.commit()

    report_file = REPORTS_DIR / f"reporte_ejecutivo_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    _generate_exec_report(report_file, run_at_iso, raw, comp if 'comp' in locals() else pd.DataFrame())

    return {"snapshots": int(len(snaps)), "alerts": int(len(alerts) if 'alerts' in locals() else 0), "report": str(report_file.name)}


def _generate_exec_report(report_file: Path, run_at_iso: str, raw_df: pd.DataFrame, comp_df: pd.DataFrame) -> None:
    total_rows = len(raw_df)
    total_products = int(raw_df["ean"].astype(str).str.strip().replace("", pd.NA).dropna().nunique()) if "ean" in raw_df.columns else 0
    avg_price = float(raw_df["price"].dropna().mean()) if "price" in raw_df.columns and raw_df["price"].notna().any() else 0.0

    winner_text = "N/A"
    if not comp_df.empty and "best_company" in comp_df.columns:
        winner_text = str(comp_df["best_company"].value_counts().idxmax())

    html = f"""
    <html>
      <head><meta charset='utf-8'><title>Reporte Ejecutivo</title></head>
      <body style='font-family: Arial, sans-serif; padding: 24px;'>
        <h1>Reporte Ejecutivo Diario</h1>
        <p><b>Corrida:</b> {run_at_iso}</p>
        <ul>
          <li>Filas procesadas: {total_rows:,}</li>
          <li>Productos (EAN) únicos: {total_products:,}</li>
          <li>Precio promedio: ${avg_price:,.0f}</li>
          <li>Empresa más competitiva: {winner_text}</li>
        </ul>
      </body>
    </html>
    """

    report_file.write_text(html, encoding="utf-8")


def get_alerts(limit: int = 200) -> pd.DataFrame:
    init_intelligence_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT run_at, ean, product_name, category, best_company, worst_company,
                   precio_min, precio_max, brecha_pct, threshold_pct
            FROM alert_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def get_brand_scores(limit: int = 200) -> pd.DataFrame:
    init_intelligence_db()
    with closing(_connect()) as conn:
        max_run = conn.execute("SELECT MAX(run_at) as max_run FROM price_snapshots").fetchone()["max_run"]
        if not max_run:
            return pd.DataFrame()

        rows = conn.execute(
            """
            WITH base AS (
                SELECT run_at, ean, brand, competencia, price
                FROM price_snapshots
                WHERE run_at = ?
            ),
            best AS (
                SELECT b1.ean, b1.brand, b1.competencia
                FROM base b1
                JOIN (
                    SELECT ean, MIN(price) AS min_price
                    FROM base
                    GROUP BY ean
                ) b2 ON b1.ean = b2.ean AND b1.price = b2.min_price
            )
            SELECT brand, competencia, COUNT(*) AS wins
            FROM best
            WHERE brand IS NOT NULL AND brand <> ''
            GROUP BY brand, competencia
            ORDER BY wins DESC
            LIMIT ?
            """,
            (max_run, int(limit)),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def get_price_history(ean: str) -> pd.DataFrame:
    init_intelligence_db()
    if not ean:
        return pd.DataFrame()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT run_at, competencia, price, list_price, stock_qty
            FROM price_snapshots
            WHERE ean = ?
            ORDER BY run_at
            """,
            (ean.strip(),),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def list_reports(limit: int = 30) -> list[Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob("reporte_ejecutivo_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[: int(limit)]
