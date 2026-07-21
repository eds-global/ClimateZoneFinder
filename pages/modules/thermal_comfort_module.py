"""Thermal Comfort Module – psychrometric analysis, adaptive comfort, and design strategies.

Exposes:
    render(df, months, comfort_model, air_speed_adjust)  ← called from pages/analysis.py

Physics notes
-------------
Humidity ratio (W) is computed from dry-bulb temperature and relative humidity:
    e_s = 611.2 * exp(17.67 * T / (T + 243.5))   [Pa]  (Magnus-Tetens approximation)
    e   = RH/100 * e_s
    W   = 0.62198 * e / (P_atm – e)               [kg water / kg dry air]
    where P_atm = 101 325 Pa (standard atmosphere)

Adaptive comfort (ASHRAE 55-2017 §5.4):
    Prevailing mean outdoor temperature uses exponentially-weighted running mean
    of daily mean temperatures:
        T_pma(d) = (1 – α)·T_d + α·T_pma(d-1)    α = 0.9 per ASHRAE 55
    Comfort temperature:
        T_comf = 0.31 · T_pma + 17.8  (°C)
    Acceptability bands:
        80 %:  T_comf ± 3.5 °C
        90 %:  T_comf ± 2.5 °C
    Applicable range: T_pma in [10 °C, 33.5 °C]

Degree hours:
    CDH = Σ max(0, DBT − 24)   per hour
    HDH = Σ max(0, 18 − DBT)   per hour

Strategy mapping (applied in priority order per hour):
    1. DBT < (T_comf − 3.5)                                → Heating
    2. DBT > (T_comf + 3.5) AND RH > 60 %                 → Mechanical Cooling
    3. DBT > (T_comf + 3.5) AND RH ≤ 60 %                 → Evaporative Cooling
    4. DBT within [T_comf − 3.5, T_comf + 3.5]
       AND wind_speed ≥ 1.0 m/s AND DBT > 24 °C           → Natural Ventilation
    5. daily ΔT ≥ 8 °C AND DBT > (T_comf + 2.5)
       AND 20 ≤ hour ≤ 6                                   → Night Flushing / Thermal Mass
    6. otherwise                                            → Comfortable
"""

from typing import Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from .st_compat import st
from .config import (
    ASHRAE_ALPHA, ASHRAE_T_PMA_MIN, ASHRAE_T_PMA_MAX,
    ASHRAE_COMFORT_NEUTRAL_A, ASHRAE_COMFORT_NEUTRAL_B,
    COMFORT_BAND_80_PCT, COMFORT_BAND_90_PCT,
    CDH_BASE_TEMP, HDH_BASE_TEMP,
    NV_MIN_WIND_SPEED, NV_COOL_DBT_THRESHOLD,
    NIGHT_FLUSH_DIURNAL_MIN, MECH_COOLING_RH_THRESHOLD,
)

# ─── Constants ────────────────────────────────────────────────────────────────

_P_ATM = 101_325.0          # Standard atmospheric pressure [Pa]
_ALPHA  = ASHRAE_ALPHA
_CDH_BASE = CDH_BASE_TEMP
_HDH_BASE = HDH_BASE_TEMP
_WIND_NV_THRESHOLD = NV_MIN_WIND_SPEED
_DIURNAL_MASS_THRESHOLD = NIGHT_FLUSH_DIURNAL_MIN

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Comfort category display labels and colours
_COMFORT_CATS  = ["Comfortable", "Too Hot", "Too Cold", "Too Humid", "Too Dry"]
_COMFORT_COLORS = {
    "Comfortable":     "#2ecc71",
    "Too Hot":         "#e74c3c",
    "Too Cold":        "#3498db",
    "Too Humid":       "#9b59b6",
    "Too Dry":         "#f39c12",
}

# Strategy labels and colours
_STRATEGY_LABELS = [
    "Comfortable",
    "Natural Ventilation",
    "Evaporative Cooling",
    "Mechanical Cooling",
    "Heating",
    "Night Flushing / Thermal Mass",
]
_STRATEGY_COLORS = {
    "Comfortable":                  "#2ecc71",
    "Natural Ventilation":          "#27ae60",
    "Evaporative Cooling":          "#1abc9c",
    "Mechanical Cooling":           "#e74c3c",
    "Heating":                      "#e67e22",
    "Night Flushing / Thermal Mass":"#8e44ad",
}

# ── Comfort bubble chart: 9-zone psychrographic classification ────────────────
# Temperature thresholds: 22 °C (cold/comfort), 28.5 °C (comfort/hot), 33 °C (hot/extreme)
# RH thresholds         : 30 % (dry/moderate), 70 % (moderate/humid)
_BUBBLE_CATS = [
    "Cold & Humid", "Cold", "Cold & Dry",
    "Humid", "Comfortable", "Dry",
    "Hot & Humid", "Hot", "Hot & Dry",
]
_BUBBLE_COLORS: dict = {
    "Cold & Humid":  "#0d47a1",   # Dark Blue
    "Cold":          "#1976d2",   # Medium Blue
    "Cold & Dry":    "#90caf9",   # Light Blue
    "Humid":         "#9e9e9e",   # Grey
    "Comfortable":   "#00c853",   # Vibrant Green
    "Dry":           "#a5d6a7",   # Light Green
    "Hot & Humid":   "#8d6e63",   # Brown / muted orange
    "Hot":           "#ff9800",   # Orange
    "Hot & Dry":     "#d32f2f",   # Red
}


# ─── Core computation functions ───────────────────────────────────────────────

