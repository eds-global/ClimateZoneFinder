"""Interactive 3D Sun-Path (sky dome) module.

An Andrew Marsh-class interactive sun dome built with Plotly Scatter3d:

* Radius-1 sky dome convention: x = east, y = north, z = up.
  (azimuth measured from north, clockwise; altitude above horizon)
      x = cos(alt) * sin(az),  y = cos(alt) * cos(az),  z = sin(alt)
* Horizon ring, compass labels, altitude circles, meridian arcs.
* Day arcs for the 21st of each month (Jun 21 / Dec 21 emphasized).
* Analemma loops per daylight hour.
* All above-horizon EPW hours as a colored point cloud
  (dry-bulb / GHI / DNI / month).
* Hour-by-hour sun animation with play/pause + hour slider.
* Alternate "2D Stereographic" view using the true stereographic
  projection r = cos(alt) / (1 + sin(alt)).

Public API:
    compute_solar_geometry(lat, lon, tz_str) -> dict of DataFrames
    render_sun_path_3d(df, metadata)         -> Streamlit renderer

Figure builders (pure — data in, go.Figure out, no Streamlit calls):
    prepare_epw_points(df, hourly)
    build_sun_dome_figure(geom, points, color_by, day_label)
    build_stereographic_figure(geom, points, color_by)
"""

from __future__ import annotations

import calendar
from typing import Optional

import numpy as np
import pandas as pd
import pytz
from .st_compat import st
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
# Constants / style
# ─────────────────────────────────────────────────────────────────────────────

BRAND_COLOR = "#a85c42"
SUN_COLOR = "#FFC300"

_MONTH_ABBR = [calendar.month_abbr[m] for m in range(1, 13)]

# 12 discrete month colors (cool winter → warm summer → cool winter)
_MONTH_COLORS = [
    "#3949ab", "#1e88e5", "#00acc1", "#43a047", "#9ccc65", "#fdd835",
    "#fb8c00", "#f4511e", "#e53935", "#8e24aa", "#5e35b1", "#455a64",
]

# "Color by" configuration: column, colorscale, colorbar title, hover format
_COLOR_OPTIONS: dict = {
    "Dry-bulb temperature": dict(
        column="dry_bulb_temperature", colorscale="RdYlBu", reversescale=True,
        cbar="Temp (°C)", label="Temp", fmt="{:.1f} °C",
    ),
    "GHI": dict(
        column="global_horizontal_irradiance", colorscale="Turbo", reversescale=False,
        cbar="GHI (Wh/m²)", label="GHI", fmt="{:.0f} Wh/m²",
    ),
    "DNI": dict(
        column="direct_normal_irradiance", colorscale="Oranges", reversescale=False,
        cbar="DNI (Wh/m²)", label="DNI", fmt="{:.0f} Wh/m²",
    ),
    "Month": dict(
        column="month", colorscale=None, reversescale=False,
        cbar="Month", label="Month", fmt=None,
    ),
}

_FAINT_GREY = "rgba(130, 130, 130, 0.30)"
_SCAFFOLD_GREY = "rgba(110, 110, 110, 0.25)"


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_timezone(tz_str: str) -> pytz.BaseTzInfo:
    """Resolve a pytz timezone string, falling back to a numeric UTC offset."""
    try:
        return pytz.timezone(tz_str)
    except Exception:
        try:
            offset_hours = float(tz_str)
            return pytz.FixedOffset(int(round(offset_hours * 60)))
        except (TypeError, ValueError):
            return pytz.UTC


