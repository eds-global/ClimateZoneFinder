from typing import Optional
from .st_compat import st

"""Wind Analysis module – replicates the Wind tab of Berkeley CBE Clima tool.

Exposes:
    render_wind_analysis(epw_df)  ← called from pages/analysis.py

Meteorological algorithm notes
--------------------------------
• Calm winds (< 0.5 m/s, WMO/ASHRAE convention) are excluded from all
  directional calculations.  Calm % is computed over the full period and
  displayed as a center annotation on the wind rose.

• Wind direction is normalised with ``% 360`` so the 0/360° boundary
  never causes wrap-around artefacts.

• Direction sectors are CENTRED on cardinal/intercardinal points:
    sector i is centred at  i * (360 / n_sectors) degrees.
  Assignment algorithm:
    shifted = (direction + sector_width / 2) % 360
    sector_idx = floor(shifted / sector_width) % n_sectors
  This places North (0°) in the centre of sector 0.

• Wind rose frequencies are expressed as **% of total hours** so the
  sum of all bars + calm% ≈ 100 %.  When the "exclude calm" toggle is
  on, bars are renormalised by non-calm hours only (shows directional
  distribution among wind hours).

• The direction heatmap uses vector (circular) averaging via atan2 to
  handle the 0/360° discontinuity correctly:
    u = cos(θ),  v = sin(θ)
    θ_mean = atan2(mean_v, mean_u)  →  convert to [0, 360).
"""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
# import streamlit as st


# ─── Module-level constants ───────────────────────────────────────────────────

# Standard compass labels
_DIR_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]
_DIR_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
_DIR_4 = ["N", "E", "S", "W"]

# Speed bin edges and display labels (m/s)
_SPEED_BINS   = [0, 2, 4, 6, 8, 10, 15, 100]
_SPEED_LABELS = ["0–2", "2–4", "4–6", "6–8", "8–10", "10–15", "15+"]

# Diverging colour scale matching CBE Clima palette
_SPEED_COLORS = [
    "#313695", "#4575b4", "#74add1", "#abd9e9",
    "#fdae61", "#f46d43", "#d73027",
]

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ─── Data preparation ─────────────────────────────────────────────────────────

def prepare_wind_data(
    df: pd.DataFrame,
    months: Optional[list[int]] = None,
    n_sectors: int = 16,
) -> pd.DataFrame:
    """Prepare and filter EPW wind data for visualisation.

    Steps
    -----
    1. Ensure time-component columns (month, hour, dayofyear) are present.
    2. Sanitise wind_speed and wind_direction (clamp, normalise to [0, 360)).
    3. Flag calm hours (wind_speed < 0.5 m/s).
    4. Apply month filter.
    5. Assign direction sectors using centered-bin algorithm.
    6. Assign speed bins via pd.cut.

    Parameters
    ----------
    df        : EPW dataframe.  Must contain wind_speed and wind_direction.
    months    : list of month numbers (1–12) to keep; None keeps all.
    n_sectors : Number of compass sectors (default 16).

    Returns
    -------
    Filtered DataFrame with added columns:
        month, hour, dayofyear, is_calm,
        sector_idx, direction_label, speed_bin
    """
    wdf = df.copy()

    # ── Time components ───────────────────────────────────────────────────────
    # Prefer precomputed columns (added by analysis.py) over recomputing them.
    if "datetime" in wdf.columns:
        dt = pd.to_datetime(wdf["datetime"])
        if "month" not in wdf.columns:
            wdf["month"] = dt.dt.month
        if "hour" not in wdf.columns:
            wdf["hour"] = dt.dt.hour
        # analysis.py stores day-of-year as "doy"; expose it as "dayofyear"
        if "dayofyear" not in wdf.columns:
            wdf["dayofyear"] = wdf["doy"] if "doy" in wdf.columns else dt.dt.dayofyear

    # ── Wind data sanitisation ────────────────────────────────────────────────
    # EPW uses 999 for missing values; clip to physically realistic range.
    wdf["wind_speed"] = (
        pd.to_numeric(wdf["wind_speed"], errors="coerce")
        .fillna(0.0)
        .clip(lower=0.0, upper=50.0)
    )
    # Normalise direction to [0, 360) – this eliminates 0/360 boundary errors.
    # NOTE: missing/NaN directions are deliberately KEPT as NaN here (rather
    # than filled with 0.0) so they don't create a fake "North" bias.  Rows
    # with a missing direction stay in the frame for speed statistics but are
    # flagged with sector_idx = -1 / direction_label = "Missing" below and
    # excluded from all rose (directional) computations.
    wdf["wind_direction"] = (
        pd.to_numeric(wdf["wind_direction"], errors="coerce")
        % 360.0
    )

    # Calm threshold: WMO / ASHRAE convention
    wdf["is_calm"] = wdf["wind_speed"] < 0.5

    # ── Month filter ──────────────────────────────────────────────────────────
    if months:
        wdf = wdf[wdf["month"].isin(months)].copy()

    if wdf.empty:
        return wdf

    # ── Direction sector assignment (centered bins) ───────────────────────────
    # Each sector is centred on its midpoint angle.
    # Shifting by half a sector width before flooring ensures that 0° falls in
    # the middle of sector 0 (North) rather than on a sector boundary.
    sector_width = 360.0 / n_sectors
    missing_dir  = wdf["wind_direction"].isna()
    shifted      = (wdf["wind_direction"] + sector_width / 2.0) % 360.0
    sector_float = np.floor(shifted / sector_width) % n_sectors
    # Missing directions get sector -1 (excluded from rose computations).
    wdf["sector_idx"] = np.where(missing_dir, -1, sector_float).astype(int)

    # Compass label lookup
    if n_sectors == 16:
        label_list = _DIR_16
    elif n_sectors == 8:
        label_list = _DIR_8
    elif n_sectors == 4:
        label_list = _DIR_4
    else:
        # Generic degree labels for non-standard sector counts
        angles = np.arange(0, 360, sector_width)
        label_list = [f"{int(a)}°" for a in angles]

    idx_to_label = {i: label_list[i] for i in range(n_sectors)}
    idx_to_label[-1] = "Missing"
    wdf["direction_label"] = wdf["sector_idx"].map(idx_to_label)

    # ── Speed bins ────────────────────────────────────────────────────────────
    wdf["speed_bin"] = pd.cut(
        wdf["wind_speed"],
        bins=_SPEED_BINS,
        labels=_SPEED_LABELS,
        right=False,
        include_lowest=True,
    )

    return wdf.reset_index(drop=True)


