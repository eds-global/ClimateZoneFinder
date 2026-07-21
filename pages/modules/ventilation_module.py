"""Natural Ventilation Potential module – climate-to-design bridge.

Evaluates three complementary passive ventilation strategies from EPW data:
  1. Cross Ventilation  – wind-driven, facade alignment is the key design decision
  2. Stack Ventilation  – buoyancy-driven, exploits outdoor-to-indoor temperature differential
  3. Night Flushing     – pre-cools thermal mass during cool nights that follow hot days

Design guidance output:
  • Actionable facade orientation recommendation
  • Per-strategy % of annual/seasonal usable hours
  • Simplified Air Changes per Hour (ACH) indicator
  • Monthly breakdown for seasonal design decisions

Exposes:
    render(epw_df, ...)  ← called from pages/analysis.py

Architecture notes
------------------
• All wind-direction arithmetic reuses the same centred-bin algorithm as
  wind_module.py (shift by half sector, floor-divide, modulo).
• Calm hours (< 0.5 m/s, WMO convention) are excluded from cross-ventilation
  counts but included in stack-ventilation counts (calm conditions favour
  buoyancy-driven flow).
• ACH is a simplified proxy model; it is NOT a CFD result. It provides a
  relative indicator, not an absolute engineering value.
"""

import re
import numpy as np
from typing import Optional
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from .st_compat import st


# ─── Constants ────────────────────────────────────────────────────────────────

_DIR_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# Canonical opposing facade pairs for cross-ventilation analysis.
# Tuple: (facade_A, facade_B, human-readable axis label)
_FACADE_PAIRS = [
    ("N",  "S",  "N–S axis"),
    ("E",  "W",  "E–W axis"),
    ("NE", "SW", "NE–SW axis"),
    ("NW", "SE", "NW–SE axis"),
]

# Maps each 8-sector direction to its cross-ventilation opposite.
_OPPOSITE_8 = {
    "N": "S",  "NE": "SW", "E": "W",  "SE": "NW",
    "S": "N",  "SW": "NE", "W": "E",  "NW": "SE",
}

# Maps 16-sector labels (from wind_module) to nearest 8-sector equivalent.
_MAP_16_TO_8 = {
    "N": "N", "NNE": "N",  "NE": "NE", "ENE": "E",
    "E": "E", "ESE": "E",  "SE": "SE", "SSE": "S",
    "S": "S", "SSW": "S",  "SW": "SW", "WSW": "W",
    "W": "W", "WNW": "W",  "NW": "NW", "NNW": "N",
}

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# ACH simplified model constants.
# Formula: ACH ≈ wind_speed × _ACH_OPENING × _ACH_EFFCY × _ACH_SCALE
# _ACH_SCALE = 10  normalises the proxy to a representative room geometry:
#   room 6 m × 6 m × 3 m, discharge coeff Cd = 0.5,
#   A_open = opening_factor × wall_area.
# At mean urban wind speed of 3 m/s → ACH ≈ 3.75 (Good range).
_ACH_OPENING  = 0.25   # fraction of wall area assumed open
_ACH_EFFCY    = 0.50   # wind-to-flow effectiveness coefficient
_ACH_SCALE    = 10.0   # normalisation to practical ACH range

# Brand-consistent colour palette (matches wind_module / dbt_module)
_C_CROSS  = "#3b82f6"   # blue   – cross ventilation
_C_STACK  = "#10b981"   # green  – stack ventilation
_C_NIGHT  = "#6366f1"   # indigo – night flushing
_C_TOTAL  = "#f59e0b"   # amber  – combined / total
_C_ACH    = "#ef4444"   # red    – poor ACH
_C_ORIENT = "#8b5cf6"   # purple – orientation banner


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _kpi(label: str, value: str, color: str) -> str:
    """Return HTML for a bordered KPI card (matches wind_module style)."""
    return (
        f'<div style="background:white;padding:14px;border-radius:8px;'
        f'border-left:4px solid {color};'
        f'box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">'
        f'<div style="font-size:11px;font-weight:700;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:#2c3e50;">{value}</div>'
        f'</div>'
    )


def _bold(text: str) -> str:
    """Convert **markdown bold** to <b>HTML bold</b>."""
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)


# ─── Data preparation ─────────────────────────────────────────────────────────

