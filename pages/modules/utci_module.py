"""Universal Thermal Climate Index (UTCI) tab rendering module.

Pipeline for every EPW hour:
  1. Solar position (altitude/azimuth) via pvlib, from EPW lat/lon/timezone.
  2. Outdoor Mean Radiant Temperature (MRT):
       - Longwave component approximated as dry bulb air temperature (the EPW
         carries no sky/ground surface temperature to do better).
       - Shortwave component is the ASHRAE 55 Annex C "SolarCal" effective
         radiant field, converted to a delta-MRT via
         pythermalcomfort.models.solar_gain, using solar altitude, direct
         normal irradiance from the EPW, and body exposure/posture
         parameters (sky view factor, shade fraction, ground reflectance).
       - MRT = dry bulb temperature + delta-MRT (delta-MRT = 0 at night).
  3. UTCI via pythermalcomfort.models.utci(tdb, tr=MRT, v=wind, rh).

This mirrors the outdoor "SolarCal" adaptation of ASHRAE 55 used by common
outdoor-comfort tooling when no measured MRT/globe-temperature data exists.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
from pythermalcomfort.models import solar_gain, utci as pytc_utci

from .config import (
    UTCI_DEFAULT_POSTURE, UTCI_DEFAULT_SKY_VIEW_FACTOR, UTCI_DEFAULT_SHADE_FRACTION,
    UTCI_DEFAULT_GROUND_REFLECTANCE, UTCI_ASW, UTCI_WIND_MIN, UTCI_WIND_MAX,
    UTCI_STRESS_BINS, UTCI_STRESS_LABELS, UTCI_STRESS_COLORS,
)

_MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ─────────────────────────────────────────────────────────────────────────────
# Calculation
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_timezone(tz_str):
    try:
        return pytz.timezone(tz_str)
    except Exception:
        try:
            offset = float(tz_str)
            hours = int(offset)
            minutes = int(round((offset - hours) * 60))
            sign = "+" if offset >= 0 else "-"
            return pytz.timezone(f"UTC{sign}{abs(hours):02d}:{abs(minutes):02d}")
        except Exception:
            return pytz.UTC


def compute_solar_position(df: pd.DataFrame, lat: float, lon: float, tz_str: str) -> pd.DataFrame:
    """Solar altitude/azimuth for every row of df (position-aligned, unfiltered)."""
    from pvlib import solarposition

    tz = _resolve_timezone(tz_str)
    times = pd.DatetimeIndex(df["datetime"].values)
    times = times.tz_localize(tz) if times.tz is None else times.tz_convert(tz)
    solpos = solarposition.get_solarposition(times, lat, lon)
    return pd.DataFrame(
        {
            "solar_altitude": solpos["apparent_elevation"].to_numpy(),
            "solar_azimuth": solpos["azimuth"].to_numpy(),
        },
        index=df.index,
    )


# ASHRAE 55 Annex C only publishes projected-area-factor (fp) tables for three
# postures — standing, sitting, supine (see UTCI_MODULE_GUIDE.md §3.3). There is
# no "walking" fp table in the standard, so a walking pedestrian is modeled with
# the standing table: an ambulatory person's solar-facing silhouette is closest
# to standing, and this is the convention outdoor-comfort tools use in the
# absence of a dedicated ambulatory posture study.
_POSTURE_ALIASES = {"walking": "standing"}


def compute_mrt(
    df: pd.DataFrame,
    lat: float,
    lon: float,
    tz_str: str,
    posture: str = UTCI_DEFAULT_POSTURE,
    sky_view_factor: float = UTCI_DEFAULT_SKY_VIEW_FACTOR,
    shade_fraction: float = UTCI_DEFAULT_SHADE_FRACTION,
    ground_reflectance: float = UTCI_DEFAULT_GROUND_REFLECTANCE,
    asw: float = UTCI_ASW,
) -> pd.DataFrame:
    """Estimate outdoor Mean Radiant Temperature for every EPW hour."""
    solpos = compute_solar_position(df, lat, lon, tz_str)
    altitude = solpos["solar_altitude"].to_numpy()
    dni = df["direct_normal_irradiance"].fillna(0.0).clip(lower=0).to_numpy()

    daytime = altitude > 0
    alt_safe = np.clip(altitude, 0.001, 90.0)

    f_bes = float(np.clip(1.0 - shade_fraction, 0.0, 1.0))
    f_svv = float(np.clip(sky_view_factor, 0.0, 1.0))

    gain = solar_gain(
        sol_altitude=alt_safe,
        sharp=0.0,
        sol_radiation_dir=dni,
        sol_transmittance=1.0,
        f_svv=np.full_like(alt_safe, f_svv),
        f_bes=np.full_like(alt_safe, f_bes),
        asw=asw,
        posture=_POSTURE_ALIASES.get(posture, posture),
        floor_reflectance=ground_reflectance,
        round_output=False,
    )
    delta_mrt = np.where(daytime, np.asarray(gain.delta_mrt, dtype=float), 0.0)
    mrt = df["dry_bulb_temperature"].to_numpy() + delta_mrt

    return pd.DataFrame(
        {
            "solar_altitude": altitude,
            "solar_azimuth": solpos["solar_azimuth"].to_numpy(),
            "delta_mrt": delta_mrt,
            "mrt": mrt,
        },
        index=df.index,
    )


def compute_utci(df: pd.DataFrame, mrt: np.ndarray) -> pd.DataFrame:
    """Vectorised UTCI + stress category for every row of df, given its MRT."""
    wind = df["wind_speed"].fillna(0.0).clip(lower=UTCI_WIND_MIN, upper=UTCI_WIND_MAX).to_numpy()
    result = pytc_utci(
        tdb=df["dry_bulb_temperature"].to_numpy(),
        tr=mrt,
        v=wind,
        rh=df["relative_humidity"].to_numpy(),
        limit_inputs=True,
        round_output=False,
    )
    return pd.DataFrame(
        {
            "utci": np.asarray(result.utci, dtype=float),
            "utci_stress_category": np.asarray(result.stress_category),
        },
        index=df.index,
    )


def add_utci_columns(
    df: pd.DataFrame,
    lat: float,
    lon: float,
    tz_str: str,
    posture: str = UTCI_DEFAULT_POSTURE,
    sky_view_factor: float = UTCI_DEFAULT_SKY_VIEW_FACTOR,
    shade_fraction: float = UTCI_DEFAULT_SHADE_FRACTION,
    ground_reflectance: float = UTCI_DEFAULT_GROUND_REFLECTANCE,
) -> pd.DataFrame:
    """Return a copy of df with solar position, MRT, and UTCI columns added."""
    out = df.copy()
    mrt_df = compute_mrt(
        out, lat, lon, tz_str,
        posture=posture, sky_view_factor=sky_view_factor,
        shade_fraction=shade_fraction, ground_reflectance=ground_reflectance,
    )
    out["solar_altitude"] = mrt_df["solar_altitude"].values
    out["solar_azimuth"]  = mrt_df["solar_azimuth"].values
    out["delta_mrt"]      = mrt_df["delta_mrt"].values
    out["mrt"]            = mrt_df["mrt"].values

    utci_df = compute_utci(out, out["mrt"].to_numpy())
    out["utci"] = utci_df["utci"].values
    out["utci_stress_category"] = utci_df["utci_stress_category"].values
    out["utci_feels_like_diff"] = out["utci"] - out["dry_bulb_temperature"]
    return out


def compute_daily_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Daily min/max/avg for UTCI, dry bulb, and MRT, indexed by day-of-year."""
    daily = df.groupby("doy").agg(
        utci_min=("utci", "min"),
        utci_max=("utci", "max"),
        utci_avg=("utci", "mean"),
        dbt_min=("dry_bulb_temperature", "min"),
        dbt_max=("dry_bulb_temperature", "max"),
        dbt_avg=("dry_bulb_temperature", "mean"),
        mrt_avg=("mrt", "mean"),
    ).reset_index()

    year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
    daily["datetime"] = pd.to_datetime(
        daily["doy"].astype(str) + f"-{year}", format="%j-%Y", errors="coerce"
    )
    daily["datetime_display"] = daily["datetime"].dt.strftime("%b %d")
    return daily