# ─── Wind Rose computation ────────────────────────────────────────────────────

def compute_wind_rose(
    wdf: pd.DataFrame,
    n_sectors: int = 16,
    exclude_calm: bool = False,
) -> tuple[pd.DataFrame, float]:
    """Build sector × speed-bin frequency table for the wind rose.

    Calm winds are always excluded from directional bars.
    The ``exclude_calm`` flag controls the denominator:
      - False  : normalise by total hours  → bars + calm% ≈ 100 %
  - True   : normalise by non-calm hours → shows directional share
                                           among wind-only hours

    Returns
    -------
    (rose_df, calm_percent)
    rose_df columns : direction_label, speed_bin, frequency_pct
    """
    total = len(wdf)
    if total == 0:
        return pd.DataFrame(), 0.0

    calm_count = int(wdf["is_calm"].sum())
    calm_pct   = calm_count / total * 100.0

    active = wdf[~wdf["is_calm"]].copy()
    # Rows with a missing wind direction (sector_idx == -1) are kept for speed
    # statistics but must never contribute to directional (rose) frequencies.
    if "sector_idx" in active.columns:
        active = active[active["sector_idx"] >= 0]
    if active.empty:
        return pd.DataFrame(), calm_pct

    denominator = len(active) if exclude_calm else total

    grouped = (
        active
        .groupby(["direction_label", "speed_bin"], observed=True)
        .size()
        .reset_index(name="count")
    )
    grouped["frequency_pct"] = grouped["count"] / denominator * 100.0

    return grouped, calm_pct


# ─── Plot: Wind Rose ──────────────────────────────────────────────────────────