def prepare_ventilation_data(
    df: pd.DataFrame,
    months: list = None,
) -> pd.DataFrame:
    """Enrich EPW DataFrame with ventilation-relevant derived columns.

    Steps
    -----
    1. Extract / confirm time components (month, hour, doy).
    2. Sanitise wind_speed and wind_direction.
    3. Flag calm hours (< 0.5 m/s, WMO/ASHRAE convention).
    4. Assign 8-sector compass labels (centred-bin algorithm).
    5. Add is_day (6–17) / is_night (18–5) boolean flags.
    6. Apply optional month filter.

    Returns enriched copy of the DataFrame.
    """
    vdf = df.copy()

    # ── Time components ───────────────────────────────────────────────────────
    if "datetime" in vdf.columns:
        dt = pd.to_datetime(vdf["datetime"])
        if "month" not in vdf.columns:
            vdf["month"] = dt.dt.month
        if "hour" not in vdf.columns:
            vdf["hour"] = dt.dt.hour
        if "doy" not in vdf.columns:
            vdf["doy"] = dt.dt.dayofyear

    # ── Wind sanitisation (EPW uses 999 for missing; clip to physical range) ──
    vdf["wind_speed"] = (
        pd.to_numeric(vdf["wind_speed"], errors="coerce")
        .fillna(0.0)
        .clip(lower=0.0, upper=50.0)
    )
    vdf["wind_direction"] = (
        pd.to_numeric(vdf["wind_direction"], errors="coerce")
        .fillna(0.0)
        % 360.0
    )

    # Temperature – impute missing with column median
    vdf["dry_bulb_temperature"] = pd.to_numeric(
        vdf["dry_bulb_temperature"], errors="coerce"
    )
    _t_med = vdf["dry_bulb_temperature"].median()
    vdf["dry_bulb_temperature"] = vdf["dry_bulb_temperature"].fillna(
        float(_t_med) if pd.notna(_t_med) else 20.0
    )

    # ── Calm flag (WMO threshold) ─────────────────────────────────────────────
    vdf["is_calm"] = vdf["wind_speed"] < 0.5

    # ── 8-sector direction label (centred-bin algorithm) ─────────────────────
    # Each sector spans 45°, centred on its midpoint angle.
    # Shifting by 22.5° before floor-dividing places North at the centre of
    # sector 0, eliminating the 0°/360° wrap-around boundary artefact.
    sw = 45.0
    shifted = (vdf["wind_direction"] + sw / 2.0) % 360.0
    vdf["_sec8"] = (shifted / sw).astype(int) % 8
    vdf["dir_8"] = vdf["_sec8"].map(dict(enumerate(_DIR_8)))

    # ── Day / Night flags ─────────────────────────────────────────────────────
    # is_day  : 06:00–17:59 (daytime occupancy / heating period)
    # is_night: 18:00–05:59 (potential thermal mass pre-cooling window)
    vdf["is_day"]   = vdf["hour"].between(6, 17)
    vdf["is_night"] = ~vdf["is_day"]

    # ── Month filter ──────────────────────────────────────────────────────────
    if months:
        vdf = vdf[vdf["month"].isin(months)].copy()

    return vdf.drop(columns=["_sec8"], errors="ignore").reset_index(drop=True)


# ─── Core computation functions ───────────────────────────────────────────────

def compute_cross_ventilation(
    vdf: pd.DataFrame,
    wind_threshold: float = 1.5,
) -> dict:
    """Evaluate cross-ventilation potential.

    A cross-ventilation hour requires:
      • wind_speed > wind_threshold  (default 1.5 m/s)
      • wind is NOT calm (< 0.5 m/s excluded)

    The dominant facade pair is identified by tallying how many wind-usable
    hours arrive from each pair's two opposing directions.  The pair capturing
    the most hours is the recommended axis.

    Direction consistency:
      dominant_sector_pct > 30 % → Strong  (reliable design basis)
      dominant_sector_pct > 20 % → Moderate
      else                       → Weak    (multi-directional climate)

    Returns
    -------
    dict with keys:
        best_pair_label, best_pair_pct, cross_hours_pct,
        prevailing_dir, dominant_sector_pct, consistency,
        pair_data [(fa, fb, label, pct), ...],
        monthly_pct  pd.Series indexed 1–12
    """
    total = len(vdf)
    if total == 0:
        return _empty_cross()

    active = vdf[~vdf["is_calm"]]
    if active.empty:
        return _empty_cross()

    # Prevailing direction = most frequent 8-sector among non-calm hours
    dir_counts   = active["dir_8"].value_counts()
    prevailing   = str(dir_counts.idxmax())
    dominant_pct = float(dir_counts.iloc[0]) / total * 100.0

    consistency = (
        "Strong"   if dominant_pct > 30 else
        "Moderate" if dominant_pct > 20 else
        "Weak"
    )

    # Wind-usable mask (speed > threshold AND not calm)
    wind_ok = (vdf["wind_speed"] > wind_threshold) & ~vdf["is_calm"]

    # Per-pair analysis
    best_pair_tuple = _FACADE_PAIRS[0]
    best_pct        = 0.0
    pair_data       = []

    for fa, fb, ax_label in _FACADE_PAIRS:
        from_pair = (vdf["dir_8"] == fa) | (vdf["dir_8"] == fb)
        pct = float((wind_ok & from_pair).sum()) / total * 100.0
        pair_data.append((fa, fb, ax_label, pct))
        if pct > best_pct:
            best_pct        = pct
            best_pair_tuple = (fa, fb, ax_label)

    # Total cross-ventilation usability = any wind-usable hour (any direction)
    cross_pct = float(wind_ok.sum()) / total * 100.0

    monthly = (
        vdf.assign(_cu=wind_ok.astype(int))
        .groupby("month")["_cu"]
        .mean()
        .mul(100)
        .reindex(range(1, 13), fill_value=0.0)
    )

    return dict(
        best_pair_label     = f"{best_pair_tuple[0]}–{best_pair_tuple[1]}",
        best_pair_pct       = round(best_pct, 1),
        cross_hours_pct     = round(cross_pct, 1),
        prevailing_dir      = prevailing,
        dominant_sector_pct = round(dominant_pct, 1),
        consistency         = consistency,
        pair_data           = pair_data,
        monthly_pct         = monthly,
    )


def _empty_cross() -> dict:
    monthly = pd.Series(0.0, index=range(1, 13))
    return dict(
        best_pair_label="N–S", best_pair_pct=0.0,
        cross_hours_pct=0.0, prevailing_dir="N/A",
        dominant_sector_pct=0.0, consistency="N/A",
        pair_data=[], monthly_pct=monthly,
    )