def _stress_category_order(categories):
    """Return categories present in df sorted cold→hot per UTCI_STRESS_LABELS."""
    present = set(categories)
    return [c for c in UTCI_STRESS_LABELS if c in present]


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render(
    df: pd.DataFrame,
    daily_stats: pd.DataFrame,
    active_tab: str,
    start_date,
    end_date,
    start_hour: int,
    end_hour: int,
) -> None:
    """Dispatch rendering based on the active tab."""
    if active_tab == "Annual Trend":
        _render_annual_trend(df, daily_stats, start_date, end_date)
    elif active_tab == "Monthly Trend":
        _render_monthly_scatter(df)
    elif active_tab == "Stress Category":
        _render_stress_distribution(df, start_date, end_date)


# ─────────────────────────────────────────────────────────────────────────────


def _render_annual_trend(df, daily_stats, start_date, end_date):
    start_month_num = start_date.month
    end_month_num   = end_date.month

    start_doy = pd.to_datetime(f"2024-{start_month_num:02d}-01").dayofyear
    end_doy   = (
        366
        if end_month_num == 12
        else pd.to_datetime(f"2024-{end_month_num + 1:02d}-01").dayofyear - 1
    )

    fig = go.Figure()

    # ── Greyed-out: before selected range ─────────────────────────────────────
    if start_doy > 1:
        before = daily_stats[daily_stats["doy"] < start_doy]
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["utci_avg"],
                                 mode="lines", line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 name="Unselected Period", showlegend=True, hoverinfo="skip"))

    # ── Active range ──────────────────────────────────────────────────────────
    active = daily_stats[(daily_stats["doy"] >= start_doy) & (daily_stats["doy"] <= end_doy)]

    # "No thermal stress" reference band (9–26 °C UTCI)
    fig.add_trace(go.Bar(x=active["datetime_display"],
                         y=[26 - 9] * len(active), base=[9] * len(active),
                         name="No thermal stress zone (UTCI 9–26°C)",
                         marker_color="rgba(116,183,97,0.18)",
                         marker_line_width=0, hoverinfo="skip"))

    # UTCI min/max range
    fig.add_trace(go.Bar(x=active["datetime_display"],
                         y=active["utci_max"] - active["utci_min"],
                         base=active["utci_min"],
                         name="UTCI Range",
                         marker_color="rgba(255,152,0,0.30)",
                         marker_line_width=0,
                         customdata=active["utci_max"],
                         hovertemplate="<b>%{x}</b><br>UTCI Min: %{base:.1f}°C<br>UTCI Max: %{customdata:.1f}°C<extra></extra>"))

    # Average MRT (context line, thin dashed)
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["mrt_avg"],
                             mode="lines", name="Mean Radiant Temperature (avg)",
                             line=dict(color="#9c27b0", width=1.5, dash="dot"),
                             hovertemplate="<b>%{x}</b><br>MRT: %{y:.2f}°C<extra></extra>"))

    # Dry bulb average
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["dbt_avg"],
                             mode="lines", name="Dry Bulb Temperature (avg)",
                             line=dict(color="#3b82f6", width=2),
                             hovertemplate="<b>%{x}</b><br>DBT: %{y:.2f}°C<extra></extra>"))

    # UTCI average (feels-like)
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["utci_avg"],
                             mode="lines", name="UTCI – \"Feels Like\" (avg)",
                             line=dict(color="#d32f2f", width=2.5),
                             hovertemplate="<b>%{x}</b><br>UTCI: %{y:.2f}°C<extra></extra>"))

    # ── Greyed-out: after selected range ──────────────────────────────────────
    if end_doy < 365:
        after = daily_stats[daily_stats["doy"] > end_doy]
        if not after.empty:
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["utci_avg"],
                                     mode="lines", line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                     showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Annual UTCI vs Dry Bulb Temperature Trend",
        xaxis_title=None,
        yaxis_title="Temperature (°C)",
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_rangeslider_visible=False,
        barmode="overlay",
        height=470,
        template="plotly_white",
        margin=dict(b=80),
    )
    st.plotly_chart(fig, use_container_width=True)

    if df.empty:
        st.info("No data available.")
        return

    _render_annual_scatter(df)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    max_row = df.loc[df["utci"].idxmax()]
    min_row = df.loc[df["utci"].idxmin()]

    date_filtered = df[
        (df["datetime"].dt.date >= start_date) &
        (df["datetime"].dt.date <= end_date)
    ]
    period = date_filtered if not date_filtered.empty else df

    utci_avg   = period["utci"].mean()
    feels_diff = period["utci_feels_like_diff"].mean()
    heat_hrs   = int((df["utci"] >= 26).sum())
    cold_hrs   = int((df["utci"] < 9).sum())
    extreme_hrs = int(((df["utci"] >= 46) | (df["utci"] < -27)).sum())

    def _card(label, value, sub, color):
        return f"""
<div style="background:white;padding:16px;border-radius:8px;border-left:4px solid {color};
            box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">
  <div style="font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
  <div style="font-size:26px;font-weight:700;color:#2c3e50;margin:8px 0;">{value}</div>
  <div style="font-size:11px;color:#718096;">{sub}</div>
</div>"""

    c1, c2, c3, c4, c5,c6 = st.columns(6)
    with c1: st.markdown(_card("Max UTCI", f"{max_row['utci']:.1f} °C",
                                f"{max_row['datetime'].strftime('%b %d')} · {int(max_row['hour']):02d}:00 · {max_row['utci_stress_category']}",
                                "#ef4444"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Min UTCI", f"{min_row['utci']:.1f} °C",
                                f"{min_row['datetime'].strftime('%b %d')} · {int(min_row['hour']):02d}:00 · {min_row['utci_stress_category']}",
                                "#3b82f6"), unsafe_allow_html=True)
    with c3: st.markdown(_card("Avg UTCI (period)", f"{utci_avg:.1f} °C", "Selected date range", "#8b5cf6"), unsafe_allow_html=True)
    # with c4: st.markdown(_card("Feels-Like Δ (period)", f"{feels_diff:+.1f} °C", "UTCI − Dry Bulb, avg", "#f59e0b"), unsafe_allow_html=True)
    with c4: st.markdown(_card("Extreme Stress Hrs", f"{extreme_hrs}", "Annual, ≥46°C or <−27°C", "#7A1A22"), unsafe_allow_html=True)

    # c5, c6 = st.columns(2)
    with c5: st.markdown(_card("Heat Stress Hours", f"{heat_hrs}", "Annual, UTCI ≥ 26°C", "#CE2029"), unsafe_allow_html=True)
    with c6: st.markdown(_card("Cold Stress Hours", f"{cold_hrs}", "Annual, UTCI < 9°C", "#3288BD"), unsafe_allow_html=True)

    st.caption(
        "MRT is estimated from EPW direct-normal irradiance and solar position "
        "(ASHRAE 55 SolarCal shortwave model), added to dry bulb air temperature "
        "as the longwave baseline. Adjust posture / sky view / shade / ground "
        "reflectance in the left panel to reflect the outdoor context being studied."
    )