def compute_psychrometric_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add humidity_ratio and wet_bulb_temperature columns to a copy of df.

    Humidity ratio W [kg/kg dry air]:
        e_s = 611.2 · exp(17.67·T / (T + 243.5))  [Pa]
        e   = RH/100 · e_s
        W   = 0.62198 · e / (101325 – e)

    Wet bulb temperature (Stull 2011 approximation, valid 5–99 % RH):
        T_wb = T·atan(0.151977·√(RH+8.313659))
              + atan(T+RH) − atan(RH−1.676331)
              + 0.00391838·RH^1.5·atan(0.023101·RH) − 4.686035
    """
    out = df.copy()

    T   = pd.to_numeric(out["dry_bulb_temperature"], errors="coerce")
    RH  = pd.to_numeric(out["relative_humidity"],    errors="coerce").clip(0, 100)

    # Saturation vapour pressure [Pa] – Magnus-Tetens
    e_s = 611.2 * np.exp(17.67 * T / (T + 243.5))
    # Actual vapour pressure [Pa]
    e   = RH / 100.0 * e_s
    # Humidity ratio [kg/kg]
    hr  = 0.62198 * e / (_P_ATM - e)
    out["humidity_ratio"] = hr.clip(lower=0.0)

    # Wet-bulb temperature (Stull 2011)
    rh_safe = RH.clip(0.5, 100)
    T_wb = (
        T  * np.arctan(0.151977 * np.sqrt(rh_safe + 8.313659))
        + np.arctan(T + rh_safe)
        - np.arctan(rh_safe - 1.676331)
        + 0.00391838 * rh_safe ** 1.5 * np.arctan(0.023101 * rh_safe)
        - 4.686035
    )
    out["wet_bulb_temperature"] = T_wb

    return out


def compute_adaptive_comfort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add ASHRAE 55 adaptive comfort columns to a copy of df.

    Returns df with additional columns:
        daily_mean_temp     – daily mean DBT
        t_pma               – prevailing mean outdoor temperature (running mean)
        t_comf              – comfort temperature  = 0.31·T_pma + 17.8
        t_comf_80_lo / hi   – 80 % acceptability band (±3.5 °C)
        t_comf_90_lo / hi   – 90 % acceptability band (±2.5 °C)
        in_80               – bool: DBT within 80 % band
        in_90               – bool: DBT within 90 % band
        adaptive_applicable – bool: T_pma in [10, 33.5]
    """
    out = df.copy()
    T   = pd.to_numeric(out["dry_bulb_temperature"], errors="coerce")

    if "month" not in out.columns:
        out["month"] = out["datetime"].dt.month
    if "doy" not in out.columns:
        out["doy"] = out["datetime"].dt.dayofyear
    if "hour" not in out.columns:
        out["hour"] = out["datetime"].dt.hour

    # Daily mean temperature
    daily_mean = out.groupby("doy")["dry_bulb_temperature"].mean()

    # Exponential running mean:  T_pma(d) = (1–α)·T_d + α·T_pma(d-1)
    doy_sorted = sorted(daily_mean.index)
    t_pma_daily = {}
    prev = daily_mean[doy_sorted[0]]
    for d in doy_sorted:
        val = (1 - _ALPHA) * daily_mean[d] + _ALPHA * prev
        t_pma_daily[d] = val
        prev = val
    pma_series = pd.Series(t_pma_daily, name="t_pma")

    # Map back to hourly
    out["t_pma"] = out["doy"].map(pma_series)

    # Comfort temperature
    out["t_comf"]       = ASHRAE_COMFORT_NEUTRAL_A * out["t_pma"] + ASHRAE_COMFORT_NEUTRAL_B
    out["t_comf_80_lo"] = out["t_comf"] - COMFORT_BAND_80_PCT
    out["t_comf_80_hi"] = out["t_comf"] + COMFORT_BAND_80_PCT
    out["t_comf_90_lo"] = out["t_comf"] - COMFORT_BAND_90_PCT
    out["t_comf_90_hi"] = out["t_comf"] + COMFORT_BAND_90_PCT

    # Applicability (ASHRAE 55 limits prevailing mean to valid range)
    out["adaptive_applicable"] = out["t_pma"].between(ASHRAE_T_PMA_MIN, ASHRAE_T_PMA_MAX)

    # Whether current hour is within bands
    out["in_80"] = (T >= out["t_comf_80_lo"]) & (T <= out["t_comf_80_hi"]) & out["adaptive_applicable"]
    out["in_90"] = (T >= out["t_comf_90_lo"]) & (T <= out["t_comf_90_hi"]) & out["adaptive_applicable"]

    return out


def classify_comfort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Classify each hour into a comfort category (simplified static PMV zone).

    Static zone: 22–26 °C  AND  RH 30–60 %
    Categories:
        Comfortable  – within static zone
        Too Hot      – DBT > 26 °C
        Too Cold     – DBT < 22 °C
        Too Humid    – RH  > 60 % (and not already too hot)
        Too Dry      – RH  < 30 % (and not already too cold)

    Adds column:  comfort_cat
    """
    out = df.copy()
    T  = pd.to_numeric(out["dry_bulb_temperature"], errors="coerce")
    RH = pd.to_numeric(out["relative_humidity"],    errors="coerce")

    conditions = [
        (T >= 22) & (T <= 26) & (RH >= 30) & (RH <= 60),  # Comfortable
        T > 26,                                             # Too Hot
        T < 22,                                             # Too Cold
        (RH > 60) & (T >= 22) & (T <= 26),                # Too Humid
        (RH < 30) & (T >= 22) & (T <= 26),                # Too Dry
    ]
    choices = ["Comfortable", "Too Hot", "Too Cold", "Too Humid", "Too Dry"]
    out["comfort_cat"] = np.select(conditions, choices, default="Too Hot")
    return out


def classify_bubble_comfort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Classify each row into one of 9 psychrographic comfort zones.

    Strict 3 × 3 grid — temperature zone × RH zone, no exceptions:

        RH \\ DBT   | < 22 °C      | 22–28.5 °C  | > 28.5 °C
        -----------|--------------|-------------|-------------
        > 70 %     | Cold & Humid | Humid       | Hot & Humid
        30–70 %    | Cold         | Comfortable | Hot
        < 30 %     | Cold & Dry   | Dry         | Hot & Dry

    RH thresholds follow the standard definition:
        > 70 % = humid,  30–70 % = moderate,  < 30 % = dry
    Temperature thresholds:  22 °C and 28.5 °C.
    Adds column: bubble_comfort
    """
    out = df.copy()
    T  = pd.to_numeric(out["dry_bulb_temperature"], errors="coerce")
    RH = pd.to_numeric(out["relative_humidity"],    errors="coerce")

    cold    = T < 22.0
    comfort = (T >= 22.0) & (T <= 28.5)
    hot     = T > 28.5            # covers both moderate and extreme heat uniformly

    dry      = RH < 30.0
    moderate = (RH >= 30.0) & (RH <= 70.0)
    humid    = RH > 70.0

    conditions = [
        cold    & humid,
        cold    & moderate,
        cold    & dry,
        comfort & humid,
        comfort & moderate,
        comfort & dry,
        hot     & humid,
        hot     & moderate,
        hot     & dry,
    ]
    choices = [
        "Cold & Humid", "Cold", "Cold & Dry",
        "Humid", "Comfortable", "Dry",
        "Hot & Humid", "Hot", "Hot & Dry",
    ]
    out["bubble_comfort"] = np.select(conditions, choices, default="Comfortable")
    return out