def compute_stack_ventilation(
    vdf: pd.DataFrame,
    comfort_min: float = 24.0,
    comfort_max: float = 26.0,
) -> dict:
    """Evaluate stack (buoyancy-driven) ventilation potential.

    Conditions for a stack-usable hour:
      • outdoor_temp < comfort_max  – outdoor air is cooler than assumed indoor
                                      temperature, creating a buoyancy gradient
      • wind_speed < 1.5 m/s       – low wind favours buoyancy dominance over
                                      wind-pressure ventilation

    Qualitative strength is based on the mean temperature driving force
    ΔT = comfort_max − outdoor_temp (when conditions are met):
      ΔT > 6 °C → High
      ΔT > 3 °C → Medium
      ΔT ≤ 3 °C → Low

    Returns
    -------
    dict with keys: stack_hours_pct, mean_delta_t, strength, monthly_pct
    """
    total = len(vdf)
    if total == 0:
        return dict(
            stack_hours_pct=0.0, mean_delta_t=0.0,
            strength="N/A", monthly_pct=pd.Series(dtype=float),
        )

    low_wind   = vdf["wind_speed"] < 1.5
    cool_out   = vdf["dry_bulb_temperature"] < comfort_max
    stack_mask = low_wind & cool_out

    delta_t = (comfort_max - vdf.loc[stack_mask, "dry_bulb_temperature"]).clip(lower=0)
    mean_dt = float(delta_t.mean()) if not delta_t.empty else 0.0

    strength = (
        "High"   if mean_dt > 6 else
        "Medium" if mean_dt > 3 else
        "Low"
    )

    stack_pct = float(stack_mask.sum()) / total * 100.0

    monthly = (
        vdf.assign(_su=stack_mask.astype(int))
        .groupby("month")["_su"]
        .mean()
        .mul(100)
        .reindex(range(1, 13), fill_value=0.0)
    )

    return dict(
        stack_hours_pct = round(stack_pct, 1),
        mean_delta_t    = round(mean_dt, 2),
        strength        = strength,
        monthly_pct     = monthly,
    )