def plot_wind_rose(
    rose_df: pd.DataFrame,
    calm_pct: float,
    n_sectors: int = 16,
) -> go.Figure:
    """Polar bar chart (Barpolar) wind rose.

    - Bars stacked by speed tier
    - Radial axis = % of hours
    - North at top, clockwise rotation (meteorological convention)
    - Calm % displayed as a centre annotation
    """
    sector_width  = 360.0 / n_sectors
    sector_angles = [i * sector_width for i in range(n_sectors)]

    if n_sectors == 16:
        label_list = _DIR_16
    elif n_sectors == 8:
        label_list = _DIR_8
    elif n_sectors == 4:
        label_list = _DIR_4
    else:
        label_list = [f"{int(a)}°" for a in sector_angles]

    label_to_angle = dict(zip(label_list, sector_angles))

    if rose_df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No directional wind data available",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=14,
        )
        fig.update_layout(height=520)
        return fig

    fig = go.Figure()

    for i, spd_lbl in enumerate(_SPEED_LABELS):
        subset   = rose_df[rose_df["speed_bin"] == spd_lbl]
        freq_map = dict(zip(subset["direction_label"], subset["frequency_pct"]))

        # Build complete arrays for all sectors (fill missing with 0.0)
        angles = [label_to_angle[lbl] for lbl in label_list]
        freqs  = [freq_map.get(lbl, 0.0)  for lbl in label_list]

        fig.add_trace(go.Barpolar(
            r                 = freqs,
            theta             = angles,
            name              = f"{spd_lbl} m/s",
            marker_color      = _SPEED_COLORS[i % len(_SPEED_COLORS)],
            marker_line_color = "white",
            marker_line_width = 0.5,
            opacity           = 0.9,
            hovertemplate     = (
                "<b>%{theta:.0f}°</b><br>%{r:.2f}%"
                "<extra>" + spd_lbl + " m/s</extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text="Wind Rose",  font_size=16, font_color="#2c3e50"),
        polar=dict(
            radialaxis=dict(
                visible        = True,
                ticksuffix     = "%",
                gridcolor      = "rgba(128,128,128,0.3)",
                linecolor      = "rgba(128,128,128,0.3)",
                tickfont_size  = 10,
            ),
            angularaxis=dict(
                # rotation=90  → 0° at the top = North
                # direction="clockwise" → standard meteorological convention
                rotation       = 90,
                direction      = "clockwise",
                tickmode       = "array",
                tickvals       = sector_angles,
                ticktext       = label_list,
                tickfont_size  = 11,
                gridcolor      = "rgba(128,128,128,0.3)",
                linecolor      = "rgba(128,128,128,0.4)",
            ),
        ),
        legend=dict(
            title       = "Wind Speed",
            orientation = "v",
            x=1.05, y=0.5,
            font_size   = 11,
        ),
        showlegend = True,
        height     = 520,
        template   = "plotly_white",
        annotations=[dict(
            text      = f"Calm<br>{calm_pct:.1f}%",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow = False,
            font      = dict(size=11, color="#555"),
            align     = "center",
        )],
    )
    return fig


# ─── Plot: Seasonal Wind Rose ─────────────────────────────────────────────────

def plot_seasonal_wind_roses(
    wdf: pd.DataFrame,
    n_sectors: int = 16,
) -> go.Figure:
    """2×2 panel of seasonal wind roses: Winter / Spring / Summer / Fall.

    Uses the same speed-bin colour scheme as the annual wind rose.
    """
    SEASONS = [
        ("Winter", [12, 1, 2]),
        ("Spring", [3, 4, 5]),
        ("Summer", [6, 7, 8]),
        ("Fall",   [9, 10, 11]),
    ]

    sector_width = 360.0 / n_sectors
    if n_sectors == 16:
        label_list = _DIR_16
    elif n_sectors == 8:
        label_list = _DIR_8
    elif n_sectors == 4:
        label_list = _DIR_4
    else:
        label_list = [f"{int(i * sector_width)}°" for i in range(n_sectors)]

    label_to_angle = {lbl: i * sector_width for i, lbl in enumerate(label_list)}
    sector_angles  = [i * sector_width for i in range(n_sectors)]

    fig = make_subplots(
        rows=2, cols=2,
        specs=[[{"type": "polar"}, {"type": "polar"}],
               [{"type": "polar"}, {"type": "polar"}]],
        subplot_titles=[s[0] for s in SEASONS],
        horizontal_spacing=0.05,
        vertical_spacing=0.10,
    )

    polar_ids = ["polar", "polar2", "polar3", "polar4"]

    for idx, (season_name, months) in enumerate(SEASONS):
        row      = idx // 2 + 1
        col      = idx % 2 + 1
        show_leg = (idx == 0)

        season_df = wdf[wdf["month"].isin(months)].copy()
        if season_df.empty:
            continue

        rose_df_s, calm_pct_s = compute_wind_rose(
            season_df, n_sectors=n_sectors, exclude_calm=False
        )

        fig.layout.annotations[idx].text = (
            f"<b>{season_name}</b>  ·  Calm {calm_pct_s:.1f}%"
        )

        for i, spd_lbl in enumerate(_SPEED_LABELS):
            subset   = rose_df_s[rose_df_s["speed_bin"] == spd_lbl]
            freq_map = dict(zip(subset["direction_label"], subset["frequency_pct"]))
            angles   = [label_to_angle[lbl] for lbl in label_list]
            freqs    = [freq_map.get(lbl, 0.0) for lbl in label_list]

            fig.add_trace(
                go.Barpolar(
                    r                 = freqs,
                    theta             = angles,
                    name              = f"{spd_lbl} m/s",
                    marker_color      = _SPEED_COLORS[i % len(_SPEED_COLORS)],
                    marker_line_color = "white",
                    marker_line_width = 0.5,
                    opacity           = 0.9,
                    showlegend        = show_leg,
                    legendgroup       = spd_lbl,
                    hovertemplate     = (
                        f"<b>%{{theta:.0f}}°</b><br>%{{r:.2f}}%"
                        f"<extra>{spd_lbl} m/s</extra>"
                    ),
                ),
                row=row, col=col,
            )

    polar_style = dict(
        radialaxis=dict(
            visible       = True,
            ticksuffix    = "%",
            gridcolor     = "rgba(128,128,128,0.3)",
            linecolor     = "rgba(128,128,128,0.3)",
            tickfont_size = 9,
        ),
        angularaxis=dict(
            rotation      = 90,
            direction     = "clockwise",
            tickmode      = "array",
            tickvals      = sector_angles,
            ticktext      = label_list,
            tickfont_size = 9,
            gridcolor     = "rgba(128,128,128,0.3)",
        ),
    )

    fig.update_layout(
        **{pid: polar_style for pid in polar_ids},
        height     = 900,
        template   = "plotly_white",
        showlegend = True,
        legend     = dict(
            title       = "Wind Speed",
            orientation = "v",
            x=1.02, y=0.5,
            font_size   = 10,
        ),
    )
    return fig


# ─── Sector label helper ──────────────────────────────────────────────────────

_BRAND = "#a85c42"


def _sector_label_list(n_sectors: int) -> list[str]:
    """Compass labels for a given sector count (internal helper)."""
    if n_sectors == 16:
        return list(_DIR_16)
    if n_sectors == 8:
        return list(_DIR_8)
    if n_sectors == 4:
        return list(_DIR_4)
    sector_width = 360.0 / n_sectors
    return [f"{int(i * sector_width)}°" for i in range(n_sectors)]


# ─── Plot: Animated Monthly Wind Rose ─────────────────────────────────────────

def plot_animated_wind_rose(
    wdf: pd.DataFrame,
    n_sectors: int = 16,
    exclude_calm: bool = False,
) -> go.Figure:
    """Animated wind rose: one frame per month (Jan → Dec).

    - Stacked ``go.Barpolar`` per speed tier, same bins/colours as the
      annual rose so the two charts are directly comparable.
    - Play / Pause buttons + a month slider (labels = month abbreviations).
    - Radial axis range is FIXED across frames (computed from the maximum
      stacked frequency over all months) so the animation never rescales.
    - Per-frame calm % shown as a centre annotation that updates each frame.
    """
    sector_width  = 360.0 / n_sectors
    label_list    = _sector_label_list(n_sectors)
    sector_angles = [i * sector_width for i in range(n_sectors)]
    label_to_idx  = {lbl: i for i, lbl in enumerate(label_list)}

    months_present = sorted(int(m) for m in wdf["month"].dropna().unique())
    if not months_present:
        fig = go.Figure()
        fig.add_annotation(
            text="No wind data available for animation",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=14,
        )
        fig.update_layout(height=560)
        return fig

    # ── Pre-compute one frequency matrix (speed-bin × sector) per month ──────
    per_month: dict[int, tuple[np.ndarray, float]] = {}
    max_stack = 0.0
    for m in months_present:
        mdf = wdf[wdf["month"] == m]
        rose_df_m, calm_m = compute_wind_rose(
            mdf, n_sectors=n_sectors, exclude_calm=exclude_calm
        )
        mat = np.zeros((len(_SPEED_LABELS), n_sectors))
        if not rose_df_m.empty:
            for _, row in rose_df_m.iterrows():
                spd = str(row["speed_bin"])
                lbl = row["direction_label"]
                if spd in _SPEED_LABELS and lbl in label_to_idx:
                    mat[_SPEED_LABELS.index(spd), label_to_idx[lbl]] = row["frequency_pct"]
        per_month[m] = (mat, calm_m)
        stack_max = float(mat.sum(axis=0).max()) if mat.size else 0.0
        max_stack = max(max_stack, stack_max)

    r_max = max_stack * 1.1 if max_stack > 0 else 1.0

    def _make_traces(mat: np.ndarray) -> list[go.Barpolar]:
        traces = []
        for i, spd_lbl in enumerate(_SPEED_LABELS):
            traces.append(go.Barpolar(
                r                 = mat[i].tolist(),
                theta             = sector_angles,
                name              = f"{spd_lbl} m/s",
                marker_color      = _SPEED_COLORS[i % len(_SPEED_COLORS)],
                marker_line_color = "white",
                marker_line_width = 0.5,
                opacity           = 0.9,
                hovertemplate     = (
                    "<b>%{theta:.0f}°</b><br>%{r:.2f}%"
                    "<extra>" + spd_lbl + " m/s</extra>"
                ),
            ))
        return traces

    def _calm_annotation(month_num: int, calm_val: float) -> dict:
        return dict(
            text      = (
                f"<b>{_MONTH_NAMES[month_num - 1]}</b><br>"
                f"Calm {calm_val:.1f}%"
            ),
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow = False,
            font      = dict(size=12, color="#555"),
            align     = "center",
        )

    first_month             = months_present[0]
    first_mat, first_calm   = per_month[first_month]

    frames = []
    for m in months_present:
        mat_m, calm_m = per_month[m]
        frames.append(go.Frame(
            name   = _MONTH_NAMES[m - 1],
            data   = _make_traces(mat_m),
            layout = go.Layout(annotations=[_calm_annotation(m, calm_m)]),
        ))

    slider_steps = [
        dict(
            method = "animate",
            label  = _MONTH_NAMES[m - 1],
            args   = [
                [_MONTH_NAMES[m - 1]],
                dict(
                    mode       = "immediate",
                    frame      = dict(duration=400, redraw=True),
                    transition = dict(duration=200),
                ),
            ],
        )
        for m in months_present
    ]

    fig = go.Figure(data=_make_traces(first_mat), frames=frames)

    fig.update_layout(
        title=dict(
            text="Monthly Wind Rose Animation",
            font_size=16, font_color="#2c3e50",
        ),
        polar=dict(
            radialaxis=dict(
                visible       = True,
                range         = [0, r_max],
                ticksuffix    = "%",
                gridcolor     = "rgba(128,128,128,0.3)",
                linecolor     = "rgba(128,128,128,0.3)",
                tickfont_size = 10,
            ),
            angularaxis=dict(
                rotation      = 90,
                direction     = "clockwise",
                tickmode      = "array",
                tickvals      = sector_angles,
                ticktext      = label_list,
                tickfont_size = 11,
                gridcolor     = "rgba(128,128,128,0.3)",
                linecolor     = "rgba(128,128,128,0.4)",
            ),
        ),
        legend=dict(
            title       = "Wind Speed",
            orientation = "v",
            x=1.05, y=0.5,
            font_size   = 11,
        ),
        showlegend = True,
        height     = 600,
        template   = "plotly_white",
        annotations=[_calm_annotation(first_month, first_calm)],
        updatemenus=[dict(
            type       = "buttons",
            direction  = "left",
            x=0.0, y=-0.08,
            xanchor="left", yanchor="top",
            pad        = dict(r=10, t=10),
            bgcolor    = "white",
            bordercolor= _BRAND,
            font       = dict(color=_BRAND),
            buttons    = [
                dict(
                    label  = "▶ Play",
                    method = "animate",
                    args   = [
                        None,
                        dict(
                            frame       = dict(duration=700, redraw=True),
                            transition  = dict(duration=250),
                            fromcurrent = True,
                            mode        = "immediate",
                        ),
                    ],
                ),
                dict(
                    label  = "⏸ Pause",
                    method = "animate",
                    args   = [
                        [None],
                        dict(
                            frame      = dict(duration=0, redraw=False),
                            transition = dict(duration=0),
                            mode       = "immediate",
                        ),
                    ],
                ),
            ],
        )],
        sliders=[dict(
            active          = 0,
            x=0.18, y=-0.06,
            xanchor="left", yanchor="top",
            len             = 0.8,
            pad             = dict(t=25, b=5),
            currentvalue    = dict(
                prefix     = "Month: ",
                visible    = True,
                font       = dict(size=13, color=_BRAND),
            ),
            steps           = slider_steps,
        )],
    )
    return fig


# ─── Plot: 3D Wind Rose Tower ─────────────────────────────────────────────────

def plot_wind_rose_3d(
    wdf: pd.DataFrame,
    n_sectors: int = 16,
) -> go.Figure:
    """3D "wind rose tower": one directional-frequency loop per month.

    - Each month's rose is drawn as a closed ``go.Scatter3d`` line loop at
      z = month index; x/y are frequency-scaled compass direction vectors
      (N = +y, E = +x, meteorological clockwise convention).
    - Each loop is coloured by that month's MEAN wind speed on a shared
      Viridis colourscale (colorbar on the right).
    - Faint vertical gridlines connect the same compass direction across
      months; N/E/S/W labels sit at the base and month labels run along z.
    """
    label_list = _sector_label_list(n_sectors)
    sector_width = 360.0 / n_sectors
    # Compass angle (deg, clockwise from North) per sector centre
    theta_deg = np.array([i * sector_width for i in range(n_sectors)])
    theta_rad = np.deg2rad(theta_deg)

    months_present = sorted(int(m) for m in wdf["month"].dropna().unique())
    if not months_present:
        fig = go.Figure()
        fig.add_annotation(
            text="No wind data available for 3D tower",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=14,
        )
        fig.update_layout(height=650)
        return fig

    # ── Per-month directional frequency (% of month hours) + mean speed ──────
    month_freqs:  dict[int, np.ndarray] = {}
    month_speeds: dict[int, float]      = {}
    for m in months_present:
        mdf   = wdf[wdf["month"] == m]
        total = len(mdf)
        active = mdf[~mdf["is_calm"]]
        if "sector_idx" in active.columns:
            active = active[active["sector_idx"] >= 0]
        counts = active.groupby("sector_idx").size() if not active.empty else pd.Series(dtype=int)
        freq = np.array([
            counts.get(i, 0) / total * 100.0 if total > 0 else 0.0
            for i in range(n_sectors)
        ])
        month_freqs[m]  = freq
        month_speeds[m] = float(mdf["wind_speed"].mean()) if total > 0 else 0.0

    max_r = max((f.max() for f in month_freqs.values()), default=1.0)
    if max_r <= 0:
        max_r = 1.0

    spd_vals = [month_speeds[m] for m in months_present]
    s_min, s_max = min(spd_vals), max(spd_vals)
    span = (s_max - s_min) if s_max > s_min else 1.0

    try:
        from plotly.colors import sample_colorscale
        loop_colors = {
            m: sample_colorscale(
                "Viridis", [(month_speeds[m] - s_min) / span]
            )[0]
            for m in months_present
        }
    except Exception:
        loop_colors = {m: "#31688e" for m in months_present}

    fig = go.Figure()

    # ── Faint vertical gridlines: same compass direction across months ───────
    for i in range(n_sectors):
        gx = [month_freqs[m][i] * np.sin(theta_rad[i]) for m in months_present]
        gy = [month_freqs[m][i] * np.cos(theta_rad[i]) for m in months_present]
        gz = list(months_present)
        fig.add_trace(go.Scatter3d(
            x=gx, y=gy, z=gz,
            mode="lines",
            line=dict(color="rgba(150,150,150,0.25)", width=1),
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Monthly closed loops ──────────────────────────────────────────────────
    for m in months_present:
        freq  = month_freqs[m]
        mname = _MONTH_NAMES[m - 1]
        mspd  = month_speeds[m]

        # Close the loop by repeating the first vertex
        idx_loop = list(range(n_sectors)) + [0]
        x = [freq[i] * np.sin(theta_rad[i]) for i in idx_loop]
        y = [freq[i] * np.cos(theta_rad[i]) for i in idx_loop]
        z = [m] * len(idx_loop)
        custom = [
            [mname, label_list[i], float(freq[i]), mspd]
            for i in idx_loop
        ]

        fig.add_trace(go.Scatter3d(
            x=x, y=y, z=z,
            mode="lines+markers",
            line   = dict(color=loop_colors[m], width=5),
            marker = dict(size=2.5, color=loop_colors[m]),
            customdata = custom,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Direction: %{customdata[1]}<br>"
                "Frequency: %{customdata[2]:.2f}%<br>"
                "Mean speed: %{customdata[3]:.2f} m/s"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    # ── Shared Viridis colorbar (dummy marker trace) ──────────────────────────
    fig.add_trace(go.Scatter3d(
        x=[0, 0], y=[0, 0], z=[months_present[0], months_present[0]],
        mode="markers",
        marker=dict(
            size=0.001,
            color=[s_min, s_max],
            colorscale="Viridis",
            cmin=s_min, cmax=s_max,
            showscale=True,
            colorbar=dict(
                title=dict(text="Mean wind<br>speed (m/s)", font_size=11),
                thickness=14, len=0.6, x=1.02,
            ),
        ),
        hoverinfo="skip",
        showlegend=False,
    ))

    # ── N/E/S/W labels at the base ────────────────────────────────────────────
    lbl_r = max_r * 1.2
    fig.add_trace(go.Scatter3d(
        x=[0, lbl_r, 0, -lbl_r],
        y=[lbl_r, 0, -lbl_r, 0],
        z=[months_present[0]] * 4,
        mode="text",
        text=["N", "E", "S", "W"],
        textfont=dict(size=14, color=_BRAND),
        hoverinfo="skip",
        showlegend=False,
    ))

    fig.update_layout(
        title=dict(
            text="3D Wind Rose Tower – Monthly Directional Frequency",
            font_size=16, font_color="#2c3e50",
        ),
        scene=dict(
            xaxis=dict(
                title="", showticklabels=False,
                range=[-lbl_r * 1.15, lbl_r * 1.15],
                showgrid=False, zeroline=False,
                backgroundcolor="rgba(0,0,0,0)",
            ),
            yaxis=dict(
                title="", showticklabels=False,
                range=[-lbl_r * 1.15, lbl_r * 1.15],
                showgrid=False, zeroline=False,
                backgroundcolor="rgba(0,0,0,0)",
            ),
            zaxis=dict(
                title    = "",
                tickmode = "array",
                tickvals = months_present,
                ticktext = [_MONTH_NAMES[m - 1] for m in months_present],
                tickfont = dict(size=10),
                gridcolor= "rgba(128,128,128,0.25)",
            ),
            aspectmode  = "manual",
            aspectratio = dict(x=1, y=1, z=1.4),
            camera      = dict(
                eye    = dict(x=1.7, y=1.7, z=0.75),
                center = dict(x=0, y=0, z=-0.05),
            ),
        ),
        height   = 680,
        template = "plotly_white",
        margin   = dict(l=10, r=60, t=50, b=10),
    )
    return fig


# ─── Plot: Comfort Wind Rose ──────────────────────────────────────────────────

# Thermal usefulness categories: (label, condition-range, colour)
_COMFORT_CATS = [
    ("Cooling wind (20–28°C)", "#0d9488"),   # teal  – useful ventilation air
    ("Hot wind (>28°C)",       "#dc2626"),   # red   – brings unwanted heat
    ("Cold wind (<20°C)",      "#3b82f6"),   # blue  – too cool for comfort
]


def plot_comfort_wind_rose(
    wdf: pd.DataFrame,
    n_sectors: int = 16,
) -> go.Figure:
    """Wind rose split by thermal usefulness of the incoming air.

    Each directional bar is stacked by the coincident dry-bulb temperature:
      - "Cooling wind"  : 20 ≤ T ≤ 28 °C  → useful natural-ventilation air
      - "Hot wind"      : T > 28 °C       → brings unwanted heat
      - "Cold wind"     : T < 20 °C       → too cool, causes draft discomfort
    Frequencies are % of total hours; calm and missing-direction hours are
    excluded from the bars.
    """
    sector_width  = 360.0 / n_sectors
    label_list    = _sector_label_list(n_sectors)
    sector_angles = [i * sector_width for i in range(n_sectors)]

    if "dry_bulb_temperature" not in wdf.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="dry_bulb_temperature column missing – cannot build comfort rose",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=13,
        )
        fig.update_layout(height=520)
        return fig

    total = len(wdf)
    active = wdf[~wdf["is_calm"]].copy()
    if "sector_idx" in active.columns:
        active = active[active["sector_idx"] >= 0]
    active = active.dropna(subset=["dry_bulb_temperature"])

    if total == 0 or active.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No directional wind data available",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=14,
        )
        fig.update_layout(height=520)
        return fig

    t = active["dry_bulb_temperature"]
    masks = {
        _COMFORT_CATS[0][0]: (t >= 20.0) & (t <= 28.0),
        _COMFORT_CATS[1][0]: t > 28.0,
        _COMFORT_CATS[2][0]: t < 20.0,
    }

    fig = go.Figure()
    for cat_label, cat_color in _COMFORT_CATS:
        subset = active[masks[cat_label]]
        counts = subset.groupby("sector_idx").size()
        freqs  = [
            counts.get(i, 0) / total * 100.0
            for i in range(n_sectors)
        ]
        fig.add_trace(go.Barpolar(
            r                 = freqs,
            theta             = sector_angles,
            name              = cat_label,
            marker_color      = cat_color,
            marker_line_color = "white",
            marker_line_width = 0.5,
            opacity           = 0.9,
            hovertemplate     = (
                "<b>%{theta:.0f}°</b><br>%{r:.2f}%"
                "<extra>" + cat_label + "</extra>"
            ),
        ))

    calm_pct = float(wdf["is_calm"].sum()) / total * 100.0

    fig.update_layout(
        title=dict(
            text="Comfort Wind Rose – Thermal Usefulness by Direction",
            font_size=16, font_color="#2c3e50",
        ),
        polar=dict(
            radialaxis=dict(
                visible       = True,
                ticksuffix    = "%",
                gridcolor     = "rgba(128,128,128,0.3)",
                linecolor     = "rgba(128,128,128,0.3)",
                tickfont_size = 10,
            ),
            angularaxis=dict(
                rotation      = 90,
                direction     = "clockwise",
                tickmode      = "array",
                tickvals      = sector_angles,
                ticktext      = label_list,
                tickfont_size = 11,
                gridcolor     = "rgba(128,128,128,0.3)",
                linecolor     = "rgba(128,128,128,0.4)",
            ),
        ),
        legend=dict(
            title       = "Air Temperature",
            orientation = "v",
            x=1.05, y=0.5,
            font_size   = 11,
        ),
        showlegend = True,
        height     = 540,
        template   = "plotly_white",
        annotations=[dict(
            text      = f"Calm<br>{calm_pct:.1f}%",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow = False,
            font      = dict(size=11, color="#555"),
            align     = "center",
        )],
    )
    return fig


# ─── Plot: Speed Heatmap ──────────────────────────────────────────────────────

def plot_speed_heatmap(wdf: pd.DataFrame) -> go.Figure:
    """Wind speed heatmap: month (x-axis) × hour-of-day (y-axis).

    Each cell = mean wind speed for that month × hour combination.
    Color scale: Viridis.
    """
    pivot = wdf.pivot_table(
        values  = "wind_speed",
        index   = "hour",
        columns = "month",
        aggfunc = "mean",
    )
    for m in range(1, 13):
        if m not in pivot.columns:
            pivot[m] = np.nan
    pivot = pivot[sorted(pivot.columns)]

    fig = px.imshow(
        pivot,
        labels = dict(x="Month", y="Hour of Day", color="m/s"),
        title  = "Wind Speed – Month × Hour",
        color_continuous_scale = "Viridis",
        aspect = "auto",
        origin = "lower",
    )
    fig.update_layout(
        height   = 400,
        template = "plotly_white",
        xaxis    = dict(
            title    = "Month",
            tickmode = "array",
            tickvals = list(range(1, 13)),
            ticktext = _MONTH_NAMES,
        ),
        yaxis    = dict(title="Hour of Day"),
        coloraxis_colorbar = dict(title="m/s"),
    )
    return fig


# ─── Plot: Direction Heatmap ──────────────────────────────────────────────────

def plot_direction_heatmap(wdf: pd.DataFrame) -> go.Figure:
    """Wind direction heatmap: month (x-axis) × hour-of-day (y-axis).

    Uses VECTOR (circular) averaging to handle the 0/360° discontinuity.
    Color scale: twilight (cyclic, perceptually uniform for angular data).
    """
    tmp = wdf.copy()
    rad       = np.deg2rad(tmp["wind_direction"])
    tmp["_u"] = np.cos(rad)
    tmp["_v"] = np.sin(rad)

    u_pivot = tmp.pivot_table(values="_u", index="hour", columns="month", aggfunc="mean")
    v_pivot = tmp.pivot_table(values="_v", index="hour", columns="month", aggfunc="mean")

    u_pivot, v_pivot = u_pivot.align(v_pivot, join="inner")

    dir_deg   = np.degrees(np.arctan2(v_pivot.values, u_pivot.values)) % 360
    dir_pivot = pd.DataFrame(dir_deg, index=u_pivot.index, columns=u_pivot.columns)

    for m in range(1, 13):
        if m not in dir_pivot.columns:
            dir_pivot[m] = np.nan
    dir_pivot    = dir_pivot[sorted(dir_pivot.columns)]
    month_cols   = sorted(dir_pivot.columns.tolist())

    fig = px.imshow(
        dir_pivot,
        labels      = dict(x="Month", y="Hour of Day", color="Direction°"),
        title       = "Wind Direction – Month × Hour",
        color_continuous_scale = "twilight",
        range_color = [0, 360],
        aspect      = "auto",
        origin      = "lower",
    )
    fig.update_layout(
        height   = 400,
        template = "plotly_white",
        xaxis    = dict(
            title    = "Month",
            tickmode = "array",
            tickvals = month_cols,
            ticktext = [_MONTH_NAMES[m - 1] for m in month_cols],
        ),
        yaxis    = dict(title="Hour of Day"),
        coloraxis_colorbar=dict(
            title    = "Direction",
            tickvals = [0, 90, 180, 270, 360],
            ticktext = ["N 0°", "E 90°", "S 180°", "W 270°", "N 360°"],
        ),
    )
    return fig


# ─── Plot: Speed Histogram ────────────────────────────────────────────────────

def plot_speed_histogram(wdf: pd.DataFrame) -> go.Figure:
    """Wind speed frequency histogram.

    Bins match the wind rose speed tiers so the two charts are consistent.
    Y-axis shows % of total hours.
    """
    total = len(wdf)
    labels, pcts = [], []

    for i in range(len(_SPEED_BINS) - 1):
        lo, hi  = _SPEED_BINS[i], _SPEED_BINS[i + 1]
        count   = int(((wdf["wind_speed"] >= lo) & (wdf["wind_speed"] < hi)).sum())
        labels.append(_SPEED_LABELS[i])
        pcts.append(count / total * 100.0 if total > 0 else 0.0)

    fig = go.Figure(go.Bar(
        x            = labels,
        y            = pcts,
        marker_color = _SPEED_COLORS[: len(labels)],
        text         = [f"{p:.1f}%" for p in pcts],
        textposition = "outside",
    ))
    fig.update_layout(
        title       = dict(text="Wind Speed Distribution", font_size=16),
        xaxis_title = "Wind Speed Bin (m/s)",
        yaxis       = dict(title="Frequency (%)", ticksuffix="%"),
        height      = 420,
        template    = "plotly_white",
        showlegend  = False,
        bargap      = 0.15,
    )
    return fig


# ─── Plot: Climate Bubble Chart ─────────────────────────────────────────────

# 12 visually distinct colours, one per month
_MONTH_COLORS = [
    "#e6194b", "#f58231", "#ffe119", "#bfef45",
    "#3cb44b", "#42d4f4", "#4363d8", "#911eb4",
    "#f032e6", "#a9a9a9", "#9a6324", "#469990",
]


def plot_climate_bubble(wdf: pd.DataFrame) -> go.Figure:
    """Temperature–Humidity–Wind bubble chart.

    Each point = one EPW hour.

    Axes / visual encoding
    ----------------------
    X   : Dry Bulb Temperature (°C)
    Y   : Relative Humidity (%)
    Size: Wind Speed (m/s) – larger bubble = stronger wind
    Color: Month – seasonal context

    Interpretation guide
    --------------------
    Large bubble at high T & high RH  → hot & humid but breezy
                                        (natural ventilation potential)
    Small bubble at high T & high RH  → hot, humid, stagnant
                                        (most uncomfortable)
    Medium/large bubble near comfort   → near-comfortable with breeze

    Algorithm notes
    ---------------
    • A floor of +0.3 is added to wind_speed before sizing so that calm
      winds still render as tiny visible dots.
    • sizemode="area" keeps perception proportional (area ∝ speed).
    • sizeref is computed from the actual data maximum so the largest
      bubble never exceeds ~28 px in diameter.
    """
    needed = {"dry_bulb_temperature", "relative_humidity", "wind_speed", "month"}
    if not needed.issubset(wdf.columns):
        missing = needed - set(wdf.columns)
        fig = go.Figure()
        fig.add_annotation(
            text=f"Missing columns for bubble chart: {missing}",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font_size=13,
        )
        fig.update_layout(height=500)
        return fig

    tmp = wdf.dropna(
        subset=["dry_bulb_temperature", "relative_humidity", "wind_speed"]
    ).copy()

    # Size floor: calm winds show as tiny dots, not invisible ones
    tmp["_bubble_size"] = tmp["wind_speed"] + 0.3
    # sizeref so the very largest bubble ≈ 28 px diameter (area mode:
    # displayed_diameter ∝ sqrt(value/sizeref)  →  sizeref = value / (px/2)²)
    max_bubble = float(tmp["_bubble_size"].max())
    sizeref    = 2.0 * max_bubble / (28.0 ** 2) if max_bubble > 0 else 0.01

    fig = go.Figure()

    for m in range(1, 13):
        mdata = tmp[tmp["month"] == m]
        if mdata.empty:
            continue
        mname = _MONTH_NAMES[m - 1]
        # Scattergl: WebGL rendering keeps ~8760 points/12 traces responsive
        fig.add_trace(go.Scattergl(
            x          = mdata["dry_bulb_temperature"],
            y          = mdata["relative_humidity"],
            mode       = "markers",
            name       = mname,
            customdata = mdata["wind_speed"].values,
            marker     = dict(
                size      = mdata["_bubble_size"].values,
                sizemode  = "area",
                sizeref   = sizeref,
                sizemin   = 2,
                color     = _MONTH_COLORS[(m - 1) % len(_MONTH_COLORS)],
                opacity   = 0.55,
                line      = dict(width=0),
            ),
            hovertemplate=(
                f"<b>{mname}</b><br>"
                "Temp: %{x:.1f}°C<br>"
                "RH: %{y:.0f}%<br>"
                "Wind: %{customdata:.1f} m/s"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title  = dict(
            text      = "Temperature – Humidity – Wind Speed",
            # x         = 0.5,
            font_size = 16,
            font_color= "#2c3e50",
        ),
        xaxis  = dict(
            title     = "Dry Bulb Temperature (°C)",
            gridcolor = "rgba(200,200,200,0.4)",
        ),
        yaxis  = dict(
            title     = "Relative Humidity (%)",
            range     = [0, 105],
            gridcolor = "rgba(200,200,200,0.4)",
        ),
        legend = dict(
            title       = "Month",
            orientation = "v",
            font_size   = 11,
        ),
        height   = 560,
        template = "plotly_white",
        annotations=[dict(
            text      = "Bubble size = wind speed (m/s)",
            xref="paper", yref="paper",
            x=0.01, y=0.99,
            showarrow = False,
            font      = dict(size=11, color="#888"),
            align     = "left",
        )],
    )
    return fig


# ─── Wind statistics ──────────────────────────────────────────────────────────

def compute_wind_statistics(wdf: pd.DataFrame) -> dict:
    """Compute prevailing-wind summary statistics.

    Returns
    -------
    dict with keys:
        prevailing_direction  – most frequent non-calm direction label
        mean_speed            – mean wind speed (m/s) over all hours
        max_speed             – peak wind speed (m/s)
        calm_percent          – % of calm hours
        strongest_direction   – direction label for the peak-speed hour
    """
    total = len(wdf)
    if total == 0:
        return {}

    calm_pct = float(wdf["is_calm"].sum()) / total * 100.0
    active   = wdf[~wdf["is_calm"]]
    # Exclude rows with a missing direction from the prevailing-direction count
    if "sector_idx" in active.columns:
        active = active[active["sector_idx"] >= 0]

    if active.empty:
        return dict(
            prevailing_direction = "N/A",
            mean_speed           = 0.0,
            max_speed            = 0.0,
            calm_percent         = calm_pct,
            strongest_direction  = "N/A",
        )

    prevailing    = active["direction_label"].value_counts().idxmax()
    max_idx       = wdf["wind_speed"].idxmax()
    strongest_dir = (
        wdf.at[max_idx, "direction_label"]
        if "direction_label" in wdf.columns
        else "N/A"
    )

    return dict(
        prevailing_direction = prevailing,
        mean_speed           = float(wdf["wind_speed"].mean()),
        max_speed            = float(wdf["wind_speed"].max()),
        calm_percent         = calm_pct,
        strongest_direction  = strongest_dir,
    )


# ─── KPI card helper ──────────────────────────────────────────────────────────

def _kpi_card(label: str, value: str, color: str) -> str:
    return (
        f'<div style="background:white;padding:14px;border-radius:8px;'
        f'border-left:4px solid {color};'
        f'box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">'
        f'<div style="font-size:11px;font-weight:700;color:{color};'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:#2c3e50;">{value}</div>'
        f'</div>'
    )


# ─── Main entry point ─────────────────────────────────────────────────────────

def render_wind_analysis(
    epw_df: pd.DataFrame,
    months: Optional[list[int]] = None,
    n_sectors: int = 16,
    exclude_calm: bool = False,
) -> None:
    """Render the Wind Analysis dashboard.

    Called from pages/analysis.py inside ``col_right``.
    All controls (month filter via date range, direction sectors, options toggle)
    live in the left panel of analysis.py and are passed in as parameters.

    Parameters
    ----------
    epw_df       : Full parsed EPW DataFrame (8760 rows).
    months       : Month numbers to include (1–12); None = all 12.
    n_sectors    : Compass sector count for the wind rose.
    exclude_calm : When True, normalise frequencies by non-calm hours only.
    """
    st.markdown(
        '<h3>Wind Analysis</h3>',
        unsafe_allow_html=True,
    )

    # ── Validate required columns ────────────────────────────────────────────
    required = {"wind_speed", "wind_direction"}
    missing  = required - set(epw_df.columns)
    if missing:
        st.error(
            f"EPW dataframe is missing columns required for wind analysis: "
            f"{', '.join(sorted(missing))}. "
            "Please ensure epw_parser.py extracts "
            "wind_speed (EPW field 21) and wind_direction (EPW field 20)."
        )
        return

    # ── Normalise months ─────────────────────────────────────────────────────
    if not months:
        months = list(range(1, 13))

    # ════════ COMPUTE ═════════════════════════════════════════════════════════
    with st.spinner("Computing wind statistics…"):
        wdf = prepare_wind_data(epw_df, months=months, n_sectors=n_sectors)

    if wdf.empty:
        st.warning("No wind data available for the selected date range.")
        return

    rose_df, calm_pct = compute_wind_rose(wdf, n_sectors=n_sectors, exclude_calm=exclude_calm)
    stats             = compute_wind_statistics(wdf)

    # ════════ KPI CARDS (top, before tabs) ════════════════════════════════════
    if stats:
        st.markdown(
            '<div style="font-size:16px;font-weight:700;padding-bottom:6px;margin:8px 0 12px;">'
            "Prevailing Wind Statistics</div>",
            unsafe_allow_html=True,
        )
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        with sc1:
            st.markdown(
                _kpi_card("Prevailing Dir.", stats["prevailing_direction"], "#3b82f6"),
                unsafe_allow_html=True,
            )
        with sc2:
            st.markdown(
                _kpi_card("Mean Speed", f"{stats['mean_speed']:.2f} m/s", "#8b5cf6"),
                unsafe_allow_html=True,
            )
        with sc3:
            st.markdown(
                _kpi_card("Max Speed", f"{stats['max_speed']:.2f} m/s", "#ef4444"),
                unsafe_allow_html=True,
            )
        with sc4:
            st.markdown(
                _kpi_card("Calm Hours", f"{stats['calm_percent']:.1f}%", "#f59e0b"),
                unsafe_allow_html=True,
            )
        with sc5:
            st.markdown(
                _kpi_card("Strongest Dir.", stats["strongest_direction"], "#06b6d4"),
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════ CHART TABS ═══════════════════════════════════════════════════════
    (
        tab_rose,
        tab_anim,
        tab_3d,
        tab_comfort,
        tab_heat,
        tab_dist,
    ) = st.tabs([
        "Wind Rose",
        "Monthly Animation",
        "3D Rose Tower",
        "Comfort Winds",
        "Heatmaps",
        "Distribution & Bubble",
    ])

    # ── Tab 1: Annual + Seasonal Wind Roses ───────────────────────────────────
    with tab_rose:
        st.plotly_chart(
            plot_wind_rose(rose_df, calm_pct, n_sectors),
            use_container_width=True,
        )
        st.plotly_chart(
            plot_seasonal_wind_roses(wdf, n_sectors),
            use_container_width=True,
        )

    # ── Tab 2: Animated Monthly Wind Rose ─────────────────────────────────────
    with tab_anim:
        st.plotly_chart(
            plot_animated_wind_rose(wdf, n_sectors=n_sectors, exclude_calm=exclude_calm),
            use_container_width=True,
        )
        st.caption(
            "Press Play (or drag the slider) to step through the months: each frame is that "
            "month's wind rose — bar length = % of the month's hours from that direction, "
            "colours = speed tiers, and the centre label shows that month's calm share."
        )

    # ── Tab 3: 3D Wind Rose Tower ─────────────────────────────────────────────
    with tab_3d:
        st.plotly_chart(
            plot_wind_rose_3d(wdf, n_sectors=n_sectors),
            use_container_width=True,
        )
        st.caption(
            "Each horizontal loop is one month's directional frequency rose (Jan at the bottom, "
            "Dec at the top); a bulge towards a compass point means winds blew from there more "
            "often, and the loop colour encodes that month's mean wind speed (drag to rotate)."
        )

    # ── Tab 4: Comfort Wind Rose ──────────────────────────────────────────────
    with tab_comfort:
        st.plotly_chart(
            plot_comfort_wind_rose(wdf, n_sectors=n_sectors),
            use_container_width=True,
        )
        st.caption(
            "Bars split each direction's wind hours by coincident air temperature: teal "
            "(20–28°C) marks directions that deliver useful cooling/ventilation air, red (>28°C) "
            "winds that bring heat, and blue (<20°C) winds too cold for comfort ventilation."
        )

    # ── Tab 5: Month × Hour Heatmaps ──────────────────────────────────────────
    with tab_heat:
        st.plotly_chart(plot_speed_heatmap(wdf), use_container_width=True)
        st.plotly_chart(plot_direction_heatmap(wdf), use_container_width=True)

    # ── Tab 6: Speed Distribution + Climate Bubble ────────────────────────────
    with tab_dist:
        st.plotly_chart(plot_speed_histogram(wdf), use_container_width=True)
        st.plotly_chart(plot_climate_bubble(wdf), use_container_width=True)
