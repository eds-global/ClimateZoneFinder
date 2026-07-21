"""Solar PV Potential module — SolarGIS country-level monthly data.

Loads the World Bank / SolarGIS PVOUT Level 1 dataset
(solargis_country_pv_data.xlsx, "Monthly data" sheet) at startup and
converts the daily averages (kWh/kWp/day) to monthly totals (kWh/kWp/month).

No EPW data is required — epw_df and metadata are accepted for API
compatibility with pages/analysis.py but are not used.

Exposes:
    render(epw_df, metadata)  ← called from pages/analysis.py
"""

import pathlib
import pandas as pd
import plotly.graph_objects as go
from .st_compat import st

# ─── Colour palette ───────────────────────────────────────────────────────────
_C_PRIMARY = "#f59e0b"
_C_HIGH    = "#10b981"
_C_LOW     = "#ef4444"
_C_BORDER  = "#f97316"
_C_TEXT    = "#2c3e50"
_C_Black    = "#000000"

_MONTHS      = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DAYS_MONTH  = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

_EXCEL_PATH  = pathlib.Path(__file__).parents[2] / "solargis_country_pv_data.xlsx"
_C_POINT2    = "#6366f1"   # indigo – daily avg scatter on the absolute chart
_C_PRIMARY2  = "#f59e0b"


# ─── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_data() -> pd.DataFrame:
    """Load and clean the SolarGIS monthly sheet.

    Returns a DataFrame with columns:
        country, iso_a3, region, yearly_daily,
        jan … dec  (monthly totals in kWh/kWp/month)
    """
    raw = pd.read_excel(
        _EXCEL_PATH,
        sheet_name="Monthly data",
        header=None,
    )

    # Row 1 contains the column names; rows 2+ are data
    raw.columns = raw.iloc[1]
    raw = raw.iloc[2:].reset_index(drop=True)

    month_cols = ["January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"]

    df = pd.DataFrame()
    df["country"]      = raw["Country or region"].astype(str).str.strip()
    df["iso_a3"]       = raw["ISO_A3"].astype(str).str.strip()
    df["region"]       = raw["World Bank Region"].fillna("Other").astype(str).str.strip()
    df["yearly_daily"] = pd.to_numeric(raw["Yearly"], errors="coerce")

    # Keep raw daily averages (kWh/kWp/day) per month
    for col in month_cols:
        df[col.lower()[:3]] = pd.to_numeric(raw[col], errors="coerce")

    df = df.dropna(subset=["yearly_daily"]).reset_index(drop=True)
    return df


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _kpi(label: str, value: str, sub: str, color: str) -> str:
    return (
        f'<div style="background:white;padding:16px 12px;border-radius:8px;'
        f'border-left:4px solid {color};'
        f'box-shadow:0 2px 6px rgba(0,0,0,0.08);text-align:center;">'
        f'<div style="font-size:11px;font-weight:700;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:24px;font-weight:700;color:{_C_TEXT};">{value}</div>'
        f'<div style="font-size:11px;color:#64748b;margin-top:4px;">{sub}</div>'
        f'</div>'
    )


_C_POINT = "#3b82f6"   # blue – daily average scatter points


def _build_chart(
    monthly_vals: list,   # kWh/kWp/month  (bars, left axis)
    daily_vals: list,     # kWh/kWp/day    (points, right axis)
    peak_idx: int,
    low_idx: int,
    country: str,
) -> go.Figure:
    bar_colors = [
        _C_HIGH if i == peak_idx else _C_LOW if i == low_idx else _C_PRIMARY
        for i in range(12)
    ]

    fig = go.Figure()

    # ── Left axis: monthly total bars ────────────────────────────────────────
    fig.add_trace(go.Bar(
        name="Monthly Total (kWh/kWp)",
        x=_MONTHS,
        y=monthly_vals,
        marker_color=bar_colors,
        marker_line_width=0,
        yaxis="y1",
        hovertemplate="<b>%{x}</b><br>Monthly total: %{y:.0f} kWh/kWp<extra></extra>",
    ))

    # ── Right axis: daily average points ─────────────────────────────────────
    fig.add_trace(go.Scatter(
        name="Daily Avg (kWh/kWp)",
        x=_MONTHS,
        y=daily_vals,
        mode="markers",
        marker=dict(
            color=_C_POINT,
            size=10,
            symbol="circle",
            line=dict(color="white", width=2),
        ),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Daily avg: %{y:.2f} kWh/kWp<extra></extra>",
    ))

    y1_max = max(monthly_vals) * 1.25
    y2_min = min(daily_vals) * 0.88
    y2_max = max(daily_vals) * 1.15

    fig.update_layout(
        title=dict(
            text=f"Solar PV Yield — {country}",
            font=dict(size=16, color=_C_TEXT),
            x=0,
        ),
        xaxis=dict(title="Month", tickfont=dict(size=12)),
        yaxis=dict(
            title=dict(text="Monthly Total (kWh / kWp)",
                       font=dict(color=_C_Black)),
            tickfont=dict(color=_C_Black),
            range=[0, y1_max],
            gridcolor="#f1f5f9",
            showgrid=True,
        ),
        yaxis2=dict(
            title=dict(text="Daily Average (kWh / kWp)",
                       font=dict(color=_C_Black)),
            tickfont=dict(color=_C_Black),
            range=[y2_min, y2_max],
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.18,
            font=dict(size=11),
        ),
        height=460,
        template="plotly_white",
        plot_bgcolor="white",
        margin=dict(t=60, b=80, l=70, r=70),
    )

    # Colour-coded peak/low annotations
    for color, label, xp in [
        (_C_HIGH, "Highest Generation", 0.4),
        (_C_LOW,  "Lowest Generation",  0.6),
    ]:
        fig.add_annotation(
            xref="paper", yref="paper",
            x=xp, y=1.07,
            text=f"<span style='color:{color}'>■</span> {label}",
            showarrow=False,
            font=dict(size=11, color=_C_TEXT),
            align="left",
        )

    return fig