def _render_annual_scatter(df):
    """Every hour of the year, single panel, colored by UTCI stress category.

    EPW datetimes are a TMY composite (each month sourced from a different
    real year), so the raw `datetime` column is not a single contiguous
    year — it must be re-based onto one fixed calendar year for the x-axis,
    the same trick `compute_daily_stats()` uses for the trend chart above.
    """
    categories = _stress_category_order(df["utci_stress_category"].unique())

    x_norm = pd.to_datetime(dict(
        year=2024,
        month=df["datetime"].dt.month,
        day=df["datetime"].dt.day,
        hour=df["datetime"].dt.hour,
        minute=df["datetime"].dt.minute,
    ))

    fig = go.Figure()
    for cat in categories:
        mask = df["utci_stress_category"] == cat
        if not mask.any():
            continue
        fig.add_trace(go.Scattergl(
            x=x_norm[mask], y=df.loc[mask, "utci"],
            mode="markers",
            marker=dict(size=3, color=UTCI_STRESS_COLORS[UTCI_STRESS_LABELS.index(cat)], opacity=0.5),
            name=cat.title(),
            hovertemplate=f"<b>{cat.title()}</b><br>%{{x|%b %d %H:00}}<br>UTCI: %{{y:.1f}}°C<extra></extra>",
        ))

    fig.update_layout(
        title="Hourly UTCI — Full Year, Colored by Thermal Stress Category",
        xaxis_title=None,
        yaxis_title="UTCI (°C)",
        xaxis=dict(tickformat="%b"),
        template="plotly_white",
        height=420,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(b=40),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_monthly_scatter(df):
    """Every hour of the year, faceted by month, colored by UTCI stress category.

    Mirrors the classic "daily chart" small-multiples layout (one panel per
    month, hour-of-day on x), but plots UTCI instead of dry bulb and colors
    each point by its thermal stress category instead of a single hue, so the
    shift in stress composition across months (not just the mean) is visible.
    """
    from plotly.subplots import make_subplots

    months_present = sorted(df["month"].unique())
    categories = _stress_category_order(df["utci_stress_category"].unique())

    fig = make_subplots(
        rows=1, cols=len(months_present),
        shared_yaxes=True,
        horizontal_spacing=0.004,
        subplot_titles=[_MONTH_SHORT[m - 1] for m in months_present],
    )

    seen_categories = set()
    for col, m in enumerate(months_present, start=1):
        month_df = df[df["month"] == m]

        for cat in categories:
            cat_df = month_df[month_df["utci_stress_category"] == cat]
            if cat_df.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=cat_df["hour"], y=cat_df["utci"],
                    mode="markers",
                    marker=dict(size=4, color=UTCI_STRESS_COLORS[UTCI_STRESS_LABELS.index(cat)], opacity=0.55),
                    name=cat.title(), legendgroup=cat,
                    showlegend=cat not in seen_categories,
                    hovertemplate=f"<b>{cat.title()}</b><br>Hour %{{x}}:00<br>UTCI: %{{y:.1f}}°C<extra></extra>",
                ),
                row=1, col=col,
            )
            seen_categories.add(cat)

        hourly_mean = month_df.groupby("hour")["utci"].mean().reset_index()
        fig.add_trace(
            go.Scatter(
                x=hourly_mean["hour"], y=hourly_mean["utci"],
                mode="lines", line=dict(color="#d32f2f", width=2.5),
                name="Hourly Average", legendgroup="avg",
                showlegend=col == 1,
                hovertemplate="Hour %{x}:00<br>Avg UTCI: %{y:.1f}°C<extra></extra>",
            ),
            row=1, col=col,
        )

        fig.update_xaxes(tickvals=[0, 6, 12, 18], row=1, col=col)

    fig.update_layout(
        title="Hourly UTCI by Month — Full Year, Colored by Thermal Stress Category",
        template="plotly_white",
        height=480,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.1, xanchor="center", x=0.5),
        margin=dict(t=120, b=40),
    )
    fig.update_yaxes(title_text="UTCI (°C)", row=1, col=1)
    st.plotly_chart(fig, use_container_width=True)