def map_strategies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign a passive design strategy to each hour.

    Requires:  t_comf, t_comf_80_lo, t_comf_80_hi columns (from compute_adaptive_comfort).
    Adds column:  strategy
    """
    out = df.copy()
    T    = pd.to_numeric(out["dry_bulb_temperature"], errors="coerce")
    RH   = pd.to_numeric(out["relative_humidity"],    errors="coerce")
    WS   = pd.to_numeric(out["wind_speed"],           errors="coerce").fillna(0)

    # Ensure hour column exists for night-flushing logic
    if "hour" not in out.columns:
        out["hour"] = out["datetime"].dt.hour
    if "doy" not in out.columns:
        out["doy"] = out["datetime"].dt.dayofyear

    # Diurnal range per day
    if "diurnal_range" not in out.columns:
        day_range = (
            out.groupby("doy")["dry_bulb_temperature"]
            .apply(lambda x: x.max() - x.min())
            .rename("diurnal_range")
        )
        out["diurnal_range"] = out["doy"].map(day_range)

    t_lo = out["t_comf_80_lo"]
    t_hi = out["t_comf_80_hi"]

    is_night      = (out["hour"] >= 20) | (out["hour"] <= 6)
    large_diurnal = out["diurnal_range"] >= _DIURNAL_MASS_THRESHOLD

    # Conditions evaluated in priority order (first match wins via np.select)
    conditions = [
        (T < t_lo),                                                          # 1: Heating
        large_diurnal & (T > t_hi) & is_night,                              # 2: Night flushing
        (T > t_hi) & (RH > 60),                                             # 3: Mechanical cooling
        (T > t_hi) & (RH <= 60),                                            # 4: Evaporative cooling
        (T >= t_lo) & (T <= t_hi) & (WS >= _WIND_NV_THRESHOLD) & (T > 24), # 5: Natural ventilation
    ]
    choices = [
        "Heating",
        "Night Flushing / Thermal Mass",
        "Mechanical Cooling",
        "Evaporative Cooling",
        "Natural Ventilation",
    ]
    out["strategy"] = np.select(conditions, choices, default="Comfortable")
    return out


def compute_degree_hours(df: pd.DataFrame) -> dict:
    """
    Compute cooling and heating degree hours.

    CDH base = 24 °C,  HDH base = 18 °C
    Returns dict with keys: cdh_total, hdh_total, cdh_per_month, hdh_per_month
    """
    T = pd.to_numeric(df["dry_bulb_temperature"], errors="coerce")
    if "month" not in df.columns:
        month = df["datetime"].dt.month
    else:
        month = df["month"]

    cdh = (T - _CDH_BASE).clip(lower=0)
    hdh = (_HDH_BASE - T).clip(lower=0)

    return {
        "cdh_total": float(cdh.sum()),
        "hdh_total": float(hdh.sum()),
        "cdh_per_month": cdh.groupby(month).sum().reindex(range(1, 13), fill_value=0),
        "hdh_per_month": hdh.groupby(month).sum().reindex(range(1, 13), fill_value=0),
    }


def generate_design_summary(
    pct_comfortable: float,
    pct_in_80: float,
    pct_in_90: float,
    strategy_pcts: dict,
    cdh_total: float,
    hdh_total: float,
    mean_rh: float,
    diurnal_range_mean: float,
) -> str:
    """
    Generate a data-driven, human-readable design recommendation paragraph.
    All percentage inputs are in the range 0–100.
    """
    lines = []

    # Overall comfort character
    cooling_pct = strategy_pcts.get("Mechanical Cooling", 0) + strategy_pcts.get("Evaporative Cooling", 0)
    heating_pct = strategy_pcts.get("Heating", 0)

    if cooling_pct >= 40:
        if heating_pct < 10:
            lines.append(
                f"This climate is strongly **cooling-dominated**: {cooling_pct:.0f}% of hours require "
                f"active or passive cooling strategies."
            )
        else:
            lines.append(
                f"This climate is **mixed**, with cooling needs in {cooling_pct:.0f}% of hours and "
                f"heating needs in {heating_pct:.0f}% of hours."
            )
    elif heating_pct >= 40:
        lines.append(
            f"This climate is strongly **heating-dominated**: {heating_pct:.0f}% of hours fall "
            f"below the adaptive comfort band."
        )
    elif pct_comfortable >= 50:
        lines.append(
            f"This climate is generally **temperate and comfortable**: {pct_comfortable:.0f}% of hours "
            f"fall within the static comfort zone."
        )
    else:
        lines.append(
            f"This climate shows **moderate thermal stress**: only {pct_comfortable:.0f}% of hours "
            f"are within the static comfort zone."
        )

    # Adaptive comfort
    lines.append(
        f"ASHRAE 55 adaptive comfort is met for **{pct_in_80:.0f}%** (80 % acceptability) and "
        f"**{pct_in_90:.0f}%** (90 % acceptability) of applicable hours."
    )

    # Passive strategies
    nv_pct = strategy_pcts.get("Natural Ventilation", 0)
    ec_pct = strategy_pcts.get("Evaporative Cooling", 0)
    mc_pct = strategy_pcts.get("Mechanical Cooling", 0)
    nm_pct = strategy_pcts.get("Night Flushing / Thermal Mass", 0)

    if nv_pct >= 15:
        lines.append(
            f"**Natural ventilation** is a viable strategy for ~{nv_pct:.0f}% of hours — "
            f"operable facades and cross-ventilation are strongly recommended."
        )

    if ec_pct >= 10:
        if mean_rh < 55:
            lines.append(
                f"**Evaporative cooling** can address ~{ec_pct:.0f}% of hours: "
                f"the average RH of {mean_rh:.0f}% provides sufficient latent capacity."
            )
        else:
            lines.append(
                f"Evaporative cooling potential is limited (~{ec_pct:.0f}% of hours) due to "
                f"the relatively high ambient humidity ({mean_rh:.0f}% mean RH)."
            )

    if mc_pct >= 20:
        lines.append(
            f"**Mechanical cooling** is required for {mc_pct:.0f}% of hours "
            f"(CDH = {cdh_total:,.0f} °C·h). High-performance glazing and external shading "
            f"are essential to minimise peak loads."
        )

    if nm_pct >= 8 or diurnal_range_mean >= 10:
        lines.append(
            f"The mean diurnal temperature range of {diurnal_range_mean:.1f} °C makes "
            f"**thermal mass with night-flushing** an effective strategy: "
            f"exposed heavyweight structure, night ventilation, and high-conductance internal surfaces are advised."
        )

    if heating_pct >= 15:
        lines.append(
            f"**Passive heating** measures — high solar-gain glazing, south-facing orientation, "
            f"and insulation — should address the {hdh_total:,.0f} °C·h heating demand."
        )

    return "  \n".join(lines)


# ─── Plotting helpers ─────────────────────────────────────────────────────────

def _psychrometric_background(fig: go.Figure, t_range=(-5, 50)) -> None:
    """
    Draw constant-RH lines and comfort zone polygons onto a Plotly figure.
    The figure x-axis is DBT (°C), y-axis is humidity ratio (g/kg dry air).
    """
    T_cont = np.linspace(t_range[0], t_range[1], 200)

    def _hr_at_rh(T_arr, rh_pct):
        """Humidity ratio [g/kg] at given RH% and temperature array."""
        e_s = 611.2 * np.exp(17.67 * T_arr / (T_arr + 243.5))
        e   = rh_pct / 100.0 * e_s
        return 1000.0 * 0.62198 * e / (_P_ATM - e)

    # ── Constant RH curves ────────────────────────────────────────────────────
    for rh in range(10, 110, 10):
        hr_line = _hr_at_rh(T_cont, rh)
        # Clip to realistic chart range
        mask = hr_line <= 30
        fig.add_trace(go.Scatter(
            x=T_cont[mask], y=hr_line[mask],
            mode="lines",
            line=dict(color="rgba(100,100,100,0.2)", width=0.8, dash="dot"),
            showlegend=False,
            hovertemplate=f"RH = {rh}%<br>T = %{{x:.1f}}°C<br>HR = %{{y:.2f}} g/kg<extra></extra>",
            name=f"RH {rh}%",
        ))
        # Label at T = 35 °C (or t_range end)
        label_T = min(38, t_range[1] - 2)
        label_hr = float(_hr_at_rh(np.array([label_T]), rh)[0])
        if 0 < label_hr <= 30:
            fig.add_annotation(
                x=label_T, y=label_hr,
                text=f"{rh}%",
                showarrow=False,
                font=dict(size=8, color="rgba(80,80,80,0.6)"),
                xanchor="left",
            )

    # ── ASHRAE 55 static comfort zone (approx polygon in psych space) ─────────
    # Zone: DBT 20–26 °C, operative RH 20–80 %, W capped at 0.012 kg/kg
    # Trace the perimeter of the ASHRAE 55 summer polygon (simplified)
    # Lower HR bound ~0.004 (RH ~20 % at 22 °C), upper W = 12 g/kg
    comfort_T  = [20, 26, 26, 20, 20]
    comfort_rh = [20, 20, 60, 60, 20]   # approximate bounding RH values
    comfort_hr = [
        float(_hr_at_rh(np.array([t]), rh)[0]) * 1000  # g/kg
        for t, rh in zip([20, 26, 26, 20, 20], [20, 20, 60, 60, 20])
    ]
    comfort_T_poly  = [20, 26, 26, 20, 20]
    comfort_hr_poly = [
        float(_hr_at_rh(np.array([20]), 20)[0]) * 1000,
        float(_hr_at_rh(np.array([26]), 20)[0]) * 1000,
        min(float(_hr_at_rh(np.array([26]), 60)[0]) * 1000, 12.0),
        min(float(_hr_at_rh(np.array([20]), 80)[0]) * 1000, 12.0),
        float(_hr_at_rh(np.array([20]), 20)[0]) * 1000,
    ]
    fig.add_trace(go.Scatter(
        x=comfort_T_poly, y=comfort_hr_poly,
        fill="toself",
        fillcolor="rgba(46,204,113,0.15)",
        line=dict(color="rgba(46,204,113,0.9)", width=1.5),
        name="ASHRAE 55 Comfort Zone",
        mode="lines",
        hoverinfo="skip",
    ))

    # ── Strategy region: Evaporative cooling (high T, low–medium RH) ─────────
    ev_T  = np.linspace(25, 45, 50)
    ev_lo = _hr_at_rh(ev_T, 0)   * 1000  # 0 % RH (floor)
    ev_hi = _hr_at_rh(ev_T, 35)  * 1000  # 35 % RH upper bound
    fig.add_trace(go.Scatter(
        x=np.concatenate([ev_T, ev_T[::-1]]),
        y=np.concatenate([ev_lo, ev_hi[::-1]]),
        fill="toself",
        fillcolor="rgba(26,188,156,0.10)",
        line=dict(color="rgba(26,188,156,0.5)", width=1),
        name="Evaporative Cooling Zone",
        mode="lines",
        hoverinfo="skip",
    ))

    # ── Strategy region: Natural ventilation (moderate T, moderate RH) ────────
    nv_T  = np.linspace(22, 32, 50)
    nv_lo = _hr_at_rh(nv_T, 20) * 1000
    nv_hi = _hr_at_rh(nv_T, 70) * 1000
    fig.add_trace(go.Scatter(
        x=np.concatenate([nv_T, nv_T[::-1]]),
        y=np.concatenate([nv_lo, nv_hi[::-1]]),
        fill="toself",
        fillcolor="rgba(39,174,96,0.08)",
        line=dict(color="rgba(39,174,96,0.4)", width=1),
        name="Natural Ventilation Zone",
        mode="lines",
        hoverinfo="skip",
    ))

    # ── Strategy region: Dehumidification (high W regardless of temperature) ──
    fig.add_hrect(
        y0=12, y1=30,
        fillcolor="rgba(155,89,182,0.06)",
        line_width=0,
        annotation_text="Dehumidification",
        annotation_position="top left",
        annotation_font=dict(size=9, color="rgba(100,60,140,0.7)"),
    )

    # ── Strategy region: Heating (left side, cold) ────────────────────────────
    heat_T  = [-5, 20, 20, -5, -5]
    heat_hr = [0, 0, 30, 30, 0]
    fig.add_trace(go.Scatter(
        x=heat_T, y=heat_hr,
        fill="toself",
        fillcolor="rgba(230,126,34,0.06)",
        line=dict(color="rgba(230,126,34,0.3)", width=1),
        name="Heating Zone",
        mode="lines",
        hoverinfo="skip",
    ))


# ─── Chart renderers ──────────────────────────────────────────────────────────

def build_psychrometric_chart_figure(df: pd.DataFrame, months: Optional[list] = None) -> go.Figure:
    """
    Build the interactive psychrometric chart with comfort zones.

    Expects df with dry_bulb_temperature, relative_humidity, month, datetime
    columns (enriched EPW data).  Pure — no Streamlit calls.
    """
    fdf = df[df["month"].isin(months)].copy() if months else df.copy()

    fdf = compute_psychrometric_data(fdf)
    # Convert HR from kg/kg → g/kg for plotting
    fdf["humidity_ratio_gkg"] = fdf["humidity_ratio"] * 1000.0

    # Colour scatter by DBT for visual richness
    fig = go.Figure()
    _psychrometric_background(fig)

    fig.add_trace(go.Scatter(
        x=fdf["dry_bulb_temperature"],
        y=fdf["humidity_ratio_gkg"],
        mode="markers",
        marker=dict(
            size=3,
            color=fdf["dry_bulb_temperature"],
            colorscale="RdYlBu_r",
            showscale=True,
            colorbar=dict(title="DBT (°C)", thickness=12, len=0.5),
            opacity=0.55,
        ),
        name="Hourly Data",
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "DBT: %{x:.1f}°C<br>"
            "HR: %{y:.2f} g/kg<br>"
            "RH: %{customdata[1]:.0f}%<extra></extra>"
        ),
        customdata=np.stack([
            fdf["datetime"].dt.strftime("%b %d %H:%M"),
            fdf["relative_humidity"],
        ], axis=1),
    ))

    fig.update_layout(
        title="Psychrometric Chart – Hourly Climate Data",
        xaxis_title="Dry Bulb Temperature (°C)",
        yaxis_title="Humidity Ratio (g/kg dry air)",
        xaxis=dict(range=[-5, 50], gridcolor="#f0f0f0"),
        yaxis=dict(range=[0, 28],  gridcolor="#f0f0f0"),
        height=520,
        template="plotly_white",
        legend=dict(orientation="v", x=1.08, y=1),
        margin=dict(r=160),
    )
    return fig


def _render_psychrometric_chart(df: pd.DataFrame, months: list) -> None:
    """Render the interactive psychrometric chart with comfort zones."""
    has_rh = "relative_humidity" in df.columns and df["relative_humidity"].notna().any()
    if not has_rh:
        st.warning("Relative humidity data not available — psychrometric chart disabled.")
        return

    fdf = df[df["month"].isin(months)].copy() if months else df.copy()
    if fdf.empty:
        st.info("No data for selected months.")
        return

    st.plotly_chart(build_psychrometric_chart_figure(fdf, months), use_container_width=True)


def build_monthly_comfort_breakdown_figure(df: pd.DataFrame) -> go.Figure:
    """
    Build the stacked monthly bar chart of comfort categories.

    Expects df with month and comfort_cat columns (from classify_comfort).
    Pure — no Streamlit calls.
    """
    monthly = (
        df.groupby(["month", "comfort_cat"])
        .size()
        .reset_index(name="hours")
    )
    monthly["month_name"] = monthly["month"].apply(lambda m: _MONTH_NAMES[m - 1])
    pivot = monthly.pivot(index="month", columns="comfort_cat", values="hours").fillna(0)
    # Normalize to % of hours per month
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    pivot_pct = pivot_pct.reindex(range(1, 13), fill_value=0)

    month_labels = [_MONTH_NAMES[m - 1] for m in pivot_pct.index]

    fig = go.Figure()
    for cat in _COMFORT_CATS:
        if cat not in pivot_pct.columns:
            continue
        fig.add_trace(go.Bar(
            name=cat,
            x=month_labels,
            y=pivot_pct[cat],
            marker_color=_COMFORT_COLORS[cat],
        ))

    fig.update_layout(
        barmode="stack",
        title="Monthly Comfort Breakdown (% of hours)",
        xaxis_title="Month",
        yaxis_title="% of Hours",
        yaxis=dict(range=[0, 100]),
        height=380,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _render_monthly_comfort_breakdown(df: pd.DataFrame) -> None:
    """Stacked monthly bar chart of comfort categories."""
    st.plotly_chart(build_monthly_comfort_breakdown_figure(df), use_container_width=True)


def build_comfort_heatmap_figure(df: pd.DataFrame) -> go.Figure:
    """
    Build the heatmap: hour of day × month, coloured by dominant comfort category.

    Expects df with month, hour and comfort_cat columns (from classify_comfort).
    Pure — no Streamlit calls.
    """
    # Encode categories as numeric for heatmap colour
    cat_order = {c: i for i, c in enumerate(_COMFORT_CATS)}
    df2 = df.copy()
    df2["cat_num"] = df2["comfort_cat"].map(cat_order).fillna(0)

    pivot = (
        df2.groupby(["month", "hour"])["cat_num"]
        .mean()
        .reset_index()
        .pivot(index="month", columns="hour", values="cat_num")
        .reindex(range(1, 13))
    )

    # Build custom colour scale
    cs = [
        [0.00, "#2ecc71"],   # Comfortable
        [0.25, "#e74c3c"],   # Too Hot
        [0.50, "#3498db"],   # Too Cold
        [0.75, "#9b59b6"],   # Too Humid
        [1.00, "#f39c12"],   # Too Dry
    ]

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{h:02d}:00" for h in range(24)],
        y=[_MONTH_NAMES[m - 1] for m in pivot.index],
        colorscale=cs,
        zmin=0, zmax=4,
        colorbar=dict(
            tickvals=[0, 1, 2, 3, 4],
            ticktext=_COMFORT_CATS,
            thickness=14, len=0.7,
        ),
        hovertemplate="Month: %{y}<br>Hour: %{x}<br>Dominant category: %{z:.2f}<extra></extra>",
    ))

    fig.update_layout(
        title="Comfort Heatmap – Hour × Month (mean category)",
        xaxis_title="Hour of Day",
        yaxis_title="Month",
        height=380,
        template="plotly_white",
    )
    return fig


def _render_comfort_heatmap(df: pd.DataFrame) -> None:
    """Heatmap: hour of day × month, coloured by dominant comfort category."""
    st.plotly_chart(build_comfort_heatmap_figure(df), use_container_width=True)


def build_strategy_chart_figure(df: pd.DataFrame) -> go.Figure:
    """
    Build the horizontal bar chart of strategy distribution.

    Expects df with a strategy column (from map_strategies).
    Pure — no Streamlit calls.
    """
    counts = df["strategy"].value_counts()
    total  = counts.sum()
    pcts   = (counts / total * 100).round(1)

    strategies = list(pcts.index)
    values     = list(pcts.values)
    colors     = [_STRATEGY_COLORS.get(s, "#95a5a6") for s in strategies]

    fig = go.Figure(go.Bar(
        x=values,
        y=strategies,
        orientation="h",
        marker_color=colors,
        text=[f"{v:.1f}%" for v in values],
        textposition="outside",
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    ))

    fig.update_layout(
        title="Passive Design Strategy Distribution",
        xaxis_title="% of Hours",
        xaxis=dict(range=[0, max(values) * 1.2 + 2]),
        height=360,
        template="plotly_white",
        margin=dict(l=220),
    )
    return fig


def _render_strategy_chart(df: pd.DataFrame) -> None:
    """Horizontal bar chart of strategy distribution."""
    st.plotly_chart(build_strategy_chart_figure(df), use_container_width=True)


def build_degree_hours_figure(degree: dict, months: list) -> go.Figure:
    """
    Build the monthly CDH / HDH grouped bar chart.

    Expects degree dict from compute_degree_hours (keys cdh_per_month,
    hdh_per_month).  Unselected months are greyed out (None values).
    Pure — no Streamlit calls.
    """
    month_labels = [_MONTH_NAMES[m - 1] for m in range(1, 13)]

    cdh = degree["cdh_per_month"].reindex(range(1, 13), fill_value=0)
    hdh = degree["hdh_per_month"].reindex(range(1, 13), fill_value=0)

    # Grey out unselected months
    cdh_selected = [float(cdh[m]) if m in months else None for m in range(1, 13)]
    hdh_selected = [float(hdh[m]) if m in months else None for m in range(1, 13)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Cooling Degree Hours (CDH)",
        x=month_labels,
        y=cdh_selected,
        marker_color="#e74c3c",
        hovertemplate="%{x}: %{y:,.0f} °C·h<extra>CDH</extra>",
    ))
    fig.add_trace(go.Bar(
        name="Heating Degree Hours (HDH)",
        x=month_labels,
        y=hdh_selected,
        marker_color="#3498db",
        hovertemplate="%{x}: %{y:,.0f} °C·h<extra>HDH</extra>",
    ))

    fig.update_layout(
        barmode="group",
        title=f"Degree Hours (CDH base {_CDH_BASE:.0f}°C | HDH base {_HDH_BASE:.0f}°C)",
        xaxis_title="Month",
        yaxis_title="Degree Hours (°C·h)",
        height=360,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _render_degree_hours_chart(degree: dict, months: list) -> None:
    """Monthly CDH / HDH stacked grouped bar chart."""
    st.plotly_chart(build_degree_hours_figure(degree, months), use_container_width=True)


def build_adaptive_comfort_chart_figure(df: pd.DataFrame, months: Optional[list] = None) -> go.Figure:
    """
    Build the adaptive comfort scatter: T_pma vs DBT with comfort bands.

    Expects df enriched by compute_adaptive_comfort (t_pma, t_comf,
    adaptive_applicable columns) with rows applicable to the adaptive model.
    Pure — no Streamlit calls.
    """
    fdf = df[df["month"].isin(months)].copy() if months else df.copy()
    appl = fdf[fdf["adaptive_applicable"]]

    # Sort for band line drawing
    sorted_pma = np.linspace(
        appl["t_pma"].min() - 1,
        appl["t_pma"].max() + 1,
        200,
    )
    comf_line  = ASHRAE_COMFORT_NEUTRAL_A * sorted_pma + ASHRAE_COMFORT_NEUTRAL_B

    fig = go.Figure()

    # 80 % band
    fig.add_trace(go.Scatter(
        x=np.concatenate([sorted_pma, sorted_pma[::-1]]),
        y=np.concatenate([comf_line + COMFORT_BAND_80_PCT, (comf_line - COMFORT_BAND_80_PCT)[::-1]]),
        fill="toself", fillcolor="rgba(46,204,113,0.12)",
        line=dict(color="rgba(46,204,113,0.5)", width=1),
        name="80% Acceptability Band",
        hoverinfo="skip",
    ))
    # 90 % band
    fig.add_trace(go.Scatter(
        x=np.concatenate([sorted_pma, sorted_pma[::-1]]),
        y=np.concatenate([comf_line + COMFORT_BAND_90_PCT, (comf_line - COMFORT_BAND_90_PCT)[::-1]]),
        fill="toself", fillcolor="rgba(46,204,113,0.22)",
        line=dict(color="rgba(46,204,113,0.8)", width=1.5),
        name="90% Acceptability Band",
        hoverinfo="skip",
    ))
    # Comfort line
    fig.add_trace(go.Scatter(
        x=sorted_pma, y=comf_line,
        mode="lines",
        line=dict(color="#27ae60", width=2, dash="dash"),
        name="Comfort Temperature",
    ))

    # Data scatter
    fig.add_trace(go.Scatter(
        x=appl["t_pma"],
        y=appl["dry_bulb_temperature"],
        mode="markers",
        marker=dict(
            size=3,
            color=appl["dry_bulb_temperature"],
            colorscale="RdYlBu_r",
            showscale=True,
            colorbar=dict(title="DBT (°C)", thickness=12, len=0.5),
            opacity=0.4,
        ),
        name="Hourly Data",
        hovertemplate=(
            "T_pma: %{x:.1f}°C<br>DBT: %{y:.1f}°C<extra></extra>"
        ),
    ))

    fig.update_layout(
        title="Adaptive Comfort – ASHRAE 55 (Prevailing Mean vs. Indoor Operative Temperature)",
        xaxis_title="Prevailing Mean Outdoor Temperature T_pma (°C)",
        yaxis_title="Dry Bulb Temperature (°C)",
        height=460,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _render_adaptive_comfort_chart(df: pd.DataFrame, months: list) -> None:
    """Scatter of adaptive comfort: T_pma vs DBT with comfort bands."""
    fdf = df[df["month"].isin(months)].copy() if months else df.copy()
    if fdf.empty or "t_comf" not in fdf.columns:
        return

    appl = fdf[fdf["adaptive_applicable"]]
    if appl.empty:
        st.info("Adaptive comfort model not applicable for this climate (T_pma outside 10–33.5 °C).")
        return

    st.plotly_chart(build_adaptive_comfort_chart_figure(fdf, months), use_container_width=True)


# ─── Comfort bubble chart ─────────────────────────────────────────────────────

def build_comfort_bubble_chart(df: pd.DataFrame, sample_every: int = 4) -> go.Figure:
    """
    Build a dynamic, climate-agnostic comfort bubble chart from EPW hourly data.

    Encoding
    --------
    X-axis  : Dry Bulb Temperature (°C) — range auto-fitted to the dataset ± 2 °C
    Y-axis  : Relative Humidity (%) — fixed 0–100 %
    Size    : Wind Speed (m/s), scaled to pixel diameter (min 5 px, max 30 px)
    Color   : 9-class Comfort Category (strict 3×3 temp × RH grid, fixed palette)
    Angle   : Wind Direction (0° = North / straight-up, clockwise) via marker_angle

    Each comfort category is rendered as a separate trace so the legend is
    interactive (click to isolate / hide a zone).

    Parameters
    ----------
    df           : EPW DataFrame with columns dry_bulb_temperature, relative_humidity,
                   wind_speed, wind_direction, datetime.
    sample_every : Downsample factor.  Default 4 → ≈2 200 pts from a full-year file,
                   which renders smoothly.  Set to 1 for full resolution.

    Returns
    -------
    go.Figure  (does NOT call st.plotly_chart — caller decides where to render)
    """
    wdf = classify_bubble_comfort(df).iloc[::sample_every].copy().reset_index(drop=True)

    T  = pd.to_numeric(wdf["dry_bulb_temperature"], errors="coerce")
    RH = pd.to_numeric(wdf["relative_humidity"],    errors="coerce")
    WS = pd.to_numeric(wdf["wind_speed"],           errors="coerce").fillna(0.0)
    WD = pd.to_numeric(wdf["wind_direction"],       errors="coerce").fillna(0.0)

    # Pixel diameter: min 5 (calm) → max 30 (strong wind)
    bubble_px = np.clip(WS * 3.5 + 5.0, 5.0, 30.0)

    # Dynamic x-axis — works for Arctic lows and tropical highs alike
    x_min = float(T.min()) - 2.0
    x_max = float(T.max()) + 2.0

    dt_str = (
        wdf["datetime"].dt.strftime("%b %d %H:%M")
        if "datetime" in wdf.columns
        else pd.Series([""] * len(wdf), dtype=str)
    )

    fig = go.Figure()

    # ── Zone reference grid ───────────────────────────────────────────────────
    # Vertical lines at temperature thresholds (only drawn if inside data range)
    for x_div, lbl in [(22.0, "22 °C"), (28.5, "28.5 °C")]:
        if x_min < x_div < x_max:
            fig.add_vline(
                x=x_div,
                line=dict(color="rgba(0,0,0,0.13)", width=1.0, dash="dot"),
                annotation_text=lbl,
                annotation_position="top left",
                annotation_font=dict(size=9, color="rgba(80,80,80,0.65)"),
            )

    # Horizontal lines at RH thresholds
    for y_div, lbl in [(30.0, "30% RH"), (70.0, "70% RH")]:
        fig.add_hline(
            y=y_div,
            line=dict(color="rgba(0,0,0,0.11)", width=1.0, dash="dot"),
            annotation_text=lbl,
            annotation_position="right",
            annotation_font=dict(size=9, color="rgba(80,80,80,0.65)"),
        )

    # ── One scatter trace per comfort category ────────────────────────────────
    # Rendered in _BUBBLE_CATS order so colder/greener zones sit beneath warmer ones
    for cat in _BUBBLE_CATS:
        mask = (wdf["bubble_comfort"] == cat).values
        if not mask.any():
            continue

        fig.add_trace(go.Scatter(
            x=T.values[mask],
            y=RH.values[mask],
            mode="markers",
            name=cat,
            marker=dict(
                symbol="triangle-up",
                size=bubble_px.values[mask],
                color=_BUBBLE_COLORS[cat],
                opacity=0.65,
                # marker_angle: 0° = pointing up (North).  EPW wind direction
                # is "direction FROM which wind blows" in the same convention,
                # so the direct mapping is correct.
                angle=WD.values[mask],
                line=dict(width=0.4, color="rgba(0,0,0,0.20)"),
            ),
            customdata=np.column_stack([
                WS.values[mask],
                WD.values[mask],
                dt_str.values[mask],
            ]),
            hovertemplate=(
                "<b>%{customdata[2]}</b><br>"
                "Temperature : %{x:.1f} °C<br>"
                "Humidity    : %{y:.0f} %<br>"
                "Wind Speed  : %{customdata[0]:.1f} m/s<br>"
                "Wind Dir    : %{customdata[1]:.0f}°"
                "<extra></extra>"
            ),
        ))

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        title="Temperature – Humidity – Wind Speed (Detailed Comfort Analysis)",
        xaxis=dict(
            title="Dry Bulb Temperature (°C)",
            range=[x_min, x_max],
            gridcolor="#f0f0f0",
            zeroline=True,
            zerolinecolor="rgba(0,0,0,0.18)",
            zerolinewidth=1.0,
        ),
        yaxis=dict(
            title="Relative Humidity (%)",
            range=[0, 100],
            gridcolor="#f0f0f0",
        ),
        template="plotly_white",
        height=580,
        legend=dict(
            title="Comfort Category",
            orientation="v",
            x=1.02,
            y=1.0,
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.10)",
            borderwidth=1,
        ),
        margin=dict(r=200, t=60, b=60),
        hovermode="closest",
    )

    return fig


def compute_comfort_bubble_stats(df: pd.DataFrame) -> dict:
    """
    Compute per-category hour counts for the comfort bubble grid.

    Returns dict with keys:
        total_hours – int, number of rows classified
        counts      – dict {category: hours} for all 9 bubble categories
        pcts        – dict {category: % of period} for all 9 bubble categories
    Pure — no Streamlit calls.
    """
    classified = classify_bubble_comfort(df)
    total_hrs  = len(classified)
    counts = {
        cat: int((classified["bubble_comfort"] == cat).sum())
        for cat in _BUBBLE_CATS
    }
    pcts = {
        cat: (counts[cat] / total_hrs * 100 if total_hrs > 0 else 0.0)
        for cat in _BUBBLE_CATS
    }
    return {"total_hours": total_hrs, "counts": counts, "pcts": pcts}


def _render_comfort_bubble_chart(
    df: pd.DataFrame,
    months: list,
    start_hour: int = 0,
    end_hour: int = 23,
) -> None:
    """Render the comfort bubble chart and per-category hour metrics."""
    fdf = df[df["month"].isin(months)].copy() if months else df.copy()
    if start_hour > 0 or end_hour < 23:
        fdf = fdf[fdf["hour"].between(start_hour, end_hour)]
    if fdf.empty:
        st.info("No data for the selected months/hours.")
        return

    st.caption(
        "Each marker represents one hourly observation.  "
        "**Size** = wind speed · **Direction** = wind origin (triangle points toward source, 0° = North).  "
        "Click legend entries to isolate comfort zones."
    )
    st.plotly_chart(build_comfort_bubble_chart(fdf), use_container_width=True)

    # ── Per-category hour counts ───────────────────────────────────────────────
    bubble_stats = compute_comfort_bubble_stats(fdf)

    st.markdown("#### Hours by Comfort Category")
    st.caption(
        "Columns = temperature zone (Cold / Comfort / Hot) · "
        "Rows = humidity zone (Humid → Moderate → Dry)"
    )

    # 3 × 3 grid matching the psychrographic grid orientation
    grid = [
        ["Cold & Humid", "Humid",        "Hot & Humid"],
        ["Cold",         "Comfortable",  "Hot"        ],
        ["Cold & Dry",   "Dry",          "Hot & Dry"  ],
    ]

    for row_cats in grid:
        cols = st.columns(3)
        for col, cat in zip(cols, row_cats):
            count = bubble_stats["counts"][cat]
            pct   = bubble_stats["pcts"][cat]
            color = _BUBBLE_COLORS[cat]
            with col:
                st.markdown(
                    f'<div style="background:white;padding:14px;border-radius:8px;'
                    f'border-left:4px solid {color};box-shadow:0 2px 4px rgba(0,0,0,0.08);'
                    f'text-align:center;margin-bottom:8px;">'
                    f'<div style="font-size:11px;font-weight:700;color:{color};'
                    f'text-transform:uppercase;letter-spacing:0.5px;">{cat}</div>'
                    f'<div style="font-size:22px;font-weight:700;color:#2c3e50;margin:6px 0;">'
                    f'{count:,} hrs</div>'
                    f'<div style="font-size:12px;color:#718096;">{pct:.1f}% of period</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ─── KPI card helper ──────────────────────────────────────────────────────────

def _kpi(label: str, value: str, meta: str = "") -> str:
    """Return an HTML KPI card string."""
    return (
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-meta">{meta}</div>'
        f'</div>'
    )


# ─── Main render entry point ──────────────────────────────────────────────────

def compute_thermal_comfort_stats(fdf: pd.DataFrame, degree: dict) -> dict:
    """
    Compute the KPI statistics shown on the thermal comfort dashboard cards.

    Expects fdf enriched by compute_adaptive_comfort → classify_comfort →
    map_strategies (and month/doy columns present), plus the degree dict
    from compute_degree_hours.  Pure — no Streamlit calls.

    Returns dict with keys:
        total_hours, pct_comfortable, pct_in_80, pct_in_90,
        strategy_pcts, cooling_pct, heating_pct, dominant_strategy,
        cdh_total, hdh_total, mean_rh, diurnal_range_mean, comfort_cat_pcts
    """
    total_hrs  = len(fdf)
    appl_mask  = fdf["adaptive_applicable"]
    appl_count = appl_mask.sum()

    pct_comfortable = (fdf["comfort_cat"] == "Comfortable").sum() / total_hrs * 100
    pct_in_80       = (fdf.loc[appl_mask, "in_80"].sum() / appl_count * 100) if appl_count else 0.0
    pct_in_90       = (fdf.loc[appl_mask, "in_90"].sum() / appl_count * 100) if appl_count else 0.0

    strategy_counts = fdf["strategy"].value_counts()
    strategy_pcts   = (strategy_counts / total_hrs * 100).to_dict()

    cdh_pct = strategy_pcts.get("Mechanical Cooling", 0) + strategy_pcts.get("Evaporative Cooling", 0)
    htg_pct = strategy_pcts.get("Heating", 0)

    dominant_strategy = strategy_counts.idxmax() if not strategy_counts.empty else "N/A"

    mean_rh        = float(fdf["relative_humidity"].mean()) if "relative_humidity" in fdf.columns else 50.0
    diurnal_range_mean = float(
        fdf.groupby("doy")["dry_bulb_temperature"]
        .apply(lambda x: x.max() - x.min())
        .mean()
    )

    comfort_cat_pcts = {
        cat: (fdf["comfort_cat"] == cat).sum() / total_hrs * 100
        for cat in _COMFORT_CATS
    }

    return {
        "total_hours":        total_hrs,
        "pct_comfortable":    pct_comfortable,
        "pct_in_80":          pct_in_80,
        "pct_in_90":          pct_in_90,
        "strategy_pcts":      strategy_pcts,
        "cooling_pct":        cdh_pct,
        "heating_pct":        htg_pct,
        "dominant_strategy":  dominant_strategy,
        "cdh_total":          degree["cdh_total"],
        "hdh_total":          degree["hdh_total"],
        "mean_rh":            mean_rh,
        "diurnal_range_mean": diurnal_range_mean,
        "comfort_cat_pcts":   comfort_cat_pcts,
    }


def render(
    df: pd.DataFrame,
    months: Optional[list[int]] = None,
    comfort_model: str = "Both",
    air_speed_adjust: bool = False,
    start_hour: int = 0,
    end_hour: int = 23,
) -> None:
    """
    Render all Thermal Comfort visualisations and design summary.

    Parameters
    ----------
    df               : EPW DataFrame (output of parse_epw + derived columns from analysis.py)
    months           : list of month ints (1–12) to include; None = all
    comfort_model    : "Adaptive", "Static", or "Both"
    air_speed_adjust : If True, raise upper comfort limit by 1.5 °C when wind_speed > 1.5 m/s
    """
    if months is None:
        months = list(range(1, 13))

    # ── Ensure required derived columns ──────────────────────────────────────
    if "month" not in df.columns:
        df = df.copy()
        df["month"] = df["datetime"].dt.month
    if "hour" not in df.columns:
        df = df.copy()
        df["hour"] = df["datetime"].dt.hour
    if "doy" not in df.columns:
        df = df.copy()
        df["doy"] = df["datetime"].dt.dayofyear

    # ── Run computations on full-year data (running mean needs context) ───────
    df_full  = compute_adaptive_comfort(df)
    df_full  = classify_comfort(df_full)
    df_full  = map_strategies(df_full)

    # Optional: wind-speed adjustment raises upper comfort bound
    if air_speed_adjust:
        ws = pd.to_numeric(df_full["wind_speed"], errors="coerce").fillna(0)
        df_full.loc[ws > 1.5, "t_comf_80_hi"] += 1.5
        df_full.loc[ws > 1.5, "t_comf_90_hi"] += 1.5
        # Re-classify in_80 / in_90 after adjustment
        T = df_full["dry_bulb_temperature"]
        df_full["in_80"] = (
            (T >= df_full["t_comf_80_lo"]) &
            (T <= df_full["t_comf_80_hi"]) &
            df_full["adaptive_applicable"]
        )
        df_full["in_90"] = (
            (T >= df_full["t_comf_90_lo"]) &
            (T <= df_full["t_comf_90_hi"]) &
            df_full["adaptive_applicable"]
        )

    # ── Filter to selected months after computing running mean ────────────────
    fdf = df_full[df_full["month"].isin(months)].copy()

    if fdf.empty:
        st.warning("No data available for the selected months.")
        return

    # ── Statistics ────────────────────────────────────────────────────────────
    degree = compute_degree_hours(df_full)   # full year CDH/HDH for context
    stats  = compute_thermal_comfort_stats(fdf, degree)

    pct_comfortable    = stats["pct_comfortable"]
    pct_in_80          = stats["pct_in_80"]
    pct_in_90          = stats["pct_in_90"]
    strategy_pcts      = stats["strategy_pcts"]
    cdh_pct            = stats["cooling_pct"]
    htg_pct            = stats["heating_pct"]
    dominant_strategy  = stats["dominant_strategy"]
    mean_rh            = stats["mean_rh"]
    diurnal_range_mean = stats["diurnal_range_mean"]

    # ── Section title ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-title">🌡️ Thermal Comfort Analysis</div>', unsafe_allow_html=True)

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6, gap="small")

    cards = [
        (k1, _kpi("Comfortable Hours",     f"{pct_comfortable:.1f}%", "Static zone 22–26°C / 30–60% RH")),
        (k2, _kpi("Adaptive 80%",          f"{pct_in_80:.1f}%",       "ASHRAE 55 – 80% acceptability")),
        (k3, _kpi("Adaptive 90%",          f"{pct_in_90:.1f}%",       "ASHRAE 55 – 90% acceptability")),
        (k4, _kpi("Cooling Required",      f"{cdh_pct:.1f}%",         f"CDH = {degree['cdh_total']:,.0f} °C·h")),
        (k5, _kpi("Heating Required",      f"{htg_pct:.1f}%",         f"HDH = {degree['hdh_total']:,.0f} °C·h")),
        (k6, _kpi("Dominant Strategy",     dominant_strategy,          "")),
    ]
    for col, html in cards:
        with col:
            st.markdown(html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tab navigation ────────────────────────────────────────────────────────
    tab_key = "tc_active_tab"
    if tab_key not in st.session_state:
        st.session_state[tab_key] = "Psychrometric Chart"

    tc_tabs = [
        "Psychrometric Chart",
        "Adaptive Comfort",
        "Monthly Breakdown",
        "Comfort Heatmap",
        "Strategy Map",
        "Degree Hours",
        "Comfort Bubble",
    ]
    tabs = st.tabs(tc_tabs)
    (
        tab_psychrometric,
        tab_adaptive,
        tab_monthly,
        tab_heatmap,
        tab_strategy,
        tab_degrees,
        tab_bubble,
    ) = tabs

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tab content ───────────────────────────────────────────────────────────
    with tab_psychrometric:
        _render_psychrometric_chart(fdf, months)

    with tab_adaptive:
        if comfort_model in ("Adaptive", "Both"):
            _render_adaptive_comfort_chart(fdf, months)
        else:
            st.info("Adaptive comfort model is not selected. Use the sidebar to enable it.")

    with tab_monthly:
        _render_monthly_comfort_breakdown(fdf)

    with tab_heatmap:
        _render_comfort_heatmap(fdf)

    with tab_strategy:
        _render_strategy_chart(fdf)

    with tab_degrees:
        _render_degree_hours_chart(degree, months)

    with tab_bubble:
        try:
            _render_comfort_bubble_chart(fdf, months, start_hour=start_hour, end_hour=end_hour)
        except Exception as _bubble_err:
            st.error(f"Comfort Bubble chart error: {_bubble_err}")
            import traceback
            st.code(traceback.format_exc())

    # ── Comfort breakdown summary ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Comfort Category Distribution")
    cc1, cc2, cc3, cc4, cc5 = st.columns(5, gap="small")
    for col, cat in zip([cc1, cc2, cc3, cc4, cc5], _COMFORT_CATS):
        pct = stats["comfort_cat_pcts"][cat]
        with col:
            st.markdown(
                _kpi(cat, f"{pct:.1f}%", ""),
                unsafe_allow_html=True,
            )

    # ── Design recommendation ─────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🧠 Design Recommendation")
    summary = generate_design_summary(
        pct_comfortable=pct_comfortable,
        pct_in_80=pct_in_80,
        pct_in_90=pct_in_90,
        strategy_pcts=strategy_pcts,
        cdh_total=degree["cdh_total"],
        hdh_total=degree["hdh_total"],
        mean_rh=mean_rh,
        diurnal_range_mean=diurnal_range_mean,
    )
    st.info(summary)