def compute_night_flushing(
    vdf: pd.DataFrame,
    hot_day_threshold: float = 30.0,
    night_cool_threshold: float = 26.0,
) -> dict:
    """Evaluate night-flushing (pre-cooling) potential.

    A night hour is 'flush-usable' if ALL three conditions hold:
      1. is_night == True (hour 18–05)
      2. outdoor_temp < night_cool_threshold  (cool enough to absorb heat)
      3. that calendar day's peak temperature > hot_day_threshold
         (significant daytime heat buildup occurred: flushing is worthwhile)

    The diurnal temperature range (daily_max − nightly_min) quantifies the
    available thermal gradient; the effectiveness score (0–10) is half of
    this value, capped at 10.

    Returns
    -------
    dict with keys:
        night_flush_pct, hot_days_pct, mean_diurnal_range,
        effectiveness_score, monthly_pct
    """
    total = len(vdf)
    if total == 0:
        return _empty_night()

    tmp = vdf.copy()

    # Ensure doy column exists
    if "doy" not in tmp.columns:
        if "datetime" in tmp.columns:
            tmp["doy"] = pd.to_datetime(tmp["datetime"]).dt.dayofyear
        else:
            tmp["doy"] = ((tmp.index // 24) % 365) + 1

    # Daily statistics – use map() for safe join without index duplication
    daily_max_map      = tmp.groupby("doy")["dry_bulb_temperature"].max().to_dict()
    daily_night_min    = (
        tmp[tmp["is_night"]]
        .groupby("doy")["dry_bulb_temperature"]
        .min()
        .to_dict()
    )

    tmp["daily_max"]       = tmp["doy"].map(daily_max_map)
    tmp["daily_night_min"] = tmp["doy"].map(daily_night_min)

    # Night-flushing flag
    night_mask = (
        tmp["is_night"]
        & (tmp["dry_bulb_temperature"] < night_cool_threshold)
        & (tmp["daily_max"] > hot_day_threshold)
    )

    night_pct = float(night_mask.sum()) / total * 100.0

    # Hot-day proportion (context for how often heat buildup requires flushing)
    total_days   = len(daily_max_map)
    hot_days_n   = sum(1 for v in daily_max_map.values() if v > hot_day_threshold)
    hot_days_pct = hot_days_n / total_days * 100.0 if total_days > 0 else 0.0

    # Diurnal range across all days with both day and night data
    diurnal_values = [
        daily_max_map[d] - daily_night_min[d]
        for d in daily_max_map
        if d in daily_night_min and pd.notna(daily_max_map[d]) and pd.notna(daily_night_min[d])
    ]
    mean_diurnal = float(np.mean(diurnal_values)) if diurnal_values else 0.0

    # Effectiveness score: larger diurnal swing → better pre-cooling potential
    effectiveness = min(10.0, mean_diurnal / 2.0)

    monthly = (
        tmp.assign(_nu=night_mask.astype(int))
        .groupby("month")["_nu"]
        .mean()
        .mul(100)
        .reindex(range(1, 13), fill_value=0.0)
    )

    return dict(
        night_flush_pct     = round(night_pct, 1),
        hot_days_pct        = round(hot_days_pct, 1),
        mean_diurnal_range  = round(mean_diurnal, 1),
        effectiveness_score = round(effectiveness, 1),
        monthly_pct         = monthly,
    )


def _empty_night() -> dict:
    monthly = pd.Series(0.0, index=range(1, 13))
    return dict(
        night_flush_pct=0.0, hot_days_pct=0.0,
        mean_diurnal_range=0.0, effectiveness_score=0.0,
        monthly_pct=monthly,
    )


def compute_ach(
    vdf: pd.DataFrame,
    opening_factor: float = _ACH_OPENING,
    effectiveness: float  = _ACH_EFFCY,
) -> dict:
    """Simplified Air Changes per Hour (ACH) indicator.

    Formula
    -------
        ACH ≈ wind_speed × opening_factor × effectiveness × 10

    The scaling constant 10 normalises the result to a representative single-
    zone room (6 m × 6 m × 3 m, discharge coefficient Cd = 0.5), producing
    output in the practical 0–15 ACH range for urban wind speeds.

    Classification
    --------------
        ACH < 1  → Poor      (inadequate for comfort cooling)
        1 ≤ ACH ≤ 3 → Moderate  (adequate for mild climates)
        ACH > 3  → Good      (strong natural ventilation potential)

    Returns
    -------
    dict with keys:
        mean_ach, category, category_color,
        ach_poor_pct, ach_moderate_pct, ach_good_pct
    """
    if vdf.empty:
        return dict(
            mean_ach=0.0, category="Poor", category_color=_C_ACH,
            ach_poor_pct=100.0, ach_moderate_pct=0.0, ach_good_pct=0.0,
        )

    ach  = vdf["wind_speed"] * opening_factor * effectiveness * _ACH_SCALE
    n    = len(ach)
    mean = float(ach.mean())

    poor_pct     = float((ach < 1).sum())               / n * 100.0
    moderate_pct = float(((ach >= 1) & (ach <= 3)).sum()) / n * 100.0
    good_pct     = float((ach > 3).sum())               / n * 100.0

    if mean < 1:
        category, cat_color = "Poor",     _C_ACH
    elif mean <= 3:
        category, cat_color = "Moderate", _C_TOTAL
    else:
        category, cat_color = "Good",     _C_STACK

    return dict(
        mean_ach         = round(mean, 2),
        category         = category,
        category_color   = cat_color,
        ach_poor_pct     = round(poor_pct, 1),
        ach_moderate_pct = round(moderate_pct, 1),
        ach_good_pct     = round(good_pct, 1),
    )


def generate_orientation_recommendation(
    prevailing_dir: str,
    cross_stats: dict,
) -> dict:
    """Generate an actionable façade orientation recommendation.

    Maps the prevailing wind direction (8-sector) to the optimal cross-
    ventilation axis and writes a plain-language design guidance string.

    Returns
    -------
    dict with keys: axis, primary_facade, secondary_facade, narrative
    """
    if prevailing_dir in ("N/A", "", None):
        return dict(
            axis="N/A", primary_facade="N/A", secondary_facade="N/A",
            narrative="Insufficient wind data for an orientation recommendation.",
        )

    # Resolve 16-sector input (e.g. from wind_module) to 8-sector
    dir8     = _MAP_16_TO_8.get(prevailing_dir, prevailing_dir)
    opp8     = _OPPOSITE_8.get(dir8, "N/A")

    # Identify canonical pair label
    axis = f"{dir8}–{opp8}"
    for fa, fb, ax_label in _FACADE_PAIRS:
        if {fa, fb} == {dir8, opp8}:
            axis = ax_label
            break

    dominant_pct = cross_stats.get("dominant_sector_pct", 0.0)
    consistency  = cross_stats.get("consistency", "")
    best_pct     = cross_stats.get("best_pair_pct", 0.0)
    best_label   = cross_stats.get("best_pair_label", axis)

    narrative = (
        f"Best orientation for openings: **{axis}**. "
        f"Place primary inlets on the **{dir8} façade** — "
        f"{dominant_pct:.0f}% of non-calm hours arrive from this direction "
        f"(consistency: **{consistency}**). "
        f"Place cross-flow outlets on the **{opp8} façade**. "
        f"The best cross-ventilation axis (**{best_label}**) captures "
        f"**{best_pct:.1f}%** of total annual hours with active wind pressure."
    )

    return dict(
        axis             = axis,
        primary_facade   = dir8,
        secondary_facade = opp8,
        narrative        = narrative,
    )


# ─── Internal: build combined usability flags ─────────────────────────────────

def _build_usability_flags(
    vdf: pd.DataFrame,
    wind_threshold: float,
    comfort_max: float,
    hot_day_threshold: float,
    night_cool_threshold: float,
) -> pd.DataFrame:
    """Return a copy of vdf with per-strategy and combined boolean flags.

    Columns added
    -------------
    cross_vent  – cross-ventilation usable hour
    stack_vent  – stack-ventilation usable hour
    night_vent  – night-flushing usable hour
    any_vent    – any strategy applicable
    """
    tmp = vdf.copy()

    # Ensure doy for daily grouping
    if "doy" not in tmp.columns:
        if "datetime" in tmp.columns:
            tmp["doy"] = pd.to_datetime(tmp["datetime"]).dt.dayofyear
        else:
            tmp["doy"] = ((tmp.index // 24) % 365) + 1

    tmp["cross_vent"] = (tmp["wind_speed"] > wind_threshold) & ~tmp["is_calm"]
    tmp["stack_vent"] = (tmp["wind_speed"] < 1.5) & (tmp["dry_bulb_temperature"] < comfort_max)

    daily_max_map = tmp.groupby("doy")["dry_bulb_temperature"].max().to_dict()
    tmp["_dmax"]  = tmp["doy"].map(daily_max_map)
    tmp["night_vent"] = (
        tmp["is_night"]
        & (tmp["dry_bulb_temperature"] < night_cool_threshold)
        & (tmp["_dmax"] > hot_day_threshold)
    )

    tmp["any_vent"] = tmp["cross_vent"] | tmp["stack_vent"] | tmp["night_vent"]
    return tmp.drop(columns=["_dmax"], errors="ignore")


# ─── Plot functions ───────────────────────────────────────────────────────────

def plot_ventilation_heatmap(
    vdf: pd.DataFrame,
    wind_threshold: float = 1.5,
    comfort_max: float    = 26.0,
    hot_day_threshold: float = 30.0,
    night_cool_threshold: float = 26.0,
) -> go.Figure:
    """Heatmap of combined ventilation usability: Month (y) × Hour (x).

    Each cell = % of hours in that Month–Hour slot where ANY ventilation
    strategy (cross / stack / night-flush) is applicable.
    Colour scale: Red (0%) → Yellow → Green (100%).
    """
    tmp = _build_usability_flags(
        vdf, wind_threshold, comfort_max, hot_day_threshold, night_cool_threshold
    )

    pivot = (
        tmp.groupby(["month", "hour"])["any_vent"]
        .mean()
        .mul(100)
        .unstack(level="hour")
        .reindex(range(1, 13))
        .fillna(0)
    )

    # Only label months that exist in the filtered data
    month_labels = [_MONTH_NAMES[int(m) - 1] for m in pivot.index]

    fig = px.imshow(
        pivot,
        labels=dict(x="Hour of Day", y="Month", color="% Usable"),
        title="Combined Ventilation Potential – Month × Hour",
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
        aspect="auto",
        y=month_labels,
    )
    fig.update_layout(
        height=440,
        template="plotly_white",
        xaxis=dict(title="Hour of Day", tickmode="linear", dtick=3),
        yaxis=dict(title=""),
        coloraxis_colorbar=dict(
            title="% Hours<br>Usable",
            ticksuffix="%",
            thickness=14,
        ),
    )
    return fig


def plot_monthly_strategy_breakdown(
    cross: dict,
    stack: dict,
    night: dict,
) -> go.Figure:
    """Grouped bar chart: monthly cross / stack / night-flush potential (%).

    Shows seasonal rhythm of each strategy — essential for deciding which
    strategy dominates in which part of the year.
    """
    months = list(range(1, 13))
    labels = _MONTH_NAMES

    def _safe(series, m):
        try:
            return float(series.get(m, 0.0)) if hasattr(series, "get") else 0.0
        except Exception:
            return 0.0

    c_vals = [_safe(cross.get("monthly_pct", {}), m) for m in months]
    s_vals = [_safe(stack.get("monthly_pct", {}), m) for m in months]
    n_vals = [_safe(night.get("monthly_pct", {}), m) for m in months]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Cross Ventilation", x=labels, y=c_vals,
        marker_color=_C_CROSS, opacity=0.85,
        hovertemplate="%{x}: %{y:.1f}%<extra>Cross</extra>",
    ))
    fig.add_trace(go.Bar(
        name="Stack Ventilation", x=labels, y=s_vals,
        marker_color=_C_STACK, opacity=0.85,
        hovertemplate="%{x}: %{y:.1f}%<extra>Stack</extra>",
    ))
    fig.add_trace(go.Bar(
        name="Night Flushing", x=labels, y=n_vals,
        marker_color=_C_NIGHT, opacity=0.85,
        hovertemplate="%{x}: %{y:.1f}%<extra>Night Flush</extra>",
    ))

    fig.update_layout(
        title=dict(text="Monthly Ventilation Strategy Breakdown", font_size=16),
        barmode="group",
        xaxis_title="Month",
        yaxis=dict(title="% of Hours Usable", ticksuffix="%", range=[0, 110]),
        height=420,
        template="plotly_white",
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
        bargap=0.15,
    )
    return fig


def plot_wind_ventilation_usability(
    vdf: pd.DataFrame,
    wind_threshold: float = 1.5,
) -> go.Figure:
    """Bar chart: cross-ventilation usable hours by 8-sector wind direction.

    Bar height = % of ALL hours where wind arrives from that direction with
    sufficient speed (> threshold).  Reveals which approach direction
    contributes most to cross-ventilation potential.
    """
    total = len(vdf)
    if total == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="No wind data available",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
        )
        fig.update_layout(height=380)
        return fig

    wind_ok = (vdf["wind_speed"] > wind_threshold) & ~vdf["is_calm"]
    dir_all    = vdf.groupby("dir_8").size().reindex(_DIR_8, fill_value=0)
    dir_usable = vdf[wind_ok].groupby("dir_8").size().reindex(_DIR_8, fill_value=0)

    freq_pct   = (dir_all    / total * 100).round(1)
    usable_pct = (dir_usable / total * 100).round(1)

    fig = go.Figure()

    # Background: total occurrence frequency (light grey)
    fig.add_trace(go.Bar(
        name="All Wind Hours",
        x=_DIR_8, y=freq_pct.values,
        marker_color="rgba(200,200,200,0.5)",
        marker_line_color="rgba(150,150,150,0.5)",
        marker_line_width=1,
        hovertemplate="%{x}: %{y:.1f}% of all hours<extra>All Wind</extra>",
    ))

    # Foreground: usable cross-ventilation hours
    fig.add_trace(go.Bar(
        name=f"Cross-Vent Usable (>{wind_threshold} m/s)",
        x=_DIR_8, y=usable_pct.values,
        marker_color=_C_CROSS,
        marker_opacity=0.85,
        text=[f"{v:.1f}%" for v in usable_pct.values],
        textposition="outside",
        hovertemplate="%{x}: %{y:.1f}% of all hours<extra>Usable</extra>",
    ))

    fig.update_layout(
        title=dict(
            text=f"Cross-Ventilation Usability by Wind Direction  (threshold: {wind_threshold} m/s)",
            font_size=16,
        ),
        barmode="overlay",
        xaxis_title="Wind Direction (8 sectors)",
        yaxis=dict(
            title="% of Total Hours",
            ticksuffix="%",
            range=[0, max(float(freq_pct.max()), float(usable_pct.max())) * 1.3 + 2],
        ),
        height=400,
        template="plotly_white",
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
        bargap=0.25,
    )
    return fig


def plot_day_night_temperature(
    vdf: pd.DataFrame,
    comfort_min: float = 24.0,
    comfort_max: float = 26.0,
) -> go.Figure:
    """Diurnal temperature profile – day & night context chart.

    Displays:
    • Mean hourly temperature curve (colored by day/night)
    • Min–Max daily range band (blue shading)
    • Green comfort band (comfort_min–comfort_max °C)
    • Purple night shading (hours 0–5 and 18–23)

    Interpretive guide
    ------------------
    Large diurnal swing + nights below comfort band → good night-flush potential.
    Mean temperature mostly above comfort_max      → stack strategy limited.
    Mean temperature within/near comfort band      → passive cooling feasible.
    """
    if vdf.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No temperature data",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
        )
        fig.update_layout(height=400)
        return fig

    hourly = (
        vdf.groupby("hour")["dry_bulb_temperature"]
        .agg(mean="mean", q10=lambda x: x.quantile(0.10), q90=lambda x: x.quantile(0.90))
        .reset_index()
    )

    is_night_hour = (hourly["hour"] >= 18) | (hourly["hour"] <= 5)

    fig = go.Figure()

    # 10th–90th percentile band
    fig.add_trace(go.Scatter(
        x=hourly["hour"], y=hourly["q90"],
        fill=None, mode="lines",
        line_color="rgba(0,0,0,0)", showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=hourly["hour"], y=hourly["q10"],
        fill="tonexty", mode="lines",
        line_color="rgba(0,0,0,0)",
        fillcolor="rgba(59,130,246,0.12)",
        name="P10–P90 Range",
    ))

    # Mean line – dot colours reflect day (blue) vs night (indigo)
    dot_colors = [
        _C_NIGHT if ((h >= 18) or (h <= 5)) else _C_CROSS
        for h in hourly["hour"]
    ]
    fig.add_trace(go.Scatter(
        x=hourly["hour"],
        y=hourly["mean"],
        mode="lines+markers",
        line=dict(color="#2c3e50", width=2.5),
        marker=dict(color=dot_colors, size=8, line=dict(width=0)),
        name="Mean Temperature",
        hovertemplate="Hour %{x}:00 → %{y:.1f}°C<extra></extra>",
    ))

    # Comfort band shading
    fig.add_hrect(
        y0=comfort_min, y1=comfort_max,
        fillcolor="rgba(16,185,129,0.14)",
        line_width=0,
        annotation_text=f"Comfort {comfort_min:.0f}–{comfort_max:.0f}°C",
        annotation_position="top right",
        annotation_font=dict(size=11, color="#10b981"),
    )

    # Night shading (two bands: early morning 0-5, evening 18-23)
    for x0, x1 in [(17.5, 23.5), (-0.5, 5.5)]:
        fig.add_vrect(
            x0=x0, x1=x1,
            fillcolor="rgba(99,102,241,0.07)",
            line_width=0,
        )
    fig.add_annotation(
        text="Night", x=21, y=hourly["q90"].max() * 1.02,
        showarrow=False, font=dict(size=10, color=_C_NIGHT),
    )
    fig.add_annotation(
        text="Night", x=3, y=hourly["q90"].max() * 1.02,
        showarrow=False, font=dict(size=10, color=_C_NIGHT),
    )

    fig.update_layout(
        title=dict(text="Diurnal Temperature Profile  (Day & Night Context)", font_size=16),
        xaxis=dict(
            title="Hour of Day", tickmode="linear", dtick=3,
            range=[-0.5, 23.5],
        ),
        yaxis=dict(title="Temperature (°C)"),
        height=420,
        template="plotly_white",
        legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
    )
    return fig