def _sun_xyz(azimuth_deg, elevation_deg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(azimuth from north CW, altitude) → unit-dome x=east, y=north, z=up."""
    az = np.radians(np.asarray(azimuth_deg, dtype=float))
    alt = np.radians(np.asarray(elevation_deg, dtype=float))
    return np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)


def _stereo_xy(azimuth_deg, elevation_deg) -> tuple[np.ndarray, np.ndarray]:
    """True stereographic projection: r = cos(alt) / (1 + sin(alt)); horizon → r = 1."""
    az = np.radians(np.asarray(azimuth_deg, dtype=float))
    alt = np.radians(np.asarray(elevation_deg, dtype=float))
    r = np.cos(alt) / (1.0 + np.sin(alt))
    return r * np.sin(az), r * np.cos(az)


# ─────────────────────────────────────────────────────────────────────────────
# Public: solar geometry (all pvlib calls live here — cache-friendly)
# ─────────────────────────────────────────────────────────────────────────────


def compute_solar_geometry(lat: float, lon: float, tz_str: str) -> dict:
    """Compute all solar-position data for the sun-path views (year 2020).

    Pure, cache-friendly: inputs are hashable primitives, output is a dict of
    plain DataFrames. All pvlib calls happen here.

    Returns dict with keys:
        hourly   — above-horizon hourly sun positions for 2020
                   (datetime [naive local], azimuth, elevation, hour, doy, x, y, z)
        analemma — same rows, intended to be grouped by ``hour`` into
                   figure-8 analemma loops
        day_arcs — 5-min resolution above-horizon arcs for the 21st of each
                   month (datetime, azimuth, elevation, x, y, z, month, label)
        day_info — per-month summary (month, label, sunrise, sunset,
                   day_length [h], max_altitude [°])
    """
    from pvlib import solarposition

    tz = _resolve_timezone(tz_str)

    # ── Hourly positions for the full (leap) year 2020 ──────────────────────
    times = pd.date_range(
        "2020-01-01 00:00", "2021-01-01 00:00", freq="h", tz=tz, inclusive="left"
    )
    solpos = solarposition.get_solarposition(times, lat, lon)

    base = pd.DataFrame(
        {
            "datetime": times.tz_localize(None),
            "azimuth": solpos["azimuth"].to_numpy(dtype=float),
            "elevation": solpos["apparent_elevation"].to_numpy(dtype=float),
        }
    )
    base["hour"] = base["datetime"].dt.hour
    base["doy"] = base["datetime"].dt.dayofyear
    x, y, z = _sun_xyz(base["azimuth"], base["elevation"])
    base["x"], base["y"], base["z"] = x, y, z

    hourly = base[base["elevation"] > 0].reset_index(drop=True)
    # Analemma loops: every hour of day that has any above-horizon sun.
    analemma = hourly.copy()

    # ── Day arcs on the 21st of each month, 5-minute resolution ─────────────
    arc_frames: list[pd.DataFrame] = []
    info_rows: list[dict] = []
    for m in range(1, 13):
        label = f"{calendar.month_abbr[m]} 21"
        day_times = pd.date_range(
            f"2020-{m:02d}-21 00:00", periods=288, freq="5min", tz=tz
        )
        sol = solarposition.get_solarposition(day_times, lat, lon)
        arc = pd.DataFrame(
            {
                "datetime": day_times.tz_localize(None),
                "azimuth": sol["azimuth"].to_numpy(dtype=float),
                "elevation": sol["apparent_elevation"].to_numpy(dtype=float),
            }
        )
        arc = arc[arc["elevation"] > 0].reset_index(drop=True)

        if arc.empty:  # polar night — no arc for this month
            info_rows.append(
                dict(month=m, label=label, sunrise="—", sunset="—",
                     day_length=0.0, max_altitude=0.0)
            )
            continue

        ax, ay, az_ = _sun_xyz(arc["azimuth"], arc["elevation"])
        arc["x"], arc["y"], arc["z"] = ax, ay, az_
        arc["month"] = m
        arc["label"] = label
        arc_frames.append(arc)

        info_rows.append(
            dict(
                month=m,
                label=label,
                sunrise=arc["datetime"].iloc[0].strftime("%H:%M"),
                sunset=arc["datetime"].iloc[-1].strftime("%H:%M"),
                day_length=round(len(arc) * 5 / 60.0, 2),
                max_altitude=round(float(arc["elevation"].max()), 1),
            )
        )

    day_arcs = (
        pd.concat(arc_frames, ignore_index=True)
        if arc_frames
        else pd.DataFrame(
            columns=["datetime", "azimuth", "elevation", "x", "y", "z", "month", "label"]
        )
    )
    day_info = pd.DataFrame(info_rows)

    return {"hourly": hourly, "analemma": analemma,
            "day_arcs": day_arcs, "day_info": day_info}


# ─────────────────────────────────────────────────────────────────────────────
# EPW join (pure)
# ─────────────────────────────────────────────────────────────────────────────


def prepare_epw_points(df: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    """Join EPW weather values onto the above-horizon hourly sun positions.

    EPW timestamps are normalized to year 2020 and floored to the hour, then
    numeric columns are averaged per hour (handles half-hour EPW stamps) and
    merged onto ``hourly`` (from :func:`compute_solar_geometry`).
    """
    if df is None or "datetime" not in getattr(df, "columns", []):
        return pd.DataFrame(
            columns=list(hourly.columns) + ["dry_bulb_temperature", "month"]
        )

    epw = df.copy()
    dt = pd.to_datetime(epw["datetime"], errors="coerce").dt.floor("h")

    def _to_2020(ts):
        try:
            return ts.replace(year=2020)
        except (ValueError, AttributeError):
            return pd.NaT

    epw["datetime"] = dt.map(_to_2020)
    epw = epw.dropna(subset=["datetime"])

    value_cols = [
        c
        for c in (
            "dry_bulb_temperature",
            "global_horizontal_irradiance",
            "direct_normal_irradiance",
            "diffuse_horizontal_irradiance",
            "relative_humidity",
        )
        if c in epw.columns
    ]
    for c in value_cols:
        epw[c] = pd.to_numeric(epw[c], errors="coerce")
    epw = epw.groupby("datetime", as_index=False)[value_cols].mean()

    merged = hourly.merge(epw, on="datetime", how="inner")
    merged["month"] = merged["datetime"].dt.month
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Shared color / hover helpers
# ─────────────────────────────────────────────────────────────────────────────


def _month_colorscale() -> list:
    scale = []
    for i, c in enumerate(_MONTH_COLORS):
        scale.append([i / 12.0, c])
        scale.append([(i + 1) / 12.0, c])
    return scale


def _marker_color_kwargs(points: pd.DataFrame, color_by: str) -> dict:
    """Marker color/colorscale/colorbar kwargs for the point cloud."""
    cfg = _COLOR_OPTIONS[color_by]
    col = cfg["column"]
    vals = pd.to_numeric(points[col], errors="coerce") if col in points.columns else pd.Series(dtype=float)

    colorbar = dict(
        title=dict(text=cfg["cbar"], side="right", font=dict(size=11)),
        thickness=14,
        len=0.65,
        x=1.02,
    )
    if col == "month":
        colorbar.update(tickvals=list(range(1, 13)), ticktext=_MONTH_ABBR, tickmode="array")
        return dict(
            color=vals, colorscale=_month_colorscale(),
            cmin=0.5, cmax=12.5, colorbar=colorbar, showscale=True,
        )

    finite = vals.dropna()
    cmin = float(finite.min()) if len(finite) else 0.0
    cmax = float(finite.max()) if len(finite) else 1.0
    if col != "dry_bulb_temperature":
        cmin = 0.0
    if cmax <= cmin:
        cmax = cmin + 1.0
    return dict(
        color=vals, colorscale=cfg["colorscale"], reversescale=cfg["reversescale"],
        cmin=cmin, cmax=cmax, colorbar=colorbar, showscale=True,
    )


def _point_customdata(points: pd.DataFrame, color_by: str) -> np.ndarray:
    cfg = _COLOR_OPTIONS[color_by]
    col = cfg["column"]
    dates = points["datetime"].dt.strftime("%b %d")
    hours = points["datetime"].dt.strftime("%H:00")
    alt = points["elevation"].map(lambda v: f"{v:.1f}")
    az = points["azimuth"].map(lambda v: f"{v:.1f}")

    if col == "month":
        vals = points[col].map(lambda m: f"Month: {calendar.month_name[int(m)]}")
    elif col in points.columns:
        vals = points[col].map(
            lambda v: f"{cfg['label']}: " + (cfg["fmt"].format(v) if pd.notna(v) else "n/a")
        )
    else:
        vals = pd.Series(["n/a"] * len(points), index=points.index)
    return np.stack([dates, hours, alt, az, vals], axis=-1)


_POINT_HOVER = (
    "<b>%{customdata[0]}, %{customdata[1]}</b><br>"
    "Altitude: %{customdata[2]}°<br>"
    "Azimuth: %{customdata[3]}°<br>"
    "%{customdata[4]}<extra></extra>"
)


def _split_on_gaps(group: pd.DataFrame, cols: tuple[str, ...]) -> list[list]:
    """Group analemma rows sorted by doy into gap-separated coordinate lists."""
    g = group.sort_values("doy")
    seg_break = (g["doy"].diff() > 5).cumsum()
    out: list[list] = [[] for _ in cols]
    for _, seg in g.groupby(seg_break):
        for lst, c in zip(out, cols):
            lst.extend(seg[c].tolist())
            lst.append(None)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3D dome scaffold traces
# ─────────────────────────────────────────────────────────────────────────────


def _dome_scaffold_traces() -> list:
    """Horizon ring, altitude circles, meridian arcs, compass labels."""
    traces = []
    t = np.linspace(0.0, 2.0 * np.pi, 181)

    # Horizon ring (unit circle at z = 0)
    traces.append(
        go.Scatter3d(
            x=np.sin(t), y=np.cos(t), z=np.zeros_like(t),
            mode="lines", line=dict(color="rgba(70,70,70,0.85)", width=4),
            hoverinfo="skip", showlegend=False, name="Horizon",
        )
    )

    # Faint altitude circles at 30° and 60°
    for alt in (30, 60):
        r = float(np.cos(np.radians(alt)))
        zz = float(np.sin(np.radians(alt)))
        traces.append(
            go.Scatter3d(
                x=r * np.sin(t), y=r * np.cos(t), z=np.full_like(t, zz),
                mode="lines", line=dict(color=_SCAFFOLD_GREY, width=1.5),
                hoverinfo="skip", showlegend=False, name=f"{alt}° altitude",
            )
        )

    # 4 faint vertical meridian arcs (each spans horizon → zenith → horizon)
    p = np.linspace(-np.pi / 2.0, np.pi / 2.0, 91)
    for az_deg in (0, 45, 90, 135):
        ux = float(np.sin(np.radians(az_deg)))
        uy = float(np.cos(np.radians(az_deg)))
        traces.append(
            go.Scatter3d(
                x=np.sin(p) * ux, y=np.sin(p) * uy, z=np.cos(p),
                mode="lines", line=dict(color=_SCAFFOLD_GREY, width=1.5),
                hoverinfo="skip", showlegend=False, name=f"Meridian {az_deg}°",
            )
        )

    # Compass labels slightly outside the horizon ring
    def _ring_xy(az_deg: float, radius: float) -> tuple[float, float]:
        return radius * float(np.sin(np.radians(az_deg))), radius * float(np.cos(np.radians(az_deg)))

    card = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]
    inter = [("NE", 45), ("SE", 135), ("SW", 225), ("NW", 315)]
    cx, cy = zip(*[_ring_xy(a, 1.12) for _, a in card])
    ix, iy = zip(*[_ring_xy(a, 1.12) for _, a in inter])
    traces.append(
        go.Scatter3d(
            x=list(cx), y=list(cy), z=[0.0] * 4, mode="text",
            text=[n for n, _ in card],
            textfont=dict(size=15, color="#333333", family="Arial Black, Arial"),
            hoverinfo="skip", showlegend=False, name="Cardinal",
        )
    )
    traces.append(
        go.Scatter3d(
            x=list(ix), y=list(iy), z=[0.0] * 4, mode="text",
            text=[n for n, _ in inter],
            textfont=dict(size=10, color="#8a8a8a"),
            hoverinfo="skip", showlegend=False, name="Intercardinal",
        )
    )
    return traces


def _ground_disc_trace(radius: float = 1.18, n: int = 72) -> go.Mesh3d:
    """Faint circular ground disc just below the horizon plane."""
    ang = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    xs = np.concatenate([[0.0], radius * np.sin(ang)])
    ys = np.concatenate([[0.0], radius * np.cos(ang)])
    zs = np.full(n + 1, -0.012)
    i = np.zeros(n, dtype=int)
    j = np.arange(1, n + 1)
    k = np.concatenate([np.arange(2, n + 1), [1]])
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color="#7d8a77", opacity=0.14, hoverinfo="skip",
        name="Ground", showlegend=False,
    )


def _building_trace(half_width: float = 0.03, height: float = 0.055) -> go.Mesh3d:
    """Small brand-colored box at the origin for orientation."""
    w = half_width
    xs = [-w, -w, w, w, -w, -w, w, w]
    ys = [-w, w, w, -w, -w, w, w, -w]
    zs = [0, 0, 0, 0, height, height, height, height]
    return go.Mesh3d(
        x=xs, y=ys, z=zs,
        i=[7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2],
        j=[3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3],
        k=[0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6],
        color=BRAND_COLOR, opacity=0.95, flatshading=True,
        hoverinfo="skip", name="Building", showlegend=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Data traces (3D)
# ─────────────────────────────────────────────────────────────────────────────


def _day_arc_traces_3d(day_arcs: pd.DataFrame) -> list:
    traces = []
    if day_arcs.empty:
        return traces
    labels_x, labels_y, labels_z, labels_txt = [], [], [], []
    for m, arc in day_arcs.groupby("month"):
        label = arc["label"].iloc[0]
        emphasized = m in (6, 12)
        if m == 6:
            color, width = "#c0392b", 5
        elif m == 12:
            color, width = "#2266aa", 5
        else:
            color, width = "rgba(150,150,150,0.55)", 1.6
        traces.append(
            go.Scatter3d(
                x=arc["x"], y=arc["y"], z=arc["z"],
                mode="lines", line=dict(color=color, width=width),
                name=label, legendgroup="dayarcs",
                showlegend=emphasized,
                hovertext=label, hoverinfo="text" if emphasized else "skip",
            )
        )
        if emphasized:
            top = arc.loc[arc["z"].idxmax()]
            labels_x.append(float(top["x"]))
            labels_y.append(float(top["y"]))
            labels_z.append(float(top["z"]) + 0.05)
            labels_txt.append(label)
    if labels_txt:
        traces.append(
            go.Scatter3d(
                x=labels_x, y=labels_y, z=labels_z, mode="text",
                text=labels_txt, textfont=dict(size=11, color="#444444"),
                hoverinfo="skip", showlegend=False, name="Arc labels",
            )
        )
    return traces


def _analemma_trace_3d(analemma: pd.DataFrame) -> go.Scatter3d:
    xs: list = []
    ys: list = []
    zs: list = []
    if not analemma.empty:
        for _, g in analemma.groupby("hour"):
            gx, gy, gz = _split_on_gaps(g, ("x", "y", "z"))
            xs += gx
            ys += gy
            zs += gz
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=_FAINT_GREY, width=1.5),
        name="Hourly analemmas", showlegend=True, hoverinfo="skip",
    )


def _point_cloud_trace_3d(points: pd.DataFrame, color_by: str) -> Optional[go.Scatter3d]:
    if points is None or points.empty:
        return None
    marker = dict(size=2.6, opacity=0.75, line=dict(width=0))
    marker.update(_marker_color_kwargs(points, color_by))
    return go.Scatter3d(
        x=points["x"], y=points["y"], z=points["z"],
        mode="markers", marker=marker,
        customdata=_point_customdata(points, color_by),
        hovertemplate=_POINT_HOVER,
        name="EPW hours", showlegend=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Animation
# ─────────────────────────────────────────────────────────────────────────────


def _sun_marker(row: Optional[pd.Series]) -> go.Scatter3d:
    if row is None:
        x, y, z, txt = [], [], [], []
    else:
        x, y, z = [float(row["x"])], [float(row["y"])], [float(row["z"])]
        txt = [
            f"{row['label']} {row['datetime'].strftime('%H:%M')}<br>"
            f"Altitude: {row['elevation']:.1f}°<br>Azimuth: {row['azimuth']:.1f}°"
        ]
    return go.Scatter3d(
        x=x, y=y, z=z, mode="markers",
        marker=dict(size=10, color=SUN_COLOR, line=dict(color=BRAND_COLOR, width=2),
                    symbol="circle"),
        name="Sun", showlegend=True,
        hovertext=txt, hoverinfo="text",
    )


def _sun_ray(row: Optional[pd.Series]) -> go.Scatter3d:
    if row is None:
        x, y, z = [], [], []
    else:
        x, y, z = [0.0, float(row["x"])], [0.0, float(row["y"])], [0.0, float(row["z"])]
    return go.Scatter3d(
        x=x, y=y, z=z, mode="lines",
        line=dict(color="rgba(255,195,0,0.85)", width=3),
        name="Sun ray", showlegend=False, hoverinfo="skip",
    )


def _animation_frames(day_arc: pd.DataFrame, sun_idx: int, ray_idx: int) -> list:
    """One frame per above-horizon hour (on-the-hour points of the 5-min arc)."""
    frames = []
    if day_arc.empty:
        return frames
    on_hour = day_arc[day_arc["datetime"].dt.minute == 0]
    for _, row in on_hour.iterrows():
        name = row["datetime"].strftime("%H:00")
        frames.append(
            go.Frame(
                name=name,
                data=[_sun_marker(row), _sun_ray(row)],
                traces=[sun_idx, ray_idx],
            )
        )
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Pure figure builders
# ─────────────────────────────────────────────────────────────────────────────


def build_sun_dome_figure(
    geom: dict,
    points: pd.DataFrame,
    color_by: str = "Dry-bulb temperature",
    day_label: str = "Jun 21",
) -> go.Figure:
    """Build the interactive 3D sun dome figure (pure — no Streamlit calls)."""
    fig = go.Figure()

    fig.add_trace(_ground_disc_trace())
    fig.add_trace(_building_trace())
    for tr in _dome_scaffold_traces():
        fig.add_trace(tr)
    for tr in _day_arc_traces_3d(geom["day_arcs"]):
        fig.add_trace(tr)
    fig.add_trace(_analemma_trace_3d(geom["analemma"]))

    cloud = _point_cloud_trace_3d(points, color_by)
    if cloud is not None:
        fig.add_trace(cloud)

    # ── Animated sun + ray on the selected day arc ───────────────────────────
    day_arcs = geom["day_arcs"]
    sel_arc = (
        day_arcs[day_arcs["label"] == day_label]
        if not day_arcs.empty
        else day_arcs
    )
    first_row = None
    if not sel_arc.empty:
        on_hour = sel_arc[sel_arc["datetime"].dt.minute == 0]
        if not on_hour.empty:
            first_row = on_hour.iloc[0]

    sun_idx = len(fig.data)
    fig.add_trace(_sun_marker(first_row))
    ray_idx = len(fig.data)
    fig.add_trace(_sun_ray(first_row))

    frames = _animation_frames(sel_arc, sun_idx, ray_idx)
    fig.frames = frames

    hidden_axis = dict(
        visible=False, showbackground=False, showgrid=False,
        zeroline=False, showticklabels=False,
    )
    fig.update_layout(
        scene=dict(
            aspectmode="data",
            xaxis=hidden_axis, yaxis=hidden_axis, zaxis=hidden_axis,
            camera=dict(eye=dict(x=1.4, y=-1.4, z=0.7)),
            bgcolor="rgba(0,0,0,0)",
        ),
        height=720,
        margin=dict(l=0, r=0, t=30, b=10),
        paper_bgcolor="white",
        legend=dict(
            orientation="h", x=0.5, xanchor="center",
            y=-0.16, yanchor="top", font=dict(size=10),
        ),
        hovermode="closest",
        font=dict(family="Arial, sans-serif", size=12),
    )

    if frames:
        anim_args = dict(
            frame=dict(duration=150, redraw=True),
            transition=dict(duration=0),
            fromcurrent=True,
        )
        stop_args = dict(
            mode="immediate",
            frame=dict(duration=0, redraw=True),
            transition=dict(duration=0),
        )
        fig.update_layout(
            updatemenus=[
                dict(
                    type="buttons",
                    direction="left",
                    x=0.02, xanchor="left", y=-0.02, yanchor="top",
                    pad=dict(r=6, t=4),
                    bgcolor="rgba(255,255,255,0.85)",
                    bordercolor=BRAND_COLOR, borderwidth=1,
                    font=dict(color=BRAND_COLOR, size=12),
                    buttons=[
                        dict(label="▶ Play", method="animate", args=[None, anim_args]),
                        dict(label="⏸ Pause", method="animate", args=[[None], stop_args]),
                    ],
                )
            ],
            sliders=[
                dict(
                    active=0,
                    x=0.22, len=0.74, y=-0.02, yanchor="top",
                    pad=dict(t=4),
                    currentvalue=dict(
                        prefix=f"{day_label} — ",
                        font=dict(size=12, color=BRAND_COLOR),
                    ),
                    steps=[
                        dict(
                            method="animate",
                            label=f.name,
                            args=[[f.name], stop_args],
                        )
                        for f in frames
                    ],
                )
            ],
        )

    return fig


def build_stereographic_figure(
    geom: dict,
    points: pd.DataFrame,
    color_by: str = "Dry-bulb temperature",
) -> go.Figure:
    """2D stereographic sun-path (r = cos(alt) / (1 + sin(alt))) — pure builder."""
    fig = go.Figure()
    t = np.linspace(0.0, 2.0 * np.pi, 241)

    # Horizon circle (r = 1)
    fig.add_trace(
        go.Scatter(
            x=np.sin(t), y=np.cos(t), mode="lines",
            line=dict(color="rgba(70,70,70,0.85)", width=2.5),
            hoverinfo="skip", showlegend=False, name="Horizon",
        )
    )

    # Altitude rings 10°..80° + labels along the NE direction
    ring_lx, ring_ly, ring_txt = [], [], []
    for alt in range(10, 90, 10):
        r = float(np.cos(np.radians(alt)) / (1.0 + np.sin(np.radians(alt))))
        fig.add_trace(
            go.Scatter(
                x=r * np.sin(t), y=r * np.cos(t), mode="lines",
                line=dict(color=_SCAFFOLD_GREY, width=1),
                hoverinfo="skip", showlegend=False, name=f"{alt}°",
            )
        )
        ring_lx.append(r * float(np.sin(np.radians(45))))
        ring_ly.append(r * float(np.cos(np.radians(45))))
        ring_txt.append(f"{alt}°")
    fig.add_trace(
        go.Scatter(
            x=ring_lx, y=ring_ly, mode="text", text=ring_txt,
            textfont=dict(size=9, color="#999999"),
            hoverinfo="skip", showlegend=False, name="Altitude labels",
        )
    )

    # N-S / E-W cross-hairs
    for xs, ys in (([0, 0], [-1, 1]), ([-1, 1], [0, 0])):
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="lines",
                line=dict(color=_SCAFFOLD_GREY, width=1, dash="dot"),
                hoverinfo="skip", showlegend=False,
            )
        )

    # Compass labels
    card = [("N", 0), ("NE", 45), ("E", 90), ("SE", 135),
            ("S", 180), ("SW", 225), ("W", 270), ("NW", 315)]
    fig.add_trace(
        go.Scatter(
            x=[1.10 * float(np.sin(np.radians(a))) for _, a in card],
            y=[1.10 * float(np.cos(np.radians(a))) for _, a in card],
            mode="text", text=[n for n, _ in card],
            textfont=dict(size=12, color="#333333"),
            hoverinfo="skip", showlegend=False, name="Compass",
        )
    )

    # Day arcs
    day_arcs = geom["day_arcs"]
    if not day_arcs.empty:
        for m, arc in day_arcs.groupby("month"):
            label = arc["label"].iloc[0]
            emphasized = m in (6, 12)
            if m == 6:
                color, width = "#c0392b", 3
            elif m == 12:
                color, width = "#2266aa", 3
            else:
                color, width = "rgba(150,150,150,0.55)", 1
            px_, py_ = _stereo_xy(arc["azimuth"], arc["elevation"])
            fig.add_trace(
                go.Scatter(
                    x=px_, y=py_, mode="lines",
                    line=dict(color=color, width=width),
                    name=label, showlegend=emphasized,
                    hovertext=label, hoverinfo="text" if emphasized else "skip",
                )
            )

    # Analemma loops (gap-separated single trace)
    analemma = geom["analemma"]
    xs: list = []
    ys: list = []
    if not analemma.empty:
        ana = analemma.copy()
        sx, sy = _stereo_xy(ana["azimuth"], ana["elevation"])
        ana["sx"], ana["sy"] = sx, sy
        for _, g in ana.groupby("hour"):
            gx, gy = _split_on_gaps(g, ("sx", "sy"))
            xs += gx
            ys += gy
    fig.add_trace(
        go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(color=_FAINT_GREY, width=1),
            name="Hourly analemmas", showlegend=True, hoverinfo="skip",
        )
    )

    # EPW point cloud
    if points is not None and not points.empty:
        px_, py_ = _stereo_xy(points["azimuth"], points["elevation"])
        marker = dict(size=4, opacity=0.75, line=dict(width=0))
        marker.update(_marker_color_kwargs(points, color_by))
        fig.add_trace(
            go.Scatter(
                x=px_, y=py_, mode="markers", marker=marker,
                customdata=_point_customdata(points, color_by),
                hovertemplate=_POINT_HOVER,
                name="EPW hours", showlegend=True,
            )
        )

    fig.update_layout(
        xaxis=dict(visible=False, range=[-1.35, 1.35],
                   scaleanchor="y", scaleratio=1),
        yaxis=dict(visible=False, range=[-1.30, 1.30]),
        height=700,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend=dict(
            orientation="h", x=0.5, xanchor="center",
            y=-0.06, yanchor="top", font=dict(size=10),
        ),
        hovermode="closest",
        annotations=[
            dict(
                x=0.0, y=1.06, xref="paper", yref="paper",
                text="Stereographic projection — r = cos(alt) / (1 + sin(alt))",
                showarrow=False, font=dict(size=11, color="#888888"),
                xanchor="left",
            )
        ],
        font=dict(family="Arial, sans-serif", size=12),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Cached wrappers (Streamlit)
# ─────────────────────────────────────────────────────────────────────────────


@st.cache_data(show_spinner="Computing solar geometry…")
def _cached_solar_geometry(lat: float, lon: float, tz_str: str) -> dict:
    return compute_solar_geometry(lat, lon, tz_str)


@st.cache_data(show_spinner=False)
def _cached_epw_points(df: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    return prepare_epw_points(df, hourly)


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit renderer
# ─────────────────────────────────────────────────────────────────────────────


def render_sun_path_3d(df: pd.DataFrame, metadata: dict) -> None:
    """Render the interactive 3D sun-path section."""
    city = metadata.get("city") or ""
    title = f"Interactive 3D Sun Path{' — ' + city if city else ''}"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    lat = metadata.get("latitude")
    lon = metadata.get("longitude")
    if lat is None or lon is None:
        st.error("Location information (latitude/longitude) not found in EPW file.")
        return
    tz_str = str(metadata.get("timezone") or "UTC")

    try:
        geom = _cached_solar_geometry(float(lat), float(lon), tz_str)
    except Exception as e:
        st.error(f"Could not compute solar geometry: {e}")
        return

    if geom["hourly"].empty:
        st.error("No above-horizon solar positions found. Check location/timezone.")
        return

    points = _cached_epw_points(df, geom["hourly"])
    if points.empty:
        st.warning("Could not join EPW data to solar positions — showing geometry only.")

    # ── Controls ─────────────────────────────────────────────────────────────
    c1, c2 = st.columns([1.2, 1.4])
    view_mode = c1.radio(
        "View mode", ["3D Dome", "2D Stereographic"],
        horizontal=True, key="sp3d_view_mode",
    )
    color_by = c2.selectbox("Color by", list(_COLOR_OPTIONS.keys()), key="sp3d_color_by")

    day_info = geom["day_info"]
    day_labels = day_info["label"].tolist()
    default_day = "Jun 21" if "Jun 21" in day_labels else day_labels[0]
    day_label = st.select_slider(
        "Animated day (21st of each month)",
        options=day_labels, value=default_day, key="sp3d_day",
    )

    # ── Live readout for the selected day ────────────────────────────────────
    sel = day_info[day_info["label"] == day_label]
    if not sel.empty:
        row = sel.iloc[0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Sunrise", row["sunrise"])
        m2.metric("Sunset", row["sunset"])
        m3.metric("Day length", f"{row['day_length']:.1f} h")
        m4.metric("Max altitude", f"{row['max_altitude']:.1f}°")

    # ── Figure ───────────────────────────────────────────────────────────────
    if view_mode == "3D Dome":
        fig = build_sun_dome_figure(geom, points, color_by, day_label)
        st.plotly_chart(fig, use_container_width=True, key="sp3d_dome_chart")
        st.caption(
            "Drag to orbit, scroll to zoom. Gold marker animates the sun along "
            f"the {day_label} arc (use ▶ Play or the hour slider)."
        )
    else:
        fig = build_stereographic_figure(geom, points, color_by)
        st.plotly_chart(fig, use_container_width=True, key="sp3d_stereo_chart")
        st.caption(
            "Stereographic projection of the sky dome: r = cos(alt) / (1 + sin(alt)). "
            "The zenith maps to the center, the horizon to the outer circle."
        )