def _build_absolute_chart(
    monthly_kwh: list,   # kWh/month  (bars, left axis)
    daily_kwh: list,     # kWh/day    (points, right axis)
    peak_idx: int,
    low_idx: int,
    country: str,
    system_kwp: float,
) -> go.Figure:
    bar_colors = [
        _C_HIGH if i == peak_idx else _C_LOW if i == low_idx else _C_PRIMARY2
        for i in range(12)
    ]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Monthly Total (kWh)",
        x=_MONTHS,
        y=monthly_kwh,
        marker_color=bar_colors,
        marker_line_width=0,
        yaxis="y1",
        hovertemplate="<b>%{x}</b><br>Monthly total: %{y:.0f} kWh<extra></extra>",
    ))

    fig.add_trace(go.Scatter(
        name="Daily Avg (kWh)",
        x=_MONTHS,
        y=daily_kwh,
        mode="markers",
        marker=dict(
            color=_C_POINT2,
            size=10,
            symbol="circle",
            line=dict(color="white", width=2),
        ),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Daily avg: %{y:.1f} kWh<extra></extra>",
    ))

    y1_max = max(monthly_kwh) * 1.25
    y2_min = min(daily_kwh) * 0.88
    y2_max = max(daily_kwh) * 1.15

    fig.update_layout(
        title=dict(
            text=f"Solar PV Yield — {country} ({system_kwp:.1f} kWp system)",
            font=dict(size=16, color=_C_TEXT),
            x=0,
        ),
        xaxis=dict(title="Month", tickfont=dict(size=12)),
        yaxis=dict(
            title=dict(text="Monthly Total (kWh)", font=dict(color=_C_Black)),
            tickfont=dict(color=_C_Black),
            range=[0, y1_max],
            gridcolor="#f1f5f9",
            showgrid=True,
        ),
        yaxis2=dict(
            title=dict(text="Daily Average (kWh)", font=dict(color=_C_Black)),
            tickfont=dict(color=_C_Black),
            range=[y2_min, y2_max],
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.18,
            font=dict(size=11),
        ),
        height=460,
        template="plotly_white",
        plot_bgcolor="white",
        margin=dict(t=60, b=80, l=70, r=70),
    )

    for color, label, xp in [
        (_C_HIGH, "Highest Generation", 0.4),
        (_C_LOW,  "Lowest Generation",  0.6),
    ]:
        fig.add_annotation(
            xref="paper", yref="paper",
            x=xp, y=1.07,
            text=f"<span style='color:{color}'>■</span> {label}",
            showarrow=False,
            font=dict(size=11, color=_C_TEXT),
            align="left",
        )

    return fig


# ─── Main entry point ─────────────────────────────────────────────────────────