def plot_ach_distribution(
    vdf: pd.DataFrame,
    opening_factor: float = _ACH_OPENING,
    effectiveness: float  = _ACH_EFFCY,
) -> go.Figure:
    """Histogram of hourly ACH values (simplified model).

    Dashed vertical lines at ACH = 1 (Poor/Moderate boundary) and ACH = 3
    (Moderate/Good boundary) provide immediate design context.
    """
    if vdf.empty:
        fig = go.Figure()
        fig.update_layout(height=360)
        return fig

    ach = vdf["wind_speed"] * opening_factor * effectiveness * _ACH_SCALE

    p99 = float(ach.quantile(0.99))
    x_max = max(p99 + 1.0, 6.0)

    fig = go.Figure(go.Histogram(
        x=ach,
        xbins=dict(start=0, end=x_max, size=0.5),
        marker_color=_C_CROSS,
        marker_opacity=0.8,
        name="ACH",
        hovertemplate="ACH %{x:.1f}: %{y} hours<extra></extra>",
    ))

    for x_val, label, color in [
        (1, "≥1: Moderate", "#f59e0b"),
        (3, "≥3: Good",     "#10b981"),
    ]:
        fig.add_vline(
            x=x_val,
            line_dash="dash", line_color=color, line_width=2,
            annotation_text=label,
            annotation_position="top",
            annotation_font=dict(size=11, color=color),
        )

    fig.update_layout(
        title=dict(text="ACH Distribution – Simplified Ventilation Model", font_size=16),
        xaxis=dict(title="Air Changes per Hour (ACH)", range=[0, x_max]),
        yaxis=dict(title="Hours per Year"),
        height=360,
        template="plotly_white",
        showlegend=False,
    )
    return fig


