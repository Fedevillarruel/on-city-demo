import io
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_elements import dashboard, elements, mui, sync

from cron_store import (
    add_job,
    delete_job,
    latest_logs,
    list_jobs,
    seed_default_jobs,
    set_job_enabled,
    update_job,
)
from intelligence_store import get_alerts, get_brand_scores, get_price_history, list_reports, process_post_run

BASE_DIR = Path(__file__).resolve().parent
LAYOUT_FILE = BASE_DIR / "dashboard_layout.json"

pd.set_option("styler.render.max_elements", 4_000_000)


st.set_page_config(
    page_title="Centro de Inteligencia de Precios",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;600;700&family=Space+Grotesk:wght@500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Barlow', sans-serif;
}

.main {
    background: radial-gradient(circle at 10% 10%, #fef5d9 0%, #f4fbf8 35%, #eef6ff 100%);
}

.block-container {
    padding-top: 1rem;
    padding-bottom: 2rem;
}

h1, h2, h3 {
    font-family: 'Space Grotesk', sans-serif;
}

.metric-card {
    border-radius: 18px;
    padding: 14px 16px;
    background: linear-gradient(135deg, #0f172a, #1d4ed8);
    color: #fff;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.22);
}

.metric-label {
    opacity: 0.85;
    font-size: 0.85rem;
}

.metric-value {
    font-size: 1.55rem;
    font-weight: 700;
}

.widget-help {
    font-size: 0.85rem;
    color: #334155;
}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ── Nombres de columnas en español ──────────────────────────────────────────
COL_LABELS: dict[str, str] = {
    "competencia": "Empresa",
    "product_id": "ID Producto",
    "sku_id": "SKU",
    "product_name": "Nombre del Producto",
    "brand": "Marca",
    "category": "Categoría",
    "seller": "Vendedor",
    "ean": "EAN",
    "price": "Precio ($)",
    "list_price": "Precio de Lista ($)",
    "cuotas_sin_interes_min": "Cuotas Mín s/interés",
    "cuotas_sin_interes_max": "Cuotas Máx s/interés",
    "mejor_promo": "Mejor Promoción",
    "available": "Disponible",
    "stock_qty": "Stock",
    "url": "URL",
    "discount_pct": "Descuento %",
    "oncity": "Precio OnCity ($)",
    "fravega": "Precio Fravega ($)",
    "cetrogar": "Precio Cetrogar ($)",
    "oncity_pct_vs_min": "OnCity % vs Mínimo",
    "fravega_pct_vs_min": "Fravega % vs Mínimo",
    "cetrogar_pct_vs_min": "Cetrogar % vs Mínimo",
    "precio_min": "Precio Mínimo ($)",
    "precio_max": "Precio Máximo ($)",
    "brecha_abs": "Diferencia ($)",
    "brecha_pct": "Diferencia (%)",
    "mejor_precio_empresa": "Empresa más barata",
    "empresas_con_precio": "Empresas con precio",
    "job_name": "Nombre horario",
    "started_at": "Inicio",
    "finished_at": "Fin",
    "status": "Estado",
    "message": "Detalle",
}


def _rename_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve copia del df con columnas renombradas al español."""
    return df.rename(columns={k: v for k, v in COL_LABELS.items() if k in df.columns})


def _color_scale(val: float, col_min: float, col_max: float, reverse: bool = False) -> str:
    """Devuelve un color CSS interpolado sin depender de matplotlib."""
    try:
        val = float(val)
        if col_max == col_min:
            ratio = 0.5
        else:
            ratio = (val - col_min) / (col_max - col_min)
        ratio = max(0.0, min(1.0, ratio))
        if reverse:
            ratio = 1.0 - ratio
        # Verde bajo → amarillo → rojo alto
        if ratio < 0.5:
            r = int(255 * ratio * 2)
            g = 200
        else:
            r = 220
            g = int(200 * (1 - (ratio - 0.5) * 2))
        return f"background-color: rgb({r},{g},120); color: #1a1a1a"
    except Exception:
        return ""


def _style_price_table(df: pd.DataFrame) -> object:
    """Coloriza y formatea columnas de precio y porcentaje con CSS puro, sin matplotlib."""
    if df.size > 3_500_000:
        return df

    price_cols   = [c for c in df.columns if "Precio" in c and "$" in c]
    pct_cols     = [c for c in df.columns if "%" in c]
    cuotas_cols  = [c for c in df.columns if "Cuota" in c]

    fmt: dict[str, str] = {}
    for col in price_cols:
        fmt[col] = "${:,.0f}"
    for col in pct_cols:
        fmt[col] = "{:.1f}%"
    for col in cuotas_cols:
        fmt[col] = "{:.0f}"

    styled = df.style
    if fmt:
        styled = styled.format(fmt, na_rep="—")

    for col in price_cols:
        try:
            series = pd.to_numeric(df[col], errors="coerce")
            col_min, col_max = series.min(), series.max()
            styled = styled.map(
                lambda v, mn=col_min, mx=col_max: _color_scale(v, mn, mx, reverse=False),
                subset=[col],
            )
        except Exception:
            pass

    for col in pct_cols:
        try:
            series = pd.to_numeric(df[col], errors="coerce")
            col_min, col_max = series.min(), series.max()
            styled = styled.map(
                lambda v, mn=col_min, mx=col_max: _color_scale(v, mn, mx, reverse=True),
                subset=[col],
            )
        except Exception:
            pass

    return styled


# ── Descripciones de variables para el constructor de fórmulas ──────────────
VAR_DESCRIPTIONS: dict[str, str] = {
    "price":                   "Precio de venta actual ($)",
    "list_price":              "Precio de lista / precio original ($)",
    "cuotas_sin_interes_min":  "Mínimo de cuotas sin interés disponibles",
    "cuotas_sin_interes_max":  "Máximo de cuotas sin interés disponibles",
    "oncity":                  "Precio publicado en OnCity ($)",
    "fravega":                 "Precio publicado en Fravega ($)",
    "cetrogar":                "Precio publicado en Cetrogar ($)",
    "precio_min":              "Precio mínimo entre todas las empresas comparadas ($)",
    "precio_max":              "Precio máximo entre todas las empresas comparadas ($)",
    "brecha_abs":              "Diferencia absoluta entre precio máx y mín ($)",
    "brecha_pct":              "Diferencia porcentual entre precio máx y mín (%)",
    "oncity_pct_vs_min":       "Cuánto más caro es OnCity vs el mínimo del mercado (%)",
    "fravega_pct_vs_min":      "Cuánto más caro es Fravega vs el mínimo del mercado (%)",
    "cetrogar_pct_vs_min":     "Cuánto más caro es Cetrogar vs el mínimo del mercado (%)",
    "discount_pct":            "Porcentaje de descuento sobre precio de lista (%)",
    "stock_qty":               "Cantidad de unidades en stock",
    "empresas_con_precio":     "Cantidad de empresas que tienen precio para ese producto",
}


# ── Constructor de columnas calculadas ──────────────────────────────────────
def _render_custom_columns(df: pd.DataFrame, table_key: str) -> pd.DataFrame:
    """UI para agregar columnas calculadas. Variables clickeables con tooltip descriptivo."""
    state_key    = f"custom_cols_{table_key}"
    formula_key  = f"formula_draft_{table_key}"
    col_name_key = f"col_name_draft_{table_key}"

    if state_key not in st.session_state:
        st.session_state[state_key] = []
    if formula_key not in st.session_state:
        st.session_state[formula_key] = ""
    if col_name_key not in st.session_state:
        st.session_state[col_name_key] = ""

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    with st.expander("➕ Agregar columna calculada", expanded=False):
        st.caption(
            "Hacé clic en una variable para agregarla a la fórmula. "
            "Pasá el mouse encima para ver qué representa cada campo. "
            "Luego completá la fórmula manualmente con operadores: `+  -  *  /  **  ( )`"
        )

        # ── Variables clickeables ────────────────────────────────────────────
        if numeric_cols:
            st.markdown("**Variables disponibles — clic para insertar en la fórmula:**")
            cols_per_row = 5
            rows = [numeric_cols[i:i + cols_per_row] for i in range(0, len(numeric_cols), cols_per_row)]
            for row in rows:
                btn_cols = st.columns(len(row))
                for col_widget, var in zip(btn_cols, row):
                    desc = VAR_DESCRIPTIONS.get(var, COL_LABELS.get(var, var))
                    label = COL_LABELS.get(var, var)
                    with col_widget:
                        if st.button(
                            label,
                            key=f"varbtn_{table_key}_{var}",
                            help=f"**{label}**\n\n{desc}\n\nNombre en fórmula: `{var}`",
                            use_container_width=True,
                        ):
                            current = st.session_state[formula_key]
                            st.session_state[formula_key] = (current + " " + var).strip()
                            st.rerun()

        st.divider()

        # ── Inputs de nombre y fórmula ───────────────────────────────────────
        col_a, col_b = st.columns([1, 2])
        with col_a:
            new_col_name = st.text_input(
                "Nombre de la columna",
                value=st.session_state[col_name_key],
                key=f"col_name_input_{table_key}",
                placeholder="ej: ratio_precio",
            )
            st.session_state[col_name_key] = new_col_name
        with col_b:
            formula = st.text_input(
                "Fórmula",
                value=st.session_state[formula_key],
                key=f"formula_input_{table_key}",
                placeholder="ej: price / list_price * 100",
            )
            st.session_state[formula_key] = formula

        # Preview del resultado
        if formula.strip() and numeric_cols:
            try:
                preview = df.eval(formula).dropna()
                st.caption(
                    f"Vista previa — min: **{preview.min():,.2f}**  |  "
                    f"max: **{preview.max():,.2f}**  |  "
                    f"promedio: **{preview.mean():,.2f}**"
                )
            except Exception as ex:
                st.warning(f"Fórmula inválida aún: {ex}")

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])
        with btn_col1:
            if st.button("✅ Agregar columna", key=f"add_col_{table_key}"):
                if not new_col_name.strip():
                    st.error("Ingresá un nombre para la columna.")
                elif not formula.strip():
                    st.error("Ingresá una fórmula.")
                else:
                    try:
                        df.eval(formula)  # validar
                        st.session_state[state_key].append((new_col_name.strip(), formula.strip()))
                        st.session_state[formula_key] = ""
                        st.session_state[col_name_key] = ""
                        st.success(f"Columna '{new_col_name}' agregada.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error en la fórmula: {e}")
        with btn_col2:
            if st.button("🗑️ Limpiar fórmula", key=f"clear_formula_{table_key}"):
                st.session_state[formula_key] = ""
                st.rerun()
        with btn_col3:
            if st.session_state[state_key]:
                to_remove = st.multiselect(
                    "Quitar columnas calculadas",
                    [n for n, _ in st.session_state[state_key]],
                    key=f"remove_col_{table_key}",
                )
                if st.button("Eliminar seleccionadas", key=f"del_col_{table_key}"):
                    st.session_state[state_key] = [
                        (n, f) for n, f in st.session_state[state_key] if n not in to_remove
                    ]
                    st.rerun()

    # Aplicar columnas guardadas al df
    result_df = df.copy()
    for col_name, col_formula in st.session_state.get(state_key, []):
        try:
            result_df[col_name] = result_df.eval(col_formula)
        except Exception:
            pass
    return result_df


# ── Filtros inline de tabla ──────────────────────────────────────────────────
def _inline_table_filters(df: pd.DataFrame, key: str) -> pd.DataFrame:
    with st.expander("🔍 Filtros de tabla", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            cats = sorted(df["category"].dropna().unique().tolist()) if "category" in df.columns else []
            sel_cat = st.multiselect("Categoría", cats, key=f"tbl_cat_{key}")
        with fc2:
            comps = sorted(df["competencia"].dropna().unique().tolist()) if "competencia" in df.columns else []
            sel_comp = st.multiselect("Empresa", comps, key=f"tbl_comp_{key}")
        with fc3:
            q = st.text_input("Búsqueda", key=f"tbl_q_{key}", placeholder="Producto, EAN, marca...")

        fc4, fc5 = st.columns(2)
        with fc4:
            if "price" in df.columns:
                p_min = float(df["price"].min() or 0)
                p_max = float(df["price"].max() or 1)
                if p_max > p_min:
                    sel_price = st.slider("Precio", p_min, p_max, (p_min, p_max), key=f"tbl_price_{key}")
                else:
                    sel_price = (p_min, p_max)
            else:
                sel_price = None
        with fc5:
            if "cuotas_sin_interes_max" in df.columns:
                cuotas_opts = sorted(df["cuotas_sin_interes_max"].dropna().astype(int).unique().tolist())
                sel_cuotas = st.multiselect("Cuotas s/interés", cuotas_opts, key=f"tbl_cuotas_{key}")
            else:
                sel_cuotas = []

    f = df.copy()
    if sel_cat:
        f = f[f["category"].isin(sel_cat)]
    if sel_comp:
        f = f[f["competencia"].isin(sel_comp)]
    if sel_cuotas:
        f = f[f["cuotas_sin_interes_max"].fillna(0).astype(int).isin(sel_cuotas)]
    if sel_price and "price" in f.columns:
        f = f[f["price"].fillna(0).between(sel_price[0], sel_price[1])]
    if q:
        mask = pd.Series(False, index=f.index)
        for col in ["product_name", "ean", "brand", "category", "seller", "competencia"]:
            if col in f.columns:
                mask |= f[col].fillna("").astype(str).str.lower().str.contains(q.lower(), regex=False)
        f = f[mask]
    return f


@st.cache_resource
def _start_scheduler_thread():
    """Inicia el scheduler como hilo de fondo (ejecuta una sola vez por proceso)."""
    from scheduler_service import run_scheduler_loop
    thread = threading.Thread(target=run_scheduler_loop, daemon=True, name="price-scheduler")
    thread.start()
    return thread


def _find_latest_file(pattern: str) -> Path | None:
    files = sorted(BASE_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
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

    if "seller" not in df.columns:
        df["seller"] = ""

    if "ean" not in df.columns:
        df["ean"] = ""

    if "cuotas_sin_interes_max" not in df.columns:
        df["cuotas_sin_interes_max"] = None

    if "cuotas_sin_interes_min" not in df.columns:
        df["cuotas_sin_interes_min"] = None

    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

    if "list_price" in df.columns:
        df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce")

    df["ean"] = df["ean"].fillna("").astype(str)
    df["seller"] = df["seller"].fillna("").astype(str)
    df["category"] = df.get("category", "").fillna("").astype(str)
    df["product_name"] = df.get("product_name", "").fillna("").astype(str)

    return df


@st.cache_data(ttl=60)
def load_data() -> tuple[pd.DataFrame, dict]:
    oncity_file = _find_latest_file("oncity_precios*.xlsx")
    fravega_file = _find_latest_file("fravega_precios*.xlsx")
    cetrogar_file = _find_latest_file("cetrogar_precios*.xlsx")

    # Clave de invalidación: fecha de modificación de los archivos
    file_mtimes = tuple(
        int(f.stat().st_mtime) if f else 0
        for f in [oncity_file, fravega_file, cetrogar_file]
    )
    _ = file_mtimes  # hace que la cache se invalide cuando cambian los archivos

    oncity_df = _load_source(oncity_file, "oncity")
    fravega_df = _load_source(fravega_file, "fravega")
    cetrogar_df = _load_source(cetrogar_file, "cetrogar")

    all_df = pd.concat([oncity_df, fravega_df, cetrogar_df], ignore_index=True)

    metadata = {
        "oncity_file": str(oncity_file.name) if oncity_file else "No encontrado",
        "fravega_file": str(fravega_file.name) if fravega_file else "No encontrado",
        "cetrogar_file": str(cetrogar_file.name) if cetrogar_file else "No encontrado",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return all_df, metadata


def _save_layout(layout: list[dict]) -> None:
    with open(LAYOUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(layout, fh)


def _load_layout() -> list[dict]:
    if not LAYOUT_FILE.exists():
        return [
            {"i": "kpi_products", "x": 0, "y": 0, "w": 3, "h": 2},
            {"i": "kpi_ean", "x": 3, "y": 0, "w": 3, "h": 2},
            {"i": "kpi_price", "x": 6, "y": 0, "w": 3, "h": 2},
            {"i": "kpi_promo", "x": 9, "y": 0, "w": 3, "h": 2},
            {"i": "kpi_stock", "x": 0, "y": 2, "w": 3, "h": 2},
            {"i": "kpi_discount", "x": 3, "y": 2, "w": 3, "h": 2},
            {"i": "kpi_sellers", "x": 6, "y": 2, "w": 3, "h": 2},
            {"i": "kpi_price_median", "x": 9, "y": 2, "w": 3, "h": 2},
            {"i": "chart_comp", "x": 0, "y": 4, "w": 6, "h": 4},
            {"i": "chart_cat", "x": 6, "y": 4, "w": 6, "h": 4},
            {"i": "chart_price_hist", "x": 0, "y": 8, "w": 6, "h": 4},
            {"i": "chart_discount_comp", "x": 6, "y": 8, "w": 6, "h": 4},
        ]

    with open(LAYOUT_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _metric_card(title: str, value: str) -> None:
    st.markdown(
        f"""
        <div class='metric-card'>
            <div class='metric-label'>{title}</div>
            <div class='metric-value'>{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _filter_data(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filtros")

    query = st.sidebar.text_input("Lupa de busqueda", placeholder="Producto, SKU, EAN, marca...")

    competencias = sorted(df["competencia"].dropna().astype(str).unique().tolist())
    selected_comp = st.sidebar.multiselect("Competencia", competencias, default=competencias)

    categories = sorted(df["category"].dropna().astype(str).unique().tolist())
    selected_categories = st.sidebar.multiselect("Categoria", categories)

    sellers = sorted([s for s in df["seller"].dropna().astype(str).unique().tolist() if s])
    selected_sellers = st.sidebar.multiselect("Vendedor", sellers)

    cuotas_options = sorted(df["cuotas_sin_interes_max"].dropna().astype(int).unique().tolist())
    selected_cuotas = st.sidebar.multiselect("Cuotas sin interes max", cuotas_options)

    only_with_ean = st.sidebar.checkbox("Solo productos con EAN", value=False)

    estado_options = ["Todos", "Solo activos", "Solo inactivos"]
    selected_estado = st.sidebar.radio("Estado del producto", estado_options, index=0, horizontal=True)

    price_min = float(df["price"].min()) if df["price"].notna().any() else 0.0
    price_max = float(df["price"].max()) if df["price"].notna().any() else 0.0
    selected_price = st.sidebar.slider(
        "Rango de precio",
        min_value=float(price_min),
        max_value=float(price_max if price_max > price_min else price_min + 1.0),
        value=(float(price_min), float(price_max if price_max > price_min else price_min + 1.0)),
    )

    f = df.copy()
    if selected_comp:
        f = f[f["competencia"].isin(selected_comp)]

    if selected_categories:
        f = f[f["category"].isin(selected_categories)]

    if selected_sellers:
        f = f[f["seller"].isin(selected_sellers)]

    if selected_cuotas:
        f = f[f["cuotas_sin_interes_max"].fillna(0).astype(int).isin(selected_cuotas)]

    f = f[f["price"].fillna(0).between(selected_price[0], selected_price[1])]

    if only_with_ean:
        f = f[f["ean"].str.strip() != ""]

    if selected_estado != "Todos" and "available" in f.columns:
        activo_mask = f["available"].fillna(False).astype(bool)
        if selected_estado == "Solo activos":
            f = f[activo_mask]
        else:
            f = f[~activo_mask]

    if query:
        q = query.lower().strip()
        search_cols = ["product_name", "sku_id", "ean", "brand", "category", "seller"]
        mask = pd.Series(False, index=f.index)
        for col in search_cols:
            if col in f.columns:
                mask = mask | f[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        f = f[mask]

    return f


def _render_dashboard(filtered_df: pd.DataFrame) -> None:
    st.subheader("Dashboard moderno (widgets drag and drop)")
    st.caption("Arrastra los widgets como en un celular. El layout se guarda automaticamente.")

    widget_catalog = {
        "kpi_products": "KPI Total productos",
        "kpi_ean": "KPI Cobertura EAN",
        "kpi_price": "KPI Precio promedio",
        "kpi_promo": "KPI Cuotas sin interes",
        "kpi_stock": "KPI Cobertura de stock",
        "kpi_discount": "KPI Descuento promedio",
        "kpi_sellers": "KPI Sellers únicos",
        "kpi_price_median": "KPI Precio mediano",
        "chart_comp": "Grafico por competidor",
        "chart_cat": "Mapa de categorias",
        "chart_price_hist": "Histograma de precios",
        "chart_discount_comp": "Descuento por competidor",
    }

    if "active_widgets" not in st.session_state:
        st.session_state["active_widgets"] = list(widget_catalog.keys())

    active_widgets = st.multiselect(
        "Widgets activos",
        options=list(widget_catalog.keys()),
        default=st.session_state["active_widgets"],
        format_func=lambda x: widget_catalog[x],
    )
    st.session_state["active_widgets"] = active_widgets

    total_products = int(filtered_df["product_id"].nunique()) if "product_id" in filtered_df.columns else len(filtered_df)
    ean_cov = 0.0
    if len(filtered_df):
        ean_cov = (filtered_df["ean"].str.strip() != "").mean() * 100

    avg_price = filtered_df["price"].mean() if filtered_df["price"].notna().any() else 0
    median_price = filtered_df["price"].median() if filtered_df["price"].notna().any() else 0
    promo_cov = (
        (filtered_df["cuotas_sin_interes_max"].fillna(0).astype(int) > 0).mean() * 100
        if len(filtered_df)
        else 0
    )
    stock_cov = (
        (filtered_df.get("available", pd.Series(dtype=bool)).fillna(False).astype(bool)).mean() * 100
        if len(filtered_df) and "available" in filtered_df.columns
        else 0
    )

    sellers_count = int(filtered_df["seller"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    discount_series = pd.Series(dtype=float)
    if "discount_pct" in filtered_df.columns:
        discount_series = pd.to_numeric(filtered_df["discount_pct"], errors="coerce")
    elif "list_price" in filtered_df.columns and "price" in filtered_df.columns:
        base = filtered_df.copy()
        list_price = pd.to_numeric(base["list_price"], errors="coerce")
        price = pd.to_numeric(base["price"], errors="coerce")
        discount_series = ((list_price - price) / list_price.replace(0, pd.NA)) * 100

    avg_discount = float(discount_series.dropna().mean()) if discount_series.dropna().any() else 0.0

    comp_group = (
        filtered_df.groupby("competencia", dropna=False)
        .size()
        .reset_index(name="productos")
        .sort_values("productos", ascending=False)
    )

    cat_group = (
        filtered_df.groupby("category", dropna=False)
        .size()
        .reset_index(name="productos")
        .sort_values("productos", ascending=False)
        .head(12)
    )

    fig_comp = px.bar(
        comp_group,
        x="competencia",
        y="productos",
        color="competencia",
        color_discrete_sequence=["#2563eb", "#0f766e", "#dc2626"],
        title="Volumen por competidor",
    )

    fig_cat = px.treemap(
        cat_group,
        path=["category"],
        values="productos",
        color="productos",
        color_continuous_scale="YlOrRd",
        title="Top categorias",
    )

    fig_price_hist = px.histogram(
        filtered_df[filtered_df["price"].notna()],
        x="price",
        nbins=30,
        color="competencia",
        barmode="overlay",
        opacity=0.65,
        title="Distribución de precios",
    )

    discount_comp_df = pd.DataFrame()
    if not discount_series.empty:
        discount_comp_df = filtered_df[["competencia"]].copy()
        discount_comp_df["discount_pct_calc"] = discount_series
        discount_comp_df = (
            discount_comp_df.dropna(subset=["discount_pct_calc"])
            .groupby("competencia", as_index=False)["discount_pct_calc"]
            .mean()
            .sort_values("discount_pct_calc", ascending=False)
        )

    fig_discount_comp = px.bar(
        discount_comp_df,
        x="competencia",
        y="discount_pct_calc",
        color="competencia",
        color_discrete_sequence=["#f97316", "#0284c7", "#16a34a"],
        title="Descuento promedio por competidor",
        labels={"discount_pct_calc": "Descuento %"},
    )

    if "dash_layout" not in st.session_state:
        st.session_state["dash_layout"] = _load_layout()

    with elements("dashboard_widgets"):
        with dashboard.Grid(
            st.session_state["dash_layout"],
            onLayoutChange=sync("dash_layout"),
            cols={"lg": 12, "md": 10, "sm": 6, "xs": 4, "xxs": 2},
            rowHeight=60,
            draggableHandle=".drag-handle",
        ):
            if "kpi_products" in active_widgets:
                with mui.Paper(key="kpi_products", sx={"p": 2}):
                    mui.Typography("Total productos", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{total_products:,}", variant="h4")

            if "kpi_ean" in active_widgets:
                with mui.Paper(key="kpi_ean", sx={"p": 2}):
                    mui.Typography("Cobertura EAN", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{ean_cov:.1f}%", variant="h4")

            if "kpi_price" in active_widgets:
                with mui.Paper(key="kpi_price", sx={"p": 2}):
                    mui.Typography("Precio promedio", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"${avg_price:,.0f}", variant="h4")

            if "kpi_promo" in active_widgets:
                with mui.Paper(key="kpi_promo", sx={"p": 2}):
                    mui.Typography("Con cuotas sin interes", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{promo_cov:.1f}%", variant="h4")

            if "kpi_stock" in active_widgets:
                with mui.Paper(key="kpi_stock", sx={"p": 2}):
                    mui.Typography("Cobertura de stock", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{stock_cov:.1f}%", variant="h4")

            if "kpi_discount" in active_widgets:
                with mui.Paper(key="kpi_discount", sx={"p": 2}):
                    mui.Typography("Descuento promedio", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{avg_discount:.1f}%", variant="h4")

            if "kpi_sellers" in active_widgets:
                with mui.Paper(key="kpi_sellers", sx={"p": 2}):
                    mui.Typography("Sellers únicos", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"{sellers_count:,}", variant="h4")

            if "kpi_price_median" in active_widgets:
                with mui.Paper(key="kpi_price_median", sx={"p": 2}):
                    mui.Typography("Precio mediano", className="drag-handle", sx={"fontWeight": 700})
                    mui.Typography(f"${median_price:,.0f}", variant="h4")

            if "chart_comp" in active_widgets:
                with mui.Paper(key="chart_comp", sx={"p": 1}):
                    mui.Typography("Productos por competidor", className="drag-handle", sx={"fontWeight": 700})
                    st.plotly_chart(fig_comp, use_container_width=True, key="chart_comp_plot")

            if "chart_cat" in active_widgets:
                with mui.Paper(key="chart_cat", sx={"p": 1}):
                    mui.Typography("Mix por categoria", className="drag-handle", sx={"fontWeight": 700})
                    st.plotly_chart(fig_cat, use_container_width=True, key="chart_cat_plot")

            if "chart_price_hist" in active_widgets:
                with mui.Paper(key="chart_price_hist", sx={"p": 1}):
                    mui.Typography("Distribución de precios", className="drag-handle", sx={"fontWeight": 700})
                    st.plotly_chart(fig_price_hist, use_container_width=True, key="chart_price_hist_plot")

            if "chart_discount_comp" in active_widgets:
                with mui.Paper(key="chart_discount_comp", sx={"p": 1}):
                    mui.Typography("Descuento por competidor", className="drag-handle", sx={"fontWeight": 700})
                    st.plotly_chart(fig_discount_comp, use_container_width=True, key="chart_discount_comp_plot")

    if st.session_state.get("dash_layout"):
        _save_layout(st.session_state["dash_layout"])

    st.markdown("<div class='widget-help'>Tip: tambien puedes filtrar arriba y el dashboard se recalcula en vivo.</div>", unsafe_allow_html=True)


def _render_table_and_exports(filtered_df: pd.DataFrame) -> None:
    st.subheader("Listado de productos")

    display_cols = [
        c
        for c in [
            "competencia",
            "product_name",
            "brand",
            "category",
            "seller",
            "ean",
            "price",
            "list_price",
            "cuotas_sin_interes_min",
            "cuotas_sin_interes_max",
            "mejor_promo",
            "available",
            "stock_qty",
            "sku_id",
            "url",
        ]
        if c in filtered_df.columns
    ]

    work_df = filtered_df[display_cols].copy()
    # Cuotas como entero
    for c in ["cuotas_sin_interes_min", "cuotas_sin_interes_max"]:
        if c in work_df.columns:
            work_df[c] = pd.to_numeric(work_df[c], errors="coerce").fillna(0).astype(int)

    work_df = _inline_table_filters(work_df, "productos")
    work_df = _render_custom_columns(work_df, "productos")
    show_df = _rename_cols(work_df)
    styled = _style_price_table(show_df)
    st.dataframe(styled, use_container_width=True, height=540)

    csv_buffer = io.StringIO()
    filtered_df[display_cols].to_csv(csv_buffer, index=False)
    st.download_button(
        "Descargar tabla filtrada (.csv)",
        data=csv_buffer.getvalue().encode("utf-8"),
        file_name=f"productos_filtrados_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    st.subheader("Reporte imprimible")
    kpi_html = f"""
    <html>
    <head><title>Reporte KPI</title></head>
    <body style='font-family: Arial, sans-serif; padding: 24px;'>
    <h1>Reporte de indicadores filtrados</h1>
      <p>Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
      <ul>
        <li>Filas filtradas: {len(filtered_df):,}</li>
        <li>Productos unicos: {filtered_df['product_id'].nunique() if 'product_id' in filtered_df.columns else len(filtered_df):,}</li>
        <li>EAN con valor: {(filtered_df['ean'].astype(str).str.strip() != '').sum() if 'ean' in filtered_df.columns else 0:,}</li>
        <li>Precio promedio: ${filtered_df['price'].mean() if 'price' in filtered_df.columns and filtered_df['price'].notna().any() else 0:,.0f}</li>
      </ul>
    <p>Usa Ctrl/Cmd+P para imprimir este reporte.</p>
    </body>
    </html>
    """

    st.download_button(
        "Descargar reporte imprimible (.html)",
        data=kpi_html.encode("utf-8"),
        file_name=f"reporte_kpi_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
        mime="text/html",
    )


def _render_tops(filtered_df: pd.DataFrame) -> None:
    st.subheader("Top productos")

    c1, c2 = st.columns(2)

    top_promo = filtered_df.copy()
    if "cuotas_sin_interes_max" in top_promo.columns:
        top_promo["cuotas_sin_interes_max"] = pd.to_numeric(top_promo["cuotas_sin_interes_max"], errors="coerce").fillna(0).astype(int)
        top_promo = top_promo.sort_values(["cuotas_sin_interes_max", "price"], ascending=[False, True]).head(20)

    top_price = filtered_df.copy()
    if "price" in top_price.columns:
        top_price = top_price.sort_values("price", ascending=False).head(20)

    promo_cols = [c for c in ["competencia", "product_name", "ean", "category", "seller", "cuotas_sin_interes_max", "price"] if c in top_promo.columns]
    price_cols_top = [c for c in ["competencia", "product_name", "ean", "category", "seller", "price", "list_price"] if c in top_price.columns]

    with c1:
        st.markdown("**Más cuotas sin interés**")
        top_promo_work = _inline_table_filters(top_promo[promo_cols], "top_promo")
        top_promo_work = _render_custom_columns(top_promo_work, "top_promo")
        st.dataframe(_style_price_table(_rename_cols(top_promo_work)), use_container_width=True, height=420)

    with c2:
        st.markdown("**Precio más alto por categoría**")
        top_price_work = _inline_table_filters(top_price[price_cols_top], "top_price")
        top_price_work = _render_custom_columns(top_price_work, "top_price")
        st.dataframe(_style_price_table(_rename_cols(top_price_work)), use_container_width=True, height=420)


def _build_price_comparison(filtered_df: pd.DataFrame) -> pd.DataFrame:
    base = filtered_df.copy()
    if base.empty:
        return pd.DataFrame()

    base["ean"] = base["ean"].fillna("").astype(str).str.strip()
    base["price"] = pd.to_numeric(base["price"], errors="coerce")

    base = base[(base["ean"] != "") & (base["price"].notna())]
    if base.empty:
        return pd.DataFrame()

    # Metadata por EAN (best effort)
    meta = (
        base.sort_values(["ean", "product_name"])
        .groupby("ean", as_index=False)
        .agg(
            product_name=("product_name", "first"),
            brand=("brand", "first"),
            category=("category", "first"),
        )
    )

    # Precio más bajo por competidor para cada EAN
    prices = base.groupby(["ean", "competencia"], as_index=False)["price"].min()
    pivot = prices.pivot(index="ean", columns="competencia", values="price").reset_index()

    comparison = meta.merge(pivot, on="ean", how="left")

    company_cols = [c for c in ["oncity", "fravega", "cetrogar"] if c in comparison.columns]
    if not company_cols:
        return pd.DataFrame()

    comparison["empresas_con_precio"] = comparison[company_cols].notna().sum(axis=1)
    comparison = comparison[comparison["empresas_con_precio"] >= 2].copy()
    if comparison.empty:
        return pd.DataFrame()

    comparison["precio_min"] = comparison[company_cols].min(axis=1)
    comparison["precio_max"] = comparison[company_cols].max(axis=1)
    comparison["brecha_abs"] = comparison["precio_max"] - comparison["precio_min"]
    comparison["brecha_pct"] = ((comparison["precio_max"] / comparison["precio_min"]) - 1) * 100

    for col in company_cols:
        comparison[f"{col}_pct_vs_min"] = ((comparison[col] / comparison["precio_min"]) - 1) * 100

    comparison["mejor_precio_empresa"] = comparison[company_cols].idxmin(axis=1)

    return comparison.sort_values(["empresas_con_precio", "brecha_pct"], ascending=[False, False])


def _render_price_comparison(filtered_df: pd.DataFrame) -> None:
    st.subheader("Comparativa de precios entre empresas")

    with st.expander("Cómo se calcula esta comparativa", expanded=False):
        st.markdown("""
        **Metodología:**
        - La comparación se realiza **exclusivamente por EAN** (código de barras del producto),
          garantizando que se compare exactamente el mismo artículo entre las empresas.
        - Solo se incluyen productos que aparezcan en **al menos 2 empresas** con precio disponible.
        - Para cada empresa se toma el **precio más bajo** publicado para ese EAN.
        - **Diferencia (%)** = ((Precio Máximo − Precio Mínimo) / Precio Mínimo) × 100
        - **% vs Mínimo** = cuánto más caro es ese precio respecto al más barato del mercado (0% = el más barato).
        - **Empresa más barata** = la que tiene el precio mínimo para ese EAN.
        """)

    comparison = _build_price_comparison(filtered_df)
    if comparison.empty:
        st.info("No hay productos comparables: se necesita EAN y precio en al menos 2 empresas. Asegurate de haber corrido los scrapers o de no tener un filtro demasiado restrictivo.")
        return

    company_cols = [c for c in ["oncity", "fravega", "cetrogar"] if c in comparison.columns]

    # ── KPIs ──────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Productos comparables", f"{len(comparison):,}")
    with c2:
        st.metric("Brecha promedio", f"{comparison['brecha_pct'].mean():.1f}%")
    with c3:
        st.metric("Brecha máxima", f"{comparison['brecha_pct'].max():.1f}%")
    with c4:
        winner = comparison["mejor_precio_empresa"].value_counts().idxmax() if len(comparison) else "-"
        st.metric("Empresa más competitiva", winner.capitalize())

    # ── Ganador global (pie) ──────────────────────────────────────────────────
    winner_share = (
        comparison["mejor_precio_empresa"].value_counts(normalize=True).rename_axis("empresa").reset_index(name="share")
    )
    winner_share["share_pct"] = winner_share["share"] * 100
    fig_winners = px.pie(
        winner_share,
        names="empresa",
        values="share_pct",
        title="Quién tiene el precio más bajo — participación global",
        hole=0.45,
    )
    st.plotly_chart(fig_winners, use_container_width=True)

    # ── Ganador por categoría ─────────────────────────────────────────────────
    st.markdown("### Ganador por categoría")
    st.caption("Por cada categoría: empresa que tiene el precio mínimo en la mayor cantidad de productos comparados.")

    cat_winner = (
        comparison.groupby(["category", "mejor_precio_empresa"])
        .size()
        .reset_index(name="victorias")
    )
    cat_winner_top = (
        cat_winner.sort_values("victorias", ascending=False)
        .groupby("category", as_index=False)
        .first()
        .sort_values("victorias", ascending=False)
    )
    cat_winner_top = cat_winner_top.rename(columns={
        "category": "Categoría",
        "mejor_precio_empresa": "Empresa ganadora",
        "victorias": "Productos con precio más bajo",
    })

    fig_cat_winners = px.bar(
        cat_winner_top.head(20),
        x="Productos con precio más bajo",
        y="Categoría",
        color="Empresa ganadora",
        orientation="h",
        title="Top 20 categorías: empresa con mejor precio",
        height=540,
    )
    st.plotly_chart(fig_cat_winners, use_container_width=True)
    st.dataframe(cat_winner_top, use_container_width=True, height=320)

    # ── Tabla comparativa detallada ───────────────────────────────────────────
    st.markdown("### Tabla detallada por producto")
    display_cols = [
        c
        for c in [
            "ean",
            "product_name",
            "brand",
            "category",
            "oncity",
            "fravega",
            "cetrogar",
            "oncity_pct_vs_min",
            "fravega_pct_vs_min",
            "cetrogar_pct_vs_min",
            "precio_min",
            "precio_max",
            "brecha_abs",
            "brecha_pct",
            "mejor_precio_empresa",
        ]
        if c in comparison.columns
    ]

    styled_comp = _style_price_table(_rename_cols(comparison[display_cols]))
    st.dataframe(styled_comp, use_container_width=True, height=460)

    csv_buffer = io.StringIO()
    comparison[display_cols].to_csv(csv_buffer, index=False)
    st.download_button(
        "Descargar comparativa (.csv)",
        data=csv_buffer.getvalue().encode("utf-8"),
        file_name=f"comparativa_productos_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    # ── Detalle individual ────────────────────────────────────────────────────
    st.markdown("### Detalle de un producto")
    options = comparison.head(500).copy()
    options["label"] = options["product_name"].astype(str) + "  |  EAN: " + options["ean"].astype(str)
    selected_label = st.selectbox("Seleccioná un producto", options["label"].tolist())
    selected_row = options.loc[options["label"] == selected_label].iloc[0]

    comp_df = pd.DataFrame(
        {
            "Empresa": [c.capitalize() for c in company_cols],
            "Precio ($)": [selected_row.get(c) for c in company_cols],
            "% vs Mínimo": [selected_row.get(f"{c}_pct_vs_min") for c in company_cols],
        }
    ).dropna(subset=["Precio ($)"])

    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.dataframe(comp_df, use_container_width=True)
    with col_b:
        fig_product = px.bar(
            comp_df,
            x="Empresa",
            y="Precio ($)",
            color="Empresa",
            title=f"Precios para: {selected_row['product_name'][:60]}",
            text="Precio ($)",
        )
        fig_product.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
        st.plotly_chart(fig_product, use_container_width=True)


def _render_cron_manager() -> None:
    st.subheader("Configurador de horarios automáticos")
    st.caption("Administra los horarios de ejecución automática desde la web: crear, editar, activar, desactivar y eliminar.")

    seed_default_jobs()
    jobs = list_jobs()
    logs = latest_logs(30)

    enabled_jobs = [j for j in jobs if bool(j.get("enabled"))]
    status_label = "Estado desconocido"
    status_type = "warning"
    status_detail = "No hay señales suficientes para inferir estado."

    if not enabled_jobs:
        status_label = "Detenido"
        status_type = "error"
        status_detail = "No hay horarios activos. Activa al menos uno para programar ejecuciones."
    elif logs:
        last_log = logs[0]
        last_status = str(last_log.get("status", "")).lower()
        started_at_raw = last_log.get("started_at")
        finished_at_raw = last_log.get("finished_at")
        started_at = None
        if started_at_raw:
            try:
                started_at = datetime.fromisoformat(str(started_at_raw))
            except ValueError:
                started_at = None

        recent_window = datetime.now() - timedelta(hours=24)
        is_recent = bool(started_at and started_at >= recent_window)

        if last_status == "running" and is_recent:
            status_label = "Ejecutando ahora"
            status_type = "success"
            status_detail = f"Los scrapers están corriendo en este momento (iniciado: {started_at_raw}). Los datos se actualizarán al terminar."
        elif last_status == "ok" and is_recent:
            status_label = "Operativo"
            status_type = "success"
            status_detail = f"Última ejecución exitosa: {finished_at_raw or started_at_raw}."
        elif last_status == "error" and is_recent:
            status_label = "Con alertas"
            status_type = "error"
            status_detail = "La última ejecución falló. Revisá los logs para ver el detalle del error."
        elif is_recent:
            status_label = "En espera"
            status_type = "warning"
            status_detail = f"Última actividad: {started_at_raw}. Esperando próxima ejecución programada."
        else:
            status_label = "Sin ejecuciones recientes"
            status_type = "warning"
            status_detail = "No hubo ejecuciones en las últimas 24 horas. Verificá que el servidor esté activo."
    else:
        status_label = "En espera"
        status_type = "warning"
        status_detail = "Hay horarios activos pero todavía no se registraron ejecuciones."

    st.markdown("### Estado del scheduler")
    if status_type == "success":
        st.success(f"{status_label}: {status_detail}")
    elif status_type == "error":
        st.error(f"{status_label}: {status_detail}")
    else:
        st.warning(f"{status_label}: {status_detail}")

    st.markdown("### Crear nuevo horario")
    with st.form("new_cron_form", clear_on_submit=True):
        name = st.text_input("Nombre", value="Nuevo horario")
        hour = st.number_input("Hora", min_value=0, max_value=23, value=9)
        minute = st.number_input("Minuto", min_value=0, max_value=59, value=0)
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            run_oncity = st.checkbox("OnCity", value=True)
        with col_b:
            run_fravega = st.checkbox("Fravega", value=True)
        with col_c:
            run_cetrogar = st.checkbox("Cetrogar", value=True)
        enabled = st.checkbox("Activo", value=True)
        submitted = st.form_submit_button("Agregar horario")
        if submitted:
            if not (run_oncity or run_fravega or run_cetrogar):
                st.error("Debes seleccionar al menos una competencia.")
            else:
                add_job(name, int(hour), int(minute), run_oncity, run_fravega, run_cetrogar, enabled)
                st.success("Horario creado.")
                st.rerun()

    st.markdown("### Horarios actuales")
    for job in jobs:
        with st.expander(
            f"#{job['id']} - {job['name']} ({int(job['hour']):02d}:{int(job['minute']):02d})",
            expanded=False,
        ):
            col1, col2 = st.columns([2, 1])
            with col1:
                with st.form(f"edit_job_{job['id']}"):
                    name = st.text_input("Nombre", value=job["name"], key=f"name_{job['id']}")
                    hour = st.number_input("Hora", min_value=0, max_value=23, value=int(job["hour"]), key=f"hour_{job['id']}")
                    minute = st.number_input(
                        "Minuto", min_value=0, max_value=59, value=int(job["minute"]), key=f"min_{job['id']}"
                    )
                    run_oncity = st.checkbox("OnCity", value=bool(job["run_oncity"]), key=f"oncity_{job['id']}")
                    run_fravega = st.checkbox("Fravega", value=bool(job["run_fravega"]), key=f"fravega_{job['id']}")
                    run_cetrogar = st.checkbox("Cetrogar", value=bool(job["run_cetrogar"]), key=f"cetrogar_{job['id']}")
                    enabled = st.checkbox("Activo", value=bool(job["enabled"]), key=f"enabled_{job['id']}")
                    save = st.form_submit_button("Guardar cambios")
                    if save:
                        update_job(
                            int(job["id"]),
                            name,
                            int(hour),
                            int(minute),
                            run_oncity,
                            run_fravega,
                            run_cetrogar,
                            enabled,
                        )
                        st.success("Cambios guardados.")
                        st.rerun()

            with col2:
                st.write(f"Ultima corrida: {job['last_run'] or 'Nunca'}")
                toggle_label = "Desactivar" if bool(job["enabled"]) else "Activar"
                if st.button(toggle_label, key=f"toggle_{job['id']}"):
                    set_job_enabled(int(job["id"]), not bool(job["enabled"]))
                    st.rerun()
                if st.button("Eliminar", key=f"delete_{job['id']}"):
                    delete_job(int(job["id"]))
                    st.warning("Horario eliminado.")
                    st.rerun()

    st.markdown("### Logs recientes")
    if logs:
        logs_df = _rename_cols(pd.DataFrame(logs).drop(columns=["id", "job_id"], errors="ignore"))
        st.dataframe(logs_df, use_container_width=True, height=360)
    else:
        st.info("Aun no hay ejecuciones registradas.")

    st.caption("Las ejecuciones se procesan automáticamente en el entorno desplegado según la configuración de horarios.")


def _render_intelligence_tab(filtered_df: pd.DataFrame) -> None:
    st.subheader("Inteligencia de precios")
    st.caption(
        "Análisis derivado de los datos cargados: brechas entre competidores, marcas más competitivas, "
        "evolución de precios y alertas automáticas. Los valores se calculan en tiempo real sobre los archivos más recientes."
    )

    # ── 1. Brechas actuales ──────────────────────────────────────────────────
    st.markdown("### 📊 Productos con mayor brecha de precio entre competidores")
    st.caption(
        "Productos que tienen precio en al menos 2 empresas. La **brecha %** es la diferencia porcentual "
        "entre el precio más caro y el más barato para el mismo EAN. "
        "Una brecha alta indica una oportunidad de posicionamiento competitivo."
    )

    if "ean" not in filtered_df.columns or filtered_df.empty:
        st.info("No hay datos disponibles con el filtro actual.")
    else:
        df_ean = filtered_df[filtered_df["ean"].str.strip() != ""].copy()
        df_ean["price"] = pd.to_numeric(df_ean["price"], errors="coerce")
        df_ean = df_ean[df_ean["price"].notna()]

        if df_ean.empty:
            st.info("No hay productos con EAN y precio en el filtro actual.")
        else:
            prices = df_ean.groupby(["ean", "competencia"], as_index=False)["price"].min()
            pivot = prices.pivot(index="ean", columns="competencia", values="price").reset_index()

            meta = (
                df_ean.sort_values(["ean", "product_name"])
                .groupby("ean", as_index=False)
                .agg(
                    product_name=("product_name", "first"),
                    brand=("brand", "first"),
                    category=("category", "first"),
                )
            )
            comp = meta.merge(pivot, on="ean", how="left")
            company_cols = [c for c in ["oncity", "fravega", "cetrogar"] if c in comp.columns]

            if company_cols:
                comp["empresas_con_precio"] = comp[company_cols].notna().sum(axis=1)
                comp = comp[comp["empresas_con_precio"] >= 2].copy()

            if comp.empty or not company_cols:
                st.info("No hay productos con precio en 2 o más empresas para comparar.")
            else:
                comp["precio_min"] = comp[company_cols].min(axis=1)
                comp["precio_max"] = comp[company_cols].max(axis=1)
                comp["brecha_pct"] = ((comp["precio_max"] / comp["precio_min"]) - 1) * 100
                comp["empresa_mas_barata"] = comp[company_cols].idxmin(axis=1)
                comp["empresa_mas_cara"] = comp[company_cols].idxmax(axis=1)
                comp = comp.sort_values("brecha_pct", ascending=False)

                # KPIs de brecha
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Productos comparables", f"{len(comp):,}", help="Con EAN en ≥2 empresas")
                k2.metric("Brecha promedio", f"{comp['brecha_pct'].mean():.1f}%", help="Diferencia % precio máx vs mín")
                k3.metric("Brecha máxima", f"{comp['brecha_pct'].max():.1f}%", help="Producto con mayor diferencia de precio")
                k4.metric("Con brecha > 25%", f"{(comp['brecha_pct'] > 25).sum():,}", help="Productos donde la diferencia supera 25%")

                threshold = st.slider(
                    "Mostrar solo productos con brecha mayor a (%)",
                    min_value=0, max_value=200, value=0, step=5,
                    help="Filtrá para ver solo los productos más dispares entre empresas"
                )
                show_comp = comp[comp["brecha_pct"] >= threshold].copy()

                display_cols = ["product_name", "brand", "category", "empresa_mas_barata", "empresa_mas_cara", "precio_min", "precio_max", "brecha_pct"] + company_cols
                display_cols = [c for c in display_cols if c in show_comp.columns]
                label_map = {
                    "product_name": "Producto", "brand": "Marca", "category": "Categoría",
                    "empresa_mas_barata": "Más barata", "empresa_mas_cara": "Más cara",
                    "precio_min": "Precio mín ($)", "precio_max": "Precio máx ($)",
                    "brecha_pct": "Brecha (%)",
                    "oncity": "OnCity ($)", "fravega": "Fravega ($)", "cetrogar": "Cetrogar ($)",
                }
                show_df = show_comp[display_cols].rename(columns=label_map)
                st.dataframe(
                    show_df.style.format({
                        "Precio mín ($)": "${:,.0f}", "Precio máx ($)": "${:,.0f}",
                        "Brecha (%)": "{:.1f}%",
                        **{v: "${:,.0f}" for k, v in label_map.items() if k in ["oncity", "fravega", "cetrogar"]},
                    }, na_rep="—"),
                    use_container_width=True,
                    height=400,
                )

                st.download_button(
                    "Descargar brechas (.csv)",
                    data=show_comp[display_cols].rename(columns=label_map).to_csv(index=False).encode(),
                    file_name=f"brechas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                )

    st.divider()

    # ── 2. Competitividad por marca ──────────────────────────────────────────
    st.markdown("### 🏆 Marcas más competitivas por empresa")
    st.caption(
        "Para cada producto con EAN en 2 o más empresas, se identifica qué empresa tiene el precio más bajo. "
        "Esta sección muestra cuántas veces cada marca aparece como la más barata en cada empresa. "
        "**Una marca con muchas 'victorias' en una empresa indica que esa empresa domina ese segmento.**"
    )

    if "ean" in filtered_df.columns and not filtered_df.empty and 'comp' in dir() and not comp.empty:
        brand_wins = []
        for _, row in comp.iterrows():
            if pd.notna(row.get("empresa_mas_barata")) and str(row.get("brand", "")).strip():
                brand_wins.append({
                    "marca": str(row["brand"]).strip(),
                    "empresa": str(row["empresa_mas_barata"]),
                })

        if brand_wins:
            bw_df = pd.DataFrame(brand_wins)
            bw_agg = bw_df.groupby(["marca", "empresa"]).size().reset_index(name="victorias")
            bw_agg = bw_agg.sort_values("victorias", ascending=False)

            top_n = st.slider("Top N marcas", min_value=5, max_value=50, value=20, step=5, key="brand_top_n")
            top_brands = bw_agg.groupby("marca")["victorias"].sum().nlargest(top_n).index
            bw_plot = bw_agg[bw_agg["marca"].isin(top_brands)]

            fig_brand = px.bar(
                bw_plot,
                x="victorias",
                y="marca",
                color="empresa",
                orientation="h",
                title=f"Top {top_n} marcas con más productos al menor precio",
                color_discrete_map={"oncity": "#2563eb", "fravega": "#0f766e", "cetrogar": "#dc2626"},
                labels={"victorias": "Productos al precio más bajo", "marca": "Marca", "empresa": "Empresa"},
                height=max(400, top_n * 22),
            )
            fig_brand.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_brand, use_container_width=True)
        else:
            st.info("No hay marcas con suficiente comparación para mostrar el ranking.")
    else:
        st.info("Calculá primero las brechas desplazándote hacia arriba o ajustá los filtros para incluir más empresas.")

    st.divider()

    # ── 3. Historial de precios ──────────────────────────────────────────────
    st.markdown("### 📈 Evolución de precio por producto (EAN)")
    st.caption(
        "Muestra cómo cambió el precio de un producto específico a lo largo del tiempo, en cada empresa. "
        "Requiere que se hayan ejecutado **al menos 2 corridas** del scraper para tener puntos de comparación. "
        "Buscá por EAN o nombre de producto."
    )

    ean_options = []
    ean_label_map = {}
    if "ean" in filtered_df.columns:
        ean_raw = filtered_df[filtered_df["ean"].str.strip() != ""][["ean", "product_name"]].drop_duplicates("ean").head(500)
        for _, row in ean_raw.iterrows():
            label = f"{row['ean']}  —  {str(row.get('product_name', ''))[:60]}"
            ean_options.append(label)
            ean_label_map[label] = str(row["ean"])

    if not ean_options:
        st.info("No hay productos con EAN en el filtro actual.")
    else:
        selected_label = st.selectbox("Seleccioná producto", ean_options, key="hist_ean_select")
        selected_ean = ean_label_map.get(selected_label, "")
        hist_df = get_price_history(selected_ean)

        if hist_df.empty:
            st.info(
                "No hay historial de precios guardado para este producto. "
                "El historial se genera automáticamente cuando se ejecutan corridas desde la pestaña **Horarios**, "
                "o manualmente con el botón de abajo."
            )
            if st.button("Generar snapshot ahora (guarda el estado actual para historial futuro)", use_container_width=True):
                info = process_post_run(BASE_DIR, datetime.now().isoformat(timespec="seconds"))
                st.success(f"Snapshot guardado: {info.get('snapshots', 0)} productos registrados.")
                st.rerun()
        else:
            hist_df["run_at"] = pd.to_datetime(hist_df["run_at"], errors="coerce")
            fig_hist = px.line(
                hist_df,
                x="run_at",
                y="price",
                color="competencia",
                markers=True,
                color_discrete_map={"oncity": "#2563eb", "fravega": "#0f766e", "cetrogar": "#dc2626"},
                labels={"run_at": "Fecha de corrida", "price": "Precio ($)", "competencia": "Empresa"},
                title=f"Evolución de precio — {selected_label[:80]}",
            )
            fig_hist.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f")
            st.plotly_chart(fig_hist, use_container_width=True)

            hist_show = hist_df[["run_at", "competencia", "price", "product_name"]].copy()
            hist_show.columns = ["Fecha", "Empresa", "Precio ($)", "Producto"]
            hist_show["Precio ($)"] = hist_show["Precio ($)"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "—")
            st.dataframe(hist_show, use_container_width=True, height=220)

    st.divider()

    # ── 4. Alertas históricas ────────────────────────────────────────────────
    st.markdown("### 🔔 Alertas de brecha registradas")
    st.caption(
        "Alertas generadas automáticamente en corridas anteriores cuando la brecha de precio entre empresas "
        "superó el umbral configurado. Sirven para ver qué productos tuvieron disparidades grandes en el pasado "
        "y monitorear si se mantienen o corrigen."
    )

    alerts_df = get_alerts(300)
    if alerts_df.empty:
        st.info(
            "Aún no hay alertas registradas. Se generan automáticamente en cada corrida del scraper "
            "cuando la brecha supera el umbral definido en los horarios automáticos."
        )
    else:
        alerts_df["run_at"] = pd.to_datetime(alerts_df["run_at"], errors="coerce")
        alerts_show = alerts_df.rename(columns={
            "run_at": "Fecha", "ean": "EAN", "product_name": "Producto",
            "category": "Categoría", "best_company": "Más barata",
            "worst_company": "Más cara", "precio_min": "Precio mín ($)",
            "precio_max": "Precio máx ($)", "brecha_pct": "Brecha (%)",
            "threshold_pct": "Umbral (%)",
        })
        st.dataframe(
            alerts_show.style.format({"Precio mín ($)": "${:,.0f}", "Precio máx ($)": "${:,.0f}", "Brecha (%)": "{:.1f}%"}, na_rep="—"),
            use_container_width=True,
            height=320,
        )

    st.divider()

    # ── 5. Reportes descargables ─────────────────────────────────────────────
    st.markdown("### 📄 Reportes ejecutivos")
    st.caption("Reportes HTML generados automáticamente al final de cada corrida. Descargalos para compartir.")

    report_files = list_reports(20)
    if not report_files:
        st.info("Todavía no hay reportes guardados. Se crean automáticamente cuando el scheduler ejecuta una corrida.")
    else:
        for report in report_files:
            with report.open("rb") as fh:
                st.download_button(
                    label=f"⬇ {report.name}",
                    data=fh.read(),
                    file_name=report.name,
                    mime="text/html",
                    key=f"dl_{report.name}",
                )


@st.dialog("Demo by Fedini", width="large")
def _demo_dialog() -> None:
    st.markdown("""
    ### 🎨 Demo by **Fedini**

    Este es un **demo completamente personalizable** del Centro de Inteligencia de Precios.

    **Todo puede ser adaptado:**
    - 🎨 Colores y temas visuales
    - 📊 Métricas y KPIs
    - 📝 Textos e idiomas
    - 🔧 Integraciones y fuentes de datos
    - 📱 Layout y diseño de la interfaz

    Contactanos para conocer cómo podemos adaptarlo a las necesidades de tu marca.
    """)

    col1, col2 = st.columns(2)
    with col1:
        st.link_button("→ Visita fedini.app", "https://fedini.app", use_container_width=True)
    with col2:
        if st.button("Entrar al demo", type="primary", use_container_width=True):
            st.session_state.demo_modal_shown = True
            st.rerun()


def _show_demo_modal() -> None:
    if "demo_modal_shown" not in st.session_state:
        st.session_state.demo_modal_shown = False

    if not st.session_state.demo_modal_shown:
        _demo_dialog()


def main() -> None:
    _start_scheduler_thread()  # inicia hilo de scheduler en segundo plano (una sola vez)
    
    _show_demo_modal()

    st.title("Centro de Inteligencia de Precios")
    st.caption("Panel visual para OnCity, Fravega y Cetrogar con filtros, indicadores, reportes y horarios automáticos")

    all_df, metadata = load_data()

    left, right, refresh_col = st.columns([2, 3, 1])
    with left:
        _metric_card("Actualizado", metadata["updated_at"])
    with right:
        def _src_status(fname: str) -> str:
            return f"✅ {fname}" if fname != "No encontrado" else "⚠️ Sin datos aún"

        st.markdown(
            f"""
            **Última actualización de datos**  
            OnCity: {_src_status(metadata['oncity_file'])}  
            Fravega: {_src_status(metadata['fravega_file'])}  
            Cetrogar: {_src_status(metadata['cetrogar_file'])}
            """
        )
    with refresh_col:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🔄 Actualizar datos", use_container_width=True, help="Fuerza la recarga de los archivos más recientes"):
            st.cache_data.clear()
            st.rerun()

    if all_df.empty:
        st.warning("Todavía no hay datos cargados en la plataforma. Cuando se ejecute el próximo horario automático se visualizarán aquí.")
        _render_cron_manager()
        return

    filtered_df = _filter_data(all_df)

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Panel", "Productos", "Comparativa", "Top", "Horarios", "Inteligencia"])

    with tab1:
        _render_dashboard(filtered_df)

    with tab2:
        _render_table_and_exports(filtered_df)

    with tab3:
        _render_price_comparison(filtered_df)

    with tab4:
        _render_tops(filtered_df)

    with tab5:
        _render_cron_manager()

    with tab6:
        _render_intelligence_tab(filtered_df)

    st.markdown("---")
    st.caption(
        "Nota stock/ventas: actualmente los sitios públicos exponen disponibilidad, pero no ventas por período de forma abierta. "
        "Si luego tienes API privada/ERP, se puede integrar esta capa sin cambiar la UX."
    )


if __name__ == "__main__":
    main()