def _render_stress_distribution(df, start_date, end_date):
    period = df[(df["datetime"].dt.date >= start_date) & (df["datetime"].dt.date <= end_date)]
    period = period if not period.empty else df

    counts = period["utci_stress_category"].value_counts()
    order  = _stress_category_order(counts.index)
    counts = counts.reindex(order).fillna(0)
    pct    = counts / len(period) * 100
    colors = [UTCI_STRESS_COLORS[UTCI_STRESS_LABELS.index(c)] for c in order]

    fig = go.Figure(go.Bar(
        x=counts.values, y=[c.title() for c in order], orientation="h",
        marker_color=colors,
        customdata=pct.values,
        hovertemplate="<b>%{y}</b><br>%{x:.0f} hours (%{customdata:.1f}%)<extra></extra>",
    ))
    fig.update_layout(
        title="Hours by UTCI Thermal Stress Category (selected date range)",
        xaxis_title="Hours", yaxis_title=None,
        template="plotly_white", height=450, showlegend=False,
        margin=dict(l=180),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Monthly composition (100% stacked) ─────────────────────────────────────
    monthly_pct = (
        df.assign(month_name=df["month"].apply(lambda m: _MONTH_SHORT[m - 1]))
        .groupby(["month", "month_name", "utci_stress_category"])
        .size()
        .rename("hours")
        .reset_index()
    )
    monthly_totals = monthly_pct.groupby("month")["hours"].transform("sum")
    monthly_pct["pct"] = monthly_pct["hours"] / monthly_totals * 100

    fig2 = go.Figure()
    all_categories = _stress_category_order(df["utci_stress_category"].unique())
    for cat in all_categories:
        sub = monthly_pct[monthly_pct["utci_stress_category"] == cat].sort_values("month")
        fig2.add_trace(go.Bar(
            x=sub["month_name"], y=sub["pct"], name=cat.title(),
            marker_color=UTCI_STRESS_COLORS[UTCI_STRESS_LABELS.index(cat)],
            hovertemplate="<b>%{x}</b><br>" + cat.title() + ": %{y:.1f}%<extra></extra>",
        ))
    fig2.update_layout(
        title="Monthly UTCI Stress Category Composition (full year)",
        xaxis_title="Month", yaxis_title="% of Hours",
        barmode="stack", template="plotly_white", height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig2.update_xaxes(categoryorder="array", categoryarray=_MONTH_SHORT)
    st.plotly_chart(fig2, use_container_width=True)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    no_stress_pct = pct.get("no thermal stress", 0.0)
    heat_pct = sum(pct.get(c, 0.0) for c in UTCI_STRESS_LABELS[6:])
    cold_pct = sum(pct.get(c, 0.0) for c in UTCI_STRESS_LABELS[:5])

    def _card(label, value, sub, color):
        return f"""
<div style="background:white;padding:16px;border-radius:8px;border-left:4px solid {color};
            box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">
  <div style="font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
  <div style="font-size:26px;font-weight:700;color:#2c3e50;margin:8px 0;">{value}</div>
  <div style="font-size:11px;color:#718096;">{sub}</div>
</div>"""

    c1, c2, c3 = st.columns(3)
    with c1: st.markdown(_card("No Thermal Stress", f"{no_stress_pct:.1f} %", "Of selected period", "#74B761"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Heat Stress", f"{heat_pct:.1f} %", "Moderate or worse", "#E97A2E"), unsafe_allow_html=True)
    with c3: st.markdown(_card("Cold Stress", f"{cold_pct:.1f} %", "Slight or worse", "#67BCD4"), unsafe_allow_html=True)