def plot_facade_pair_table(cross: dict) -> go.Figure:
    """Horizontal bar chart comparing all four facade-pair usabilities.

    Allows the designer to immediately compare all axis options and pick the
    best fit given site constraints (view, access, shading requirements).
    """
    pair_data = cross.get("pair_data", [])
    if not pair_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No pair data",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
        )
        fig.update_layout(height=300)
        return fig

    labels = [ax_label for _, _, ax_label, _ in pair_data]
    pcts   = [pct      for _, _, _,        pct in pair_data]

    # Sort descending for readability
    order  = sorted(range(len(pcts)), key=lambda i: pcts[i])
    labels = [labels[i] for i in order]
    pcts   = [pcts[i]   for i in order]
    colors = [_C_CROSS if p == max(pcts) else "rgba(59,130,246,0.45)" for p in pcts]

    fig = go.Figure(go.Bar(
        x=pcts,
        y=labels,
        orientation="h",
        marker_color=colors,
        text=[f"{p:.1f}%" for p in pcts],
        textposition="outside",
        hovertemplate="%{y}: %{x:.1f}% of total hours<extra></extra>",
    ))

    fig.update_layout(
        title=dict(text="Cross-Ventilation Potential by Facade Axis", font_size=16),
        xaxis=dict(
            title="% of Total Hours with Cross-Ventilation Potential",
            ticksuffix="%",
            range=[0, max(pcts) * 1.35 + 1],
        ),
        yaxis=dict(title=""),
        height=300,
        template="plotly_white",
        showlegend=False,
        margin=dict(l=110),
    )
    return fig