def render() -> None:
    """Render the Solar PV Potential dashboard (no EPW data required)."""
    st.markdown(
        f'<h3 style="color:{_C_TEXT};margin-bottom:4px;">Solar PV Potential</h3>',
        unsafe_allow_html=True,
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    try:
        df = _load_data()
    except Exception as exc:
        st.error(f"Could not load SolarGIS data: {exc}")
        return

    countries = sorted(df["country"].tolist())
    default   = "United Arab Emirates" if "United Arab Emirates" in countries else countries[0]

    col_sel, col_roof, col_pct = st.columns([2, 1, 1])
    with col_sel:
        selected = st.selectbox(
            "Select Country",
            options=countries,
            index=countries.index(default),
            help="SolarGIS PVOUT Level 1 — long-term monthly average specific yield.",
            key="solar_pv_country",
        )
    with col_roof:
        roof_size = st.number_input(
            "Roof Size (m²)",
            min_value=1,
            value=100,
            step=1,
            help="Total roof area in square metres.",
            key="solar_pv_roof_size",
        )
    with col_pct:
        roof_pct = st.slider(
            "Solar Coverage (%)",
            min_value=0,
            max_value=100,
            value=80,
            step=1,
            help="Percentage of roof area used for solar panels.",
            key="solar_pv_roof_pct",
        )

    effective_area = roof_size * (roof_pct / 100)
    system_kwp     = effective_area / 10   # 10 m² = 1 kWp

    row         = df[df["country"] == selected].iloc[0]
    daily_vals  = [float(row[m]) for m in ["jan", "feb", "mar", "apr", "may", "jun",
                                            "jul", "aug", "sep", "oct", "nov", "dec"]]
    monthly_vals = [d * days for d, days in zip(daily_vals, _DAYS_MONTH)]

    # Peak/low based on monthly totals (same ranking as daily since days differ slightly)
    peak_idx     = monthly_vals.index(max(monthly_vals))
    low_idx      = monthly_vals.index(min(monthly_vals))

    annual_total     = sum(monthly_vals)
    annual_daily_avg = float(row["yearly_daily"])

    # ── KPI row ───────────────────────────────────────────────────────────────
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)

    with k1:
        st.markdown(
            _kpi("Annual Yield", f"{annual_total:,.0f}", "kWh / kWp", _C_BORDER),
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            _kpi("Annual Daily Average", f"{annual_daily_avg:.2f}", "kWh / kWp.day", _C_BORDER),
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            _kpi("Peak Generation (monthly)", f"{monthly_vals[peak_idx]:.0f}",
                 f"kWh / kWp · {_MONTHS[peak_idx]}", _C_HIGH),
            unsafe_allow_html=True,
        )
    with k4:
        st.markdown(
            _kpi("Peak Generation (daily average)", f"{daily_vals[peak_idx]:.2f}", f"kWh / kWp · {_MONTHS[peak_idx]}", _C_PRIMARY),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── Dual-axis chart (per kWp reference) ──────────────────────────────────
    st.plotly_chart(
        _build_chart(monthly_vals, daily_vals, peak_idx, low_idx, selected),
        use_container_width=True,
    )

    # ── Absolute yield chart (scaled by roof system size) ────────────────────
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:13px;color:#64748b;margin-bottom:6px;">'
        f'<strong>System size:</strong> {effective_area:.1f} m² effective area '
        f'({roof_size} m² × {roof_pct}%) → <strong>{system_kwp:.2f} kWp</strong>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if system_kwp > 0:
        monthly_kwh      = [v * system_kwp for v in monthly_vals]
        daily_kwh        = [v * system_kwp for v in daily_vals]
        annual_kwh       = sum(monthly_kwh)
        annual_daily_kwh = annual_daily_avg * system_kwp

        a1, a2, a3, a4 = st.columns(4)
        with a1:
            st.markdown(
                _kpi("Annual Yield (System)", f"{annual_kwh:,.0f}", "kWh / year", _C_BORDER),
                unsafe_allow_html=True,
            )
        with a2:
            st.markdown(
                _kpi("Annual Daily Average", f"{annual_daily_kwh:.1f}", "kWh / day", _C_BORDER),
                unsafe_allow_html=True,
            )
        with a3:
            st.markdown(
                _kpi("Peak Month Output", f"{monthly_kwh[peak_idx]:,.0f}",
                     f"kWh · {_MONTHS[peak_idx]}", _C_HIGH),
                unsafe_allow_html=True,
            )
        with a4:
            st.markdown(
                _kpi("Peak Day Output", f"{daily_kwh[peak_idx]:.1f}",
                     f"kWh / day · {_MONTHS[peak_idx]}", _C_PRIMARY),
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        st.plotly_chart(
            _build_absolute_chart(monthly_kwh, daily_kwh, peak_idx, low_idx,
                                  selected, system_kwp),
            use_container_width=True,
        )
    else:
        st.info("Set a roof size and coverage above 0% to see absolute yield estimates.")

    # ── Data source note ──────────────────────────────────────────────────────
    iso    = row["iso_a3"]
    region = row["region"]

    st.markdown(
        f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;
                    padding:12px 16px;margin-top:8px;font-size:12px;color:#64748b;">
        <strong>Data Source:</strong> World Bank Group — Global Solar Atlas 2.0,
        powered by <strong>SolarGIS</strong>. PVOUT Level 1 long-term average
        practical photovoltaic power output (kWh/kWp.day) for
        <strong>{selected}</strong> (ISO&nbsp;{iso}, {region} region).
        <br><br>
        <em>Bars show monthly total yield (daily avg × days in month, kWh/kWp/month).
        Points show the long-term average daily specific yield (kWh/kWp/day) on the
        right axis. Actual yield varies with system tilt, shading, inverter losses,
        and local microclimate.</em>
        </div>
        """,
        unsafe_allow_html=True,
    )
