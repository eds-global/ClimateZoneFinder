"""Rainfall Analysis module — NOAA daily-summaries fetch + LEED compliance."""

import math
import json
import urllib.request
import pandas as pd
import plotly.graph_objects as go
from .config import (
    RUNOFF_COEFF_ROOF, RUNOFF_COEFF_PAVED,
    RUNOFF_COEFF_GREEN, RUNOFF_COEFF_WATER,
    VALID_GI_PERCENTILES, DEFAULT_HEAVY_RAIN_THRESHOLD,
)

try:
    import streamlit as st
except ImportError:
    import types
    import functools as _functools
    st = types.SimpleNamespace(
        cache_data=lambda **kw: _functools.lru_cache(maxsize=64),
        fragment=lambda fn: fn,
        warning=lambda *a, **kw: None,
        error=lambda *a, **kw: None,
        info=lambda *a, **kw: None,
        markdown=lambda *a, **kw: None,
        plotly_chart=lambda *a, **kw: None,
        columns=lambda *a, **kw: [],
        tabs=lambda *a, **kw: [],
        container=lambda *a, **kw: types.SimpleNamespace(
            __enter__=lambda s: s, __exit__=lambda s, *a: None),
        session_state={},
    )

_BUILTIN_STATIONS = {
    "Bengaluru":              "IN009010100",
    "Chennai":                "IN020040900",
    "New Delhi (Safdarjung)": "IN022021900",
    "New Delhi (Palam)":      "IN022023000",
    "Kolkata":                "IN024140300",
    "Mumbai":                 "IN012070800",
    "Pune":                   "IN012190100",
    "Surat":                  "IN005171200",
    "Lucknow/Amausi":         "IN023351400",
    "Jeddah":                 "SA000041024",
    "Riyadh":                 "SA000040438",
    "Hyderabad":              "IN001080500",
}


def get_stations() -> dict[str, str]:
    """Return the merged station dict (built-in + any overrides from noaa_stations.json)."""
    import json
    import pathlib
    stations = dict(_BUILTIN_STATIONS)
    override_path = pathlib.Path(__file__).parents[2] / "noaa_stations.json"
    if override_path.exists():
        try:
            extra = json.loads(override_path.read_text(encoding="utf-8"))
            stations.update({k: v for k, v in extra.items() if not k.startswith("_")})
        except Exception:
            pass
    return stations


STATIONS = get_stations()

SURFACE_TYPES = {
    "Roof Area":        RUNOFF_COEFF_ROOF,
    "Total paved area": RUNOFF_COEFF_PAVED,
    "Total green area": RUNOFF_COEFF_GREEN,
    "Waterbody":        RUNOFF_COEFF_WATER,
}

_RUNOFF_SURFACES = [
    {"label": "Roof Area",              "key": "roof",  "rc": RUNOFF_COEFF_ROOF,  "color": "#1d4ed8"},
    {"label": "Total paved area on site", "key": "paved", "rc": RUNOFF_COEFF_PAVED, "color": "#6b7280"},
    {"label": "Total green area on site", "key": "green", "rc": RUNOFF_COEFF_GREEN, "color": "#22c55e"},
    {"label": "Waterbody",              "key": "water", "rc": RUNOFF_COEFF_WATER, "color": "#0891b2"},
]

TYPOLOGIES = [
    "Office", "Commercial", "Healthcare", "Mixed Use",
    "Residential", "Hotels", "University", "Other",
]

PERCENTILE_OPTIONS = list(VALID_GI_PERCENTILES)

_MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── Data fetch ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _fetch_noaa(station_id: str, year: int) -> pd.DataFrame:
    base = "https://www.ncei.noaa.gov/access/services/data/v1"
    url = (
        f"{base}?dataset=daily-summaries"
        f"&stations={station_id}"
        f"&startDate={year}-01-01"
        f"&endDate={year}-12-31"
        f"&dataTypes=PRCP"
        f"&format=json"
        f"&units=metric"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        st.warning(f"No data returned for this station/year. ({exc})")
        return pd.DataFrame()

    try:
        arr = json.loads(raw)
    except Exception:
        st.warning("No data returned for this station/year.")
        return pd.DataFrame()

    if not isinstance(arr, list) or len(arr) == 0:
        st.warning("No data returned for this station/year.")
        return pd.DataFrame()

    rows = []
    for rec in arr:
        if "PRCP" not in rec:
            continue
        try:
            prcp_mm = float(str(rec["PRCP"]).replace(",", "."))
        except (ValueError, TypeError):
            continue
        date = pd.to_datetime(rec.get("DATE", ""), errors="coerce")
        if pd.isna(date):
            continue
        rows.append({
            "date":       date,
            "month":      date.month,
            "prcp_mm":    prcp_mm,
            "day_of_year": date.dayofyear,
        })

    if not rows:
        st.warning("No data returned for this station/year.")
        return pd.DataFrame()

    return pd.DataFrame(rows)


@st.cache_data(ttl=86400)
def _fetch_percentile_depth(station_id: str, percentile: int,
                             start_year: int = 1990) -> dict:
    from datetime import date as _date
    end_str   = _date.today().isoformat()
    start_str = f"{start_year}-01-01"

    url = (
        "https://www.ncei.noaa.gov/access/services/data/v1"
        f"?dataset=daily-summaries"
        f"&stations={station_id}"
        f"&startDate={start_str}"
        f"&endDate={end_str}"
        f"&dataTypes=PRCP"
        f"&format=json"
        f"&units=metric"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        return {"error": str(exc)}

    try:
        arr = json.loads(raw)
    except Exception:
        return {"error": "Invalid JSON from NOAA"}

    if not isinstance(arr, list) or len(arr) == 0:
        return {"error": "No data returned for this station"}

    rainfall = sorted(
        float(str(row["PRCP"]).replace(",", "."))
        for row in arr
        if "PRCP" in row and float(str(row["PRCP"]).replace(",", ".")) > 0
    )

    n = len(rainfall)
    if n == 0:
        return {"error": "No positive rainfall records found"}

    rank   = (percentile / 100.0) * (n - 1)
    lower  = int(rank)
    upper  = min(lower + 1, n - 1)
    weight = rank - lower
    value_mm = rainfall[lower] * (1 - weight) + rainfall[upper] * weight

    return {
        "rainfallDepth_m": value_mm / 1000.0,
        "raw_mm":          value_mm,
        "sample_size":     n,
        "percentile":      percentile,
        "start_date":      start_str,
        "end_date":        end_str,
    }


# ── LEED helpers ──────────────────────────────────────────────────────────────

def _runoff_volume(area: float, rc: float, depth_m: float) -> float:
    return area * rc * depth_m


def _gi_volume(shape: str, depth: float, count: int,
               length: float = 0, breadth: float = 0,
               radius: float = 0) -> float:
    if shape == "Cuboid":
        return length * breadth * depth * count
    else:
        return math.pi * radius ** 2 * depth * count


def _compliance_points(total_gi: float, total_runoff: float,
                        typology: str, percentile: int) -> int:
    compliant = total_gi >= total_runoff
    if typology == "Healthcare":
        return {95: 1, 98: 2, 85: 2}.get(percentile, 0) if compliant else 0
    else:
        return {95: 2, 98: 3, 85: 3}.get(percentile, 0) if compliant else 0


# ── KPI card helper ───────────────────────────────────────────────────────────

def _card(label: str, value: str, sub: str, color: str) -> str:
    return (
        f'<div style="background:white;padding:16px;border-radius:8px;'
        f'border-left:4px solid {color};box-shadow:0 2px 4px rgba(0,0,0,0.08);'
        f'text-align:center;">'
        f'<div style="font-size:11px;font-weight:700;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
        f'<div style="font-size:26px;font-weight:700;color:#2c3e50;'
        f'margin:8px 0;">{value}</div>'
        f'<div style="font-size:11px;color:#718096;">{sub}</div>'
        f'</div>'
    )


def _fmt_vol(litres: float, unit: str = "L") -> str:
    """Abbreviate large litre values for readability (5,702,745 → 5.70M L)."""
    if litres >= 1_000_000:
        return f"{litres / 1_000_000:.2f}M {unit}"
    if litres >= 10_000:
        return f"{litres / 1_000:.1f}K {unit}"
    return f"{litres:,.0f} {unit}"


# ── Bar color by intensity ────────────────────────────────────────────────────

def _intensity_color(mm: float) -> str:
    if mm < 50:
        return "#93c5fd"
    elif mm < 150:
        return "#3b82f6"
    elif mm < 300:
        return "#1d4ed8"
    else:
        return "#1e3a5f"


# ── Tab renderers ─────────────────────────────────────────────────────────────

def _render_monthly_rainfall(df: pd.DataFrame, year: int) -> None:
    monthly = df.groupby("month")["prcp_mm"].sum().reindex(range(1, 13), fill_value=0)
    annual_total = monthly.sum()
    annual_mean  = annual_total / 12

    colors = [_intensity_color(v) for v in monthly.values]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=_MONTH_LABELS,
        y=monthly.values,
        marker_color=colors,
        name="Monthly Rainfall",
        hovertemplate="<b>%{x}</b><br>%{y:.1f} mm<extra></extra>",
    ))
    fig.add_hline(
        y=annual_mean,
        line_dash="dash", line_color="red",
        annotation_text=f"Monthly mean ({annual_mean:.1f} mm)",
        annotation_position="top right",
    )
    fig.update_layout(
        title=f"Monthly Rainfall Totals – {year}",
        xaxis_title="Month", yaxis_title="Rainfall (mm)",
        height=450, template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    wettest_idx  = int(monthly.idxmax())
    driest_idx   = int(monthly.idxmin())
    monsoon_total = monthly[[6, 7, 8, 9]].sum()

    c1, c2, c5 = st.columns(3)
    with c1: st.markdown(_card("Annual Total", f"{annual_total:.1f} mm", "Full year", "#1d4ed8"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Wettest Month", f"{monthly[wettest_idx]:.1f} mm", _MONTH_LABELS[wettest_idx - 1], "#1e3a5f"), unsafe_allow_html=True)
    # with c3: st.markdown(_card("Driest Month", f"{monthly[driest_idx]:.1f} mm", _MONTH_LABELS[driest_idx - 1], "#93c5fd"), unsafe_allow_html=True)
    # with c4: st.markdown(_card("Monsoon Jun–Sep", f"{monsoon_total:.1f} mm", "Jun, Jul, Aug, Sep", "#3b82f6"), unsafe_allow_html=True)
    with c5: st.markdown(_card("Mean Monthly", f"{annual_mean:.1f} mm", "Annual ÷ 12", "#0891b2"), unsafe_allow_html=True)


def _render_rainy_days(df: pd.DataFrame, year: int,
                        heavy_rain_threshold: float) -> None:
    rainy = df[df["prcp_mm"] > 0].copy()

    def _count(mask_fn):
        return rainy[mask_fn(rainy["prcp_mm"])].groupby("month").size().reindex(range(1, 13), fill_value=0)

    light_days    = _count(lambda x: (x > 0)   & (x < 10))
    moderate_days = _count(lambda x: (x >= 10) & (x < 25))
    heavy_days    = _count(lambda x: (x >= 25) & (x < heavy_rain_threshold))
    extreme_days  = _count(lambda x:  x >= heavy_rain_threshold)

    fig = go.Figure()
    for label, series, color in [
        ("Light (<10 mm)",    light_days,    "#bfdbfe"),
        ("Moderate (10–25)",  moderate_days, "#3b82f6"),
        ("Heavy (25–50)",     heavy_days,    "#1d4ed8"),
        (f"Extreme (≥{heavy_rain_threshold:.0f} mm)", extreme_days, "#ef4444"),
    ]:
        fig.add_trace(go.Bar(
            x=_MONTH_LABELS, y=series.values, name=label,
            hovertemplate="<b>%{x}</b><br>" + label + ": %{y}<extra></extra>",
        ))
    fig.update_traces(marker_color=None)
    # Re-apply colors per trace
    fig.data[0].marker.color = "#bfdbfe"
    fig.data[1].marker.color = "#3b82f6"
    fig.data[2].marker.color = "#1d4ed8"
    fig.data[3].marker.color = "#ef4444"

    fig.update_layout(
        barmode="stack",
        title=f"Rainy Days per Month by Intensity – {year}",
        xaxis_title="Month", yaxis_title="Days",
        height=450, template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    total_rainy   = int(rainy.shape[0])
    extreme_count = int((rainy["prcp_mm"] >= heavy_rain_threshold).sum())

    all_days = df.sort_values("date").copy()
    all_days["is_wet"] = all_days["prcp_mm"] > 0

    max_dry = max_wet = cur_dry = cur_wet = 0
    dry_start = dry_end = dry_best_start = dry_best_end = None

    for _, row in all_days.iterrows():
        if not row["is_wet"]:
            cur_wet = 0
            if cur_dry == 0:
                dry_start = row["date"]
            cur_dry += 1
            dry_end = row["date"]
            if cur_dry > max_dry:
                max_dry = cur_dry
                dry_best_start, dry_best_end = dry_start, dry_end
        else:
            cur_dry = 0
            dry_start = None
            cur_wet += 1
            max_wet = max(max_wet, cur_wet)

    longest_dry_str = (
        f"{dry_best_start.strftime('%b %d')} → {dry_best_end.strftime('%b %d')}"
        if dry_best_start and dry_best_end else "N/A"
    )

    c1, c2 = st.columns(2)
    with c1: st.markdown(_card("Total Rainy Days", str(total_rainy), "Days with prcp > 0", "#3b82f6"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Extreme Rain Days", str(extreme_count), f"≥ {heavy_rain_threshold:.0f} mm/day", "#ef4444"), unsafe_allow_html=True)
    # with c3: st.markdown(_card("Max Consec. Dry", str(max_dry), "days", "#f59e0b"), unsafe_allow_html=True)
    # with c4: st.markdown(_card("Max Consec. Wet", str(max_wet), "days", "#0891b2"), unsafe_allow_html=True)
    # with c5: st.markdown(_card("Longest Dry Spell", longest_dry_str, f"{max_dry} days", "#8b5cf6"), unsafe_allow_html=True)


@st.fragment
def _render_roof_runoff(df: pd.DataFrame, year: int,
                         roof_area_m2: float) -> None:
    # ── Area inputs per surface type ──────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    cols = [c1, c2, c3, c4]
    areas = {}
    for i, surf in enumerate(_RUNOFF_SURFACES):
        with cols[i]:
            areas[surf["key"]] = st.number_input(
                surf["label"],
                value=(roof_area_m2 if i == 0 else 0.0),
                min_value=0.0,
                step=10.0,
                key=f"runoff_area_{surf['key']}",
            )

    monthly_prcp = df.groupby("month")["prcp_mm"].sum().reindex(range(1, 13), fill_value=0)

    # ── Stacked runoff chart ───────────────────────────────────────────────────
    fig = go.Figure()
    monthly_per_surf = {}
    for surf in _RUNOFF_SURFACES:
        monthly_vol = (monthly_prcp / 1000.0) * areas[surf["key"]] * surf["rc"]
        monthly_per_surf[surf["key"]] = monthly_vol
        fig.add_trace(go.Bar(
            x=_MONTH_LABELS,
            y=monthly_vol.values,
            name=surf["label"],
            marker_color=surf["color"],
            hovertemplate=f"<b>%{{x}}</b><br>{surf['label']}: %{{y:.3f}} m³<extra></extra>",
        ))

    fig.update_layout(
        barmode="stack",
        title=f"Monthly Surface Runoff by Type – {year}",
        xaxis_title="Month",
        yaxis_title="Runoff Volume (m³)",
        height=450,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    surf_df = pd.DataFrame(
        {surf["key"]: monthly_per_surf[surf["key"]] for surf in _RUNOFF_SURFACES}
    )
    total_monthly = surf_df.sum(axis=1)
    total_annual  = float(total_monthly.sum())
    peak_month    = int(total_monthly.idxmax()) if total_annual > 0 else 1

    ca, cb = st.columns(2)
    with ca:
        st.markdown(
            _card("Total Annual Runoff", _fmt_vol(total_annual * 1000), "All surfaces combined", "#ac1dd8"),
            unsafe_allow_html=True,
        )
    with cb:
        st.markdown(
            _card("Peak Runoff Month", _fmt_vol(float(total_monthly[peak_month]) * 1000),
                  _MONTH_LABELS[peak_month - 1], "#ef4444"),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    short_labels = ["Roof", "Paved", "Green Area", "Waterbody"]
    cs1, cs2, cs3, cs4 = st.columns(4)
    for i, (surf, col) in enumerate(zip(_RUNOFF_SURFACES, [cs1, cs2, cs3, cs4])):
        with col:
            annual_vol = float(surf_df[surf["key"]].sum())
            st.markdown(
                _card(short_labels[i], _fmt_vol(annual_vol * 1000), f"RC = {surf['rc']:.2f}", surf["color"]),
                unsafe_allow_html=True,
            )


# def _render_summary(df: pd.DataFrame, heavy_rain_threshold: float) -> None:
#     monthly_prcp  = df.groupby("month")["prcp_mm"].sum().reindex(range(1, 13), fill_value=0)
#     rainy_days    = df[df["prcp_mm"] > 0].groupby("month").size().reindex(range(1, 13), fill_value=0)
#     heavy_days    = df[df["prcp_mm"] >= heavy_rain_threshold].groupby("month").size().reindex(range(1, 13), fill_value=0)
#     runoff_m3     = (monthly_prcp / 1000) * 200.0 * 0.85  # default 200 m² for summary

#     def _classify(mm):
#         if mm < 25:   return "Arid"
#         elif mm < 75:  return "Dry"
#         elif mm < 150: return "Moderate"
#         elif mm < 300: return "Wet"
#         else:          return "Very Wet"

#     summary = pd.DataFrame({
#         "Month":            _MONTH_LABELS,
#         "Total (mm)":       monthly_prcp.values,
#         "Rainy Days":       rainy_days.values,
#         f"Heavy Days (≥{heavy_rain_threshold:.0f}mm)": heavy_days.values,
#         "Runoff (m³)":      runoff_m3.values,
#         "Classification":   [_classify(v) for v in monthly_prcp.values],
#     })

#     st.dataframe(
#         summary,
#         use_container_width=True,
#         hide_index=True,
#         column_config={
#             "Total (mm)":  st.column_config.NumberColumn(format="%.1f"),
#             "Runoff (m³)": st.column_config.NumberColumn(format="%.1f"),
#         },
#     )

#     peak_month_idx = int(monthly_prcp.idxmax())
#     peak_mm        = float(monthly_prcp[peak_month_idx])
#     peak_name      = _MONTH_LABELS[peak_month_idx - 1]
#     st.info(f"Peak rainfall month: {peak_name} ({peak_mm:.1f} mm)")


# @st.fragment
# def _render_leed_tab(station_id: str) -> None:
#     c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
#     with c1:
#         st.selectbox(
#             "Rainfall Percentile",
#             options=PERCENTILE_OPTIONS,
#             index=2,
#             key="leed_percentile",
#             format_func=lambda x: f"{x}th percentile",
#         )
#     with c2:
#         st.number_input(
#             "Historical data from year",
#             value=1990, min_value=1950, max_value=2020,
#             step=1, key="leed_start_year",
#         )
#     with c3:
#         st.selectbox(
#             "Typology",
#             options=TYPOLOGIES,
#             index=1,
#             key="leed_typology",
#         )
#     with c4:
#         st.radio(
#             "Units",
#             options=["SI (m², m³)", "Imperial (ft², ft³)"],
#             key="leed_units",
#             horizontal=True,
#         )

#     result = _fetch_percentile_depth(
#         station_id,
#         st.session_state.get("leed_percentile", 95),
#         st.session_state.get("leed_start_year", 1990),
#     )

#     if "error" in result:
#         st.error(f"Could not fetch percentile depth: {result['error']}")
#         return

#     depth_m = result["rainfallDepth_m"]
#     st.info(
#         f"Design storm depth ({result['percentile']}th percentile, "
#         f"{result['start_date']} – {result['end_date']}, "
#         f"n={result['sample_size']} rain-days): "
#         f"**{result['raw_mm']:.2f} mm** ({depth_m:.4f} m)"
#     )

#     st.markdown("#### Surface Details")
#     default_surfaces = pd.DataFrame({
#         "Surface Type": list(SURFACE_TYPES.keys())[:1],
#         "Area":         [0.0],
#         "RC":           [list(SURFACE_TYPES.values())[0]],
#     })
#     surfaces_df = st.data_editor(
#         st.session_state.get("leed_surfaces_df", default_surfaces),
#         key="leed_surfaces_editor",
#         num_rows="dynamic",
#         use_container_width=True,
#         column_config={
#             "Surface Type": st.column_config.SelectboxColumn(
#                 "Surface Type",
#                 options=list(SURFACE_TYPES.keys()),
#                 required=True,
#             ),
#             "Area": st.column_config.NumberColumn(
#                 "Area (m²)", min_value=0.0, format="%.2f"
#             ),
#             "RC": st.column_config.NumberColumn(
#                 "Runoff Coefficient", min_value=0.0, max_value=1.0, format="%.2f"
#             ),
#         },
#         hide_index=True,
#     )
#     st.session_state["leed_surfaces_df"] = surfaces_df
#     st.caption("RC defaults — Roof/Paved: 0.90 · Green: 0.10 · Waterbody: 0.90")

#     st.markdown("#### Recharge Strategies (Green Infrastructure)")
#     default_gi = pd.DataFrame({
#         "GI Type":   [""],
#         "Shape":     ["Cuboid"],
#         "Length m":  [0.0],
#         "Breadth m": [0.0],
#         "Radius m":  [0.0],
#         "Depth m":   [0.0],
#         "Count":     [1],
#     })
#     gi_df = st.data_editor(
#         st.session_state.get("leed_gi_df", default_gi),
#         key="leed_gi_editor",
#         num_rows="dynamic",
#         use_container_width=True,
#         column_config={
#             "GI Type": st.column_config.TextColumn("GI Type", required=True),
#             "Shape": st.column_config.SelectboxColumn(
#                 "Shape", options=["Cuboid", "Cylindrical"], required=True
#             ),
#             "Length m":  st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
#             "Breadth m": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
#             "Radius m":  st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
#             "Depth m":   st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
#             "Count":     st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
#         },
#         hide_index=True,
#     )
#     st.session_state["leed_gi_df"] = gi_df
#     st.caption(
#         "For Cuboid: use Length + Breadth + Depth. "
#         "For Cylindrical: use Radius + Depth. Leave unused fields as 0."
#     )

#     if st.button("Calculate LEED Compliance", type="primary", use_container_width=False):
#         surface_results = []
#         total_runoff = 0.0
#         for _, row in surfaces_df.iterrows():
#             area = float(row["Area"])
#             rc   = float(row["RC"])
#             vol  = _runoff_volume(area, rc, depth_m)
#             surface_results.append({
#                 "type": row["Surface Type"],
#                 "area": area, "rc": rc, "runoff": vol,
#             })
#             total_runoff += vol

#         gi_results = []
#         total_gi = 0.0
#         for _, row in gi_df.iterrows():
#             vol = _gi_volume(
#                 shape=row["Shape"],
#                 depth=float(row["Depth m"]),
#                 count=int(row["Count"]),
#                 length=float(row["Length m"]),
#                 breadth=float(row["Breadth m"]),
#                 radius=float(row["Radius m"]),
#             )
#             gi_results.append({"type": row["GI Type"], "shape": row["Shape"], "volume": vol})
#             total_gi += vol

#         typology   = st.session_state.get("leed_typology", "Commercial")
#         percentile = int(st.session_state.get("leed_percentile", 95))
#         points     = _compliance_points(total_gi, total_runoff, typology, percentile)
#         max_points = 2 if typology == "Healthcare" else 3

#         st.session_state["leed_results"] = {
#             "surface_results": surface_results,
#             "gi_results":      gi_results,
#             "total_runoff":    total_runoff,
#             "total_gi":        total_gi,
#             "points":          points,
#             "max_points":      max_points,
#             "typology":        typology,
#             "percentile":      percentile,
#             "depth_m":         depth_m,
#         }

#     if "leed_results" not in st.session_state:
#         return

#     res        = st.session_state["leed_results"]
#     total_runoff    = res["total_runoff"]
#     total_gi        = res["total_gi"]
#     points          = res["points"]
#     max_points      = res["max_points"]
#     percentile      = res["percentile"]
#     surface_results = res["surface_results"]
#     gi_results      = res["gi_results"]

#     is_noncompliant = points <= 0
#     at_max          = (not is_noncompliant) and points >= max_points
#     can_enhance     = (not is_noncompliant) and (not at_max)

#     if is_noncompliant:
#         card_color = "#b71c1c"
#         card_text  = (
#             f"Non-compliant — Recharge/Storage ({total_gi:.2f} m³) "
#             f"fails to meet site runoff ({total_runoff:.2f} m³) "
#             f"during {percentile}th percentile event."
#         )
#     elif at_max:
#         card_color = "#2e7d32"
#         card_text  = (
#             f"Compliant — {points} point/s achievable. "
#             f"Recharge ({total_gi:.2f} m³) ≥ Runoff ({total_runoff:.2f} m³)."
#         )
#     else:
#         card_color = "#1b5e20"
#         card_text  = (
#             f"Compliant (Enhance) — {points} point/s now. "
#             f"Increasing percentile or adding GI may yield up to {max_points} point/s."
#         )

#     st.markdown(
#         f'<div style="background:white;padding:16px;border-radius:8px;'
#         f'border-left:4px solid {card_color};box-shadow:0 2px 4px rgba(0,0,0,0.08);'
#         f'text-align:center;">'
#         f'<div style="font-size:11px;font-weight:700;color:{card_color};'
#         f'text-transform:uppercase;letter-spacing:0.5px;">LEED Compliance Status</div>'
#         f'<div style="font-size:18px;font-weight:700;color:#2c3e50;margin:8px 0;">{card_text}</div>'
#         f'</div>',
#         unsafe_allow_html=True,
#     )
#     st.markdown("<br>", unsafe_allow_html=True)

#     c1, c2, c3, c4 = st.columns(4)
#     with c1: st.markdown(_card("Total Runoff",        f"{total_runoff:.2f} m³", "Site runoff volume",    "#ef4444"), unsafe_allow_html=True)
#     with c2: st.markdown(_card("Total Recharge",      f"{total_gi:.2f} m³",    "GI storage volume",     "#2e7d32"), unsafe_allow_html=True)
#     with c3: st.markdown(_card("LEED Points Earned",  str(points),              "Points achieved",       "#3b82f6"), unsafe_allow_html=True)
#     with c4: st.markdown(_card("Max Available Points", str(max_points),         f"For {res['typology']}", "#f59e0b"), unsafe_allow_html=True)

#     fig = go.Figure()
#     for s in surface_results:
#         fig.add_trace(go.Bar(
#             name=s["type"],
#             x=["Site Runoff", "Recharge/Storage"],
#             y=[s["runoff"], 0],
#         ))
#     for g in gi_results:
#         fig.add_trace(go.Bar(
#             name=g["type"],
#             x=["Site Runoff", "Recharge/Storage"],
#             y=[0, g["volume"]],
#         ))
#     fig.update_layout(
#         barmode="stack",
#         title=f"Runoff vs Recharge — {percentile}th Percentile Event",
#         yaxis_title="Volume (m³)",
#         height=400, template="plotly_white",
#         legend=dict(orientation="h", y=-0.25),
#     )
#     st.plotly_chart(fig, use_container_width=True)

#     left_col, right_col = st.columns(2)
#     with left_col:
#         st.markdown("**Surface Runoff Breakdown**")
#         surf_table = pd.DataFrame([
#             {
#                 "Surface Type": s["type"],
#                 "Area (m²)":    round(s["area"],   3),
#                 "RC":           round(s["rc"],     3),
#                 "Runoff (m³)":  round(s["runoff"], 3),
#             }
#             for s in surface_results
#         ])
#         st.dataframe(surf_table, use_container_width=True, hide_index=True)

#     with right_col:
#         st.markdown("**GI Storage Breakdown**")
#         gi_table = pd.DataFrame([
#             {
#                 "GI Type":     g["type"],
#                 "Shape":       g["shape"],
#                 "Volume (m³)": round(g["volume"], 3),
#             }
#             for g in gi_results
#         ])
#         st.dataframe(gi_table, use_container_width=True, hide_index=True)


# ── GI Balance tab ───────────────────────────────────────────────────────────

@st.fragment
def _render_gi_balance_tab(station_id: str, year: int) -> None:
    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        percentile = st.selectbox(
            "Baseline Percentile",
            options=PERCENTILE_OPTIONS,
            index=2,
            key="balance_percentile",
            format_func=lambda x: f"{x}th percentile",
        )
    with c2:
        start_year = st.number_input(
            "Historical data from year",
            value=1990, min_value=1950, max_value=2020,
            step=1, key="balance_start_year",
        )

    result = _fetch_percentile_depth(station_id, percentile, start_year)
    if "error" in result:
        st.error(f"Could not fetch baseline depth: {result['error']}")
        return

    baseline_mm = result["raw_mm"]
    st.info(
        f"{result['percentile']}th-percentile daily depth "
        f"({result['start_date']} – {result['end_date']}, "
        f"n={result['sample_size']} rain-days): "
        f"**{baseline_mm:.2f} mm = {baseline_mm:.2f} L/m²** — used as GI capacity baseline"
    )

    # Full-year data (cached, no extra cost)
    full_df = _fetch_noaa(station_id, year)
    if full_df.empty:
        st.warning("No rainfall data available for this station/year.")
        return

    daily = full_df.sort_values("date").copy()
    daily["stored"]   = daily["prcp_mm"].clip(upper=baseline_mm)
    daily["overflow"] = (daily["prcp_mm"] - baseline_mm).clip(lower=0)

    # Monthly aggregation
    monthly_grp = daily.groupby("month").agg(
        stored=("stored",   "sum"),
        overflow=("overflow", "sum"),
    ).reindex(range(1, 13), fill_value=0)

    # Per-day overflow flag still needed for KPI cards
    overflow_mask = daily["overflow"] > 0
    overflow_days = daily[overflow_mask]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=_MONTH_LABELS,
        y=monthly_grp["stored"].values,
        name="Stored (L/m²)",
        marker_color="#22c55e",
        customdata=monthly_grp[["stored", "overflow"]].values,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Stored: %{customdata[0]:.1f} L/m²<br>"
            "Overflow: %{customdata[1]:.1f} L/m²"
            "<extra></extra>"
        ),
    ))
    fig.add_trace(go.Bar(
        x=_MONTH_LABELS,
        y=-monthly_grp["overflow"].values,
        name="Overflow (L/m²)",
        marker_color="#ef4444",
        customdata=monthly_grp[["stored", "overflow"]].values,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Stored: %{customdata[0]:.1f} L/m²<br>"
            "Overflow: %{customdata[1]:.1f} L/m²"
            "<extra></extra>"
        ),
    ))
    fig.add_hline(
        y=0,
        line_dash="dash", line_color="#374151", line_width=1.5,
        annotation_text=f"Daily baseline: {baseline_mm:.2f} mm",
        annotation_position="top right",
    )
    fig.update_layout(
        barmode="relative",
        title=(
            f"Monthly Rainwater Harvesting Potential – {year} "
            f"({percentile}th-percentile baseline: {baseline_mm:.2f} mm/day)"
        ),
        xaxis_title="Month",
        yaxis_title="Volume (L/m²)",
        height=450,
        template="plotly_white",
        hovermode="x unified",
        bargap=0.25,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Summary metrics ────────────────────────────────────────────────────────
    total_recharge = daily["stored"].sum()

    total_overflow = daily["overflow"].sum()

    if not overflow_days.empty:
        worst_month_idx = int(monthly_grp["overflow"].idxmax())
        worst_month_str = (
            f"{_MONTH_LABELS[worst_month_idx - 1]} "
            f"({monthly_grp.loc[worst_month_idx, 'overflow']:,.0f} L/m²)"
        )
    else:
        worst_month_str = "None"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            _card("Storage Potential", f"{total_recharge:,.0f} L/m²", "Total captured by GI", "#22c55e"),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _card("Recharge Potential", f"{total_overflow:,.0f} L/m²", "Excess beyond GI capacity", "#ef4444"),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _card("Overflow Days", str(len(overflow_days)),
                  f"Days with rain > {baseline_mm:.1f} mm", "#f59e0b"),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            _card("Worst Overflow Month", worst_month_str, "Highest monthly excess", "#8b5cf6"),
            unsafe_allow_html=True,
        )


# ── Public entry point ────────────────────────────────────────────────────────

def render(
    station_id: str,
    year: int,
    start_month: int,
    end_month: int,
    heavy_rain_threshold: float,
    roof_area_m2: float,
) -> None:
    df = _fetch_noaa(station_id, year)
    if df.empty:
        return

    df = df[(df["month"] >= start_month) & (df["month"] <= end_month)].copy()
    if df.empty:
        st.warning("No data in the selected month range.")
        return

    tab1, tab2, tab3, tab4 = st.tabs([
        "Monthly Rainfall", "Rainy Days", "Storage Potential","Runoff"
    ])

    with tab1:
        _render_monthly_rainfall(df, year)

    with tab2:
        _render_rainy_days(df, year, heavy_rain_threshold)

    with tab3:
         _render_gi_balance_tab(station_id, year)

    # with tab4:
    #     _render_summary(df, heavy_rain_threshold)

    # with tab5:
    #     _render_leed_tab(station_id)

    with tab4:
        _render_roof_runoff(df, year, roof_area_m2)
       