# ─── Main entry point ─────────────────────────────────────────────────────────

def render(
    epw_df: pd.DataFrame,
    months: Optional[list[int]] = None,
    wind_threshold: float = 1.5,
    comfort_min: float  = 24.0,
    comfort_max: float  = 26.0,
    opening_factor: float = _ACH_OPENING,
    effectiveness: float  = _ACH_EFFCY,
) -> None:
    """Render the Natural Ventilation Potential dashboard.

    Called from pages/analysis.py inside ``col_right``.
    All user-adjustable controls are passed from the left panel as arguments.

    Parameters
    ----------
    epw_df         : Full parsed EPW DataFrame (8 760 rows typical).
    months         : Month numbers to include (1–12); None = all twelve.
    wind_threshold : Minimum wind speed (m/s) for cross-ventilation usability.
    comfort_min    : Lower bound of indoor comfort temperature band (°C).
    comfort_max    : Upper bound – used as stack-ventilation trigger (°C).
    opening_factor : Fraction of wall area assumed open (ACH model).
    effectiveness  : Wind-to-flow conversion factor (ACH model).
    """
    st.markdown(
        '<h3 style="color:#2c3e50;margin-bottom:4px;">Natural Ventilation Potential</h3>',
        unsafe_allow_html=True,
    )

    # ── Column validation ─────────────────────────────────────────────────────
    required = {"wind_speed", "wind_direction", "dry_bulb_temperature"}
    missing  = required - set(epw_df.columns)
    if missing:
        st.error(
            f"EPW data is missing columns required for ventilation analysis: "
            f"{', '.join(sorted(missing))}. "
            "Ensure the EPW parser provides wind_speed, wind_direction, and "
            "dry_bulb_temperature."
        )
        return

    if not months:
        months = list(range(1, 13))

    # ════════ COMPUTE ═════════════════════════════════════════════════════════
    with st.spinner("Computing ventilation potential…"):
        vdf    = prepare_ventilation_data(epw_df, months=months)
        cross  = compute_cross_ventilation(vdf, wind_threshold)
        stack  = compute_stack_ventilation(vdf, comfort_min, comfort_max)
        night  = compute_night_flushing(
            vdf,
            hot_day_threshold    = 30.0,
            night_cool_threshold = comfort_max,
        )
        ach    = compute_ach(vdf, opening_factor, effectiveness)
        orient = generate_orientation_recommendation(cross["prevailing_dir"], cross)

    if vdf.empty:
        st.warning("No data available for the selected period.")
        return

    # ── Combined ventilation coverage ─────────────────────────────────────────
    tmp_flags   = _build_usability_flags(
        vdf, wind_threshold, comfort_max,
        hot_day_threshold    = 30.0,
        night_cool_threshold = comfort_max,
    )
    total_vent_pct = float(tmp_flags["any_vent"].mean()) * 100.0

    # ── Climate-context warnings ───────────────────────────────────────────────
    calm_pct  = float(vdf["is_calm"].sum()) / len(vdf) * 100.0
    mean_temp = float(vdf["dry_bulb_temperature"].mean())
    if "relative_humidity" in vdf.columns:
        mean_rh = float(pd.to_numeric(vdf["relative_humidity"], errors="coerce").mean())
    else:
        mean_rh = None

    if calm_pct > 50:
        st.warning(
            f"⚠️ **Very low-wind climate**: {calm_pct:.0f}% of hours are calm "
            f"(< 0.5 m/s). Cross-ventilation effectiveness is limited — "
            "prioritise **Stack Ventilation** and **Night Flushing** strategies."
        )

    if mean_rh is not None and mean_rh > 70 and mean_temp > 28:
        st.info(
            "ℹ️ **Hot & humid climate detected.** High nocturnal humidity reduces "
            "latent heat release during night flushing. Consider supplemental "
            "dehumidification or a hybrid mixed-mode strategy alongside passive cooling."
        )

    # ════════ ORIENTATION RECOMMENDATION BANNER ═══════════════════════════════
    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);
                    color:white;padding:20px 24px;border-radius:10px;
                    margin:8px 0 18px 0;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;
                      letter-spacing:1.2px;opacity:0.75;margin-bottom:8px;">
            Orientation Recommendation
          </div>
          <div style="font-size:14.5px;line-height:1.7;">
            {_bold(orient["narrative"])}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ════════ KPI ROW 1 ═══════════════════════════════════════════════════════
    kc1, kc2, kc3, kc4, kc5 = st.columns(5)
    with kc1:
        st.markdown(
            _kpi("Total Potential", f"{total_vent_pct:.1f}%", _C_TOTAL),
            unsafe_allow_html=True,
        )
    with kc2:
        st.markdown(
            _kpi("Cross Ventilation", f"{cross['cross_hours_pct']:.1f}%", _C_CROSS),
            unsafe_allow_html=True,
        )
    with kc3:
        st.markdown(
            _kpi("Stack Ventilation", f"{stack['stack_hours_pct']:.1f}%", _C_STACK),
            unsafe_allow_html=True,
        )
    with kc4:
        st.markdown(
            _kpi("Night Flushing", f"{night['night_flush_pct']:.1f}%", _C_NIGHT),
            unsafe_allow_html=True,
        )
    with kc5:
        st.markdown(
            _kpi("Mean ACH", f"{ach['mean_ach']:.2f}  ({ach['category']})", ach["category_color"]),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════ KPI ROW 2 (detail) ══════════════════════════════════════════════
    kd1, kd2, kd3, kd4, kd5 = st.columns(5)
    with kd1:
        st.markdown(
            _kpi("Prevailing Wind", cross["prevailing_dir"], "#64748b"),
            unsafe_allow_html=True,
        )
    with kd2:
        st.markdown(
            _kpi("Best Axis", cross["best_pair_label"], _C_CROSS),
            unsafe_allow_html=True,
        )
    with kd3:
        st.markdown(
            _kpi("Stack Strength", stack["strength"], _C_STACK),
            unsafe_allow_html=True,
        )
    with kd4:
        st.markdown(
            _kpi("Diurnal Range", f"{night['mean_diurnal_range']:.1f} °C", _C_NIGHT),
            unsafe_allow_html=True,
        )
    with kd5:
        st.markdown(
            _kpi("Hot Days", f"{night['hot_days_pct']:.0f}%", "#f97316"),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════ CHARTS ══════════════════════════════════════════════════════════

    # 1. Ventilation usability heatmap
    st.plotly_chart(
        plot_ventilation_heatmap(
            vdf,
            wind_threshold       = wind_threshold,
            comfort_max          = comfort_max,
            hot_day_threshold    = 30.0,
            night_cool_threshold = comfort_max,
        ),
        use_container_width=True,
    )

    # 2. Monthly strategy breakdown
    st.plotly_chart(
        plot_monthly_strategy_breakdown(cross, stack, night),
        use_container_width=True,
    )

    # 3. Facade axis comparison (horizontal bar)
    st.plotly_chart(
        plot_facade_pair_table(cross),
        use_container_width=True,
    )

    # 4. Wind direction vs cross-ventilation usability
    st.plotly_chart(
        plot_wind_ventilation_usability(vdf, wind_threshold),
        use_container_width=True,
    )

    # 5. Diurnal temperature profile (day & night context)
    st.plotly_chart(
        plot_day_night_temperature(vdf, comfort_min, comfort_max),
        use_container_width=True,
    )

    # 6. ACH distribution
    st.plotly_chart(
        plot_ach_distribution(vdf, opening_factor, effectiveness),
        use_container_width=True,
    )

    # ════════ STRATEGY SUMMARY TABLE ══════════════════════════════════════════
    st.markdown(
        '<div style="font-size:16px;font-weight:700;padding-bottom:6px;'
        'margin:20px 0 12px;border-bottom:2px solid #e2e8f0;">'
        "Ventilation Strategy Summary</div>",
        unsafe_allow_html=True,
    )

    summary_data = {
        "Strategy":          ["Cross Ventilation", "Stack Ventilation", "Night Flushing"],
        "Usable Hours (%)":  [
            f"{cross['cross_hours_pct']:.1f}",
            f"{stack['stack_hours_pct']:.1f}",
            f"{night['night_flush_pct']:.1f}",
        ],
        "Key Indicator":     [
            f"Prevailing: {cross['prevailing_dir']}  |  Consistency: {cross['consistency']}",
            f"Mean ΔT: {stack['mean_delta_t']:.1f} °C  |  Strength: {stack['strength']}",
            f"Diurnal range: {night['mean_diurnal_range']:.1f} °C  |  Effectiveness: {night['effectiveness_score']:.1f}/10",
        ],
        "Design Implication": [
            f"Orient openings on {orient['primary_facade']}–{orient['secondary_facade']} axis",
            "Use high-level openings and thermal chimneys for stack effect",
            "Expose thermal mass; open vents after 18:00 on days with peak > 30°C",
        ],
    }

    st.dataframe(
        pd.DataFrame(summary_data),
        use_container_width=True,
        hide_index=True,
    )
