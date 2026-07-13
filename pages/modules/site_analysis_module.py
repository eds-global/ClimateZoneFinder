"""Site analysis — 3D massing, animated shadow study, facade solar exposure.

Brings site-scale design questions into the climate dashboard:
  • 3D building massing (dimensions + orientation) on a ground plane
  • Hour-by-hour animated shadow study for any design day (plotly frames)
  • Optional neighbouring block to visualise overshadowing
  • Annual/monthly solar irradiation on every facade + roof
    (pvlib Hay-Davies transposition of the EPW's DNI/DHI/GHI)
  • Wind exposure per facade from the EPW wind record

Geometry convention: x = east (m), y = north (m), z = up (m).
Facade azimuths follow the compass convention (N=0°, E=90°, cw).
Limitations (stated in-UI): facade irradiation is unshaded — the neighbour
block affects the shadow animation but not the irradiation totals.
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pvlib
import streamlit as st
from shapely.geometry import MultiPoint

BRAND = "#a85c42"
DESIGN_DAYS = {f"{calendar.month_abbr[m]} 21": m for m in range(1, 13)}


# ── Timezone / solar helpers ──────────────────────────────────────────────────

def _resolve_tz(tz_str: str):
    """Return a tz usable by pandas; fall back to UTC."""
    try:
        import pytz
        return pytz.timezone(tz_str)
    except Exception:
        return "UTC"


@st.cache_data(show_spinner=False)
def day_solar_positions(lat: float, lon: float, tz_str: str, month: int,
                        step_min: int = 20) -> pd.DataFrame:
    """Solar altitude/azimuth for the 21st of `month` (year 2020)."""
    tz = _resolve_tz(tz_str)
    times = pd.date_range(f"2020-{month:02d}-21 00:00", f"2020-{month:02d}-21 23:59",
                          freq=f"{step_min}min", tz=tz)
    sp = pvlib.solarposition.get_solarposition(times, lat, lon)
    out = pd.DataFrame({
        "time": times.tz_localize(None),
        "altitude": sp["apparent_elevation"].to_numpy(),
        "azimuth": sp["azimuth"].to_numpy(),
    })
    return out[out["altitude"] > 0.5].reset_index(drop=True)


@st.cache_data(show_spinner="Computing facade irradiation…")
def facade_irradiation(df: pd.DataFrame, lat: float, lon: float, tz_str: str,
                       rotation: float) -> pd.DataFrame:
    """Monthly plane-of-array irradiation (kWh/m²) for 4 facades + roof.

    Hay-Davies transposition of EPW DNI/DHI/GHI. Unshaded surfaces.
    """
    tz = _resolve_tz(tz_str)
    times = pd.to_datetime(dict(
        year=2020, month=df["month"].to_numpy(),
        day=df["datetime"].dt.day.to_numpy(), hour=df["hour"].to_numpy(),
    ))
    try:
        times = times.dt.tz_localize(tz, nonexistent="NaT", ambiguous="NaT")
    except Exception:
        times = times.dt.tz_localize("UTC")
    idx = pd.DatetimeIndex(times)
    valid = ~idx.isna()
    idx = idx[valid]
    d = df.loc[valid]

    sp = pvlib.solarposition.get_solarposition(idx, lat, lon)
    dni_extra = pvlib.irradiance.get_extra_radiation(idx)

    surfaces = {
        "North facade": (90.0, (0.0 + rotation) % 360),
        "East facade": (90.0, (90.0 + rotation) % 360),
        "South facade": (90.0, (180.0 + rotation) % 360),
        "West facade": (90.0, (270.0 + rotation) % 360),
        "Roof (flat)": (0.0, 180.0),
    }
    rows = []
    for name, (tilt, az) in surfaces.items():
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt, surface_azimuth=az,
            solar_zenith=sp["apparent_zenith"], solar_azimuth=sp["azimuth"],
            dni=d["direct_normal_irradiance"].to_numpy(),
            ghi=d["global_horizontal_irradiance"].to_numpy(),
            dhi=d["diffuse_horizontal_irradiance"].to_numpy(),
            dni_extra=dni_extra, model="haydavies",
        )
        poa_wh = pd.Series(np.clip(poa["poa_global"].to_numpy(), 0, None),
                           index=idx)
        monthly = poa_wh.groupby(idx.month).sum() / 1000.0  # kWh/m²
        for m in range(1, 13):
            rows.append({"Surface": name, "month": m,
                         "kwh_m2": float(monthly.get(m, 0.0))})
    return pd.DataFrame(rows)


# ── Geometry ──────────────────────────────────────────────────────────────────

def box_corners(cx: float, cy: float, length: float, width: float,
                height: float, rot_deg: float):
    """Footprint (4×3) and roof (4×3) corner arrays for a rotated box.

    `length` runs east-west at rot=0; rotation is clockwise from north.
    """
    hx, hy = length / 2.0, width / 2.0
    pts = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]], dtype=float)
    th = np.radians(rot_deg)
    rot = np.array([[np.cos(th), np.sin(th)], [-np.sin(th), np.cos(th)]])
    xy = pts @ rot.T + np.array([cx, cy])
    base = np.column_stack([xy, np.zeros(4)])
    top = np.column_stack([xy, np.full(4, height)])
    return base, top


def shadow_polygon(base: np.ndarray, top: np.ndarray,
                   altitude: float, azimuth: float) -> np.ndarray:
    """Ground shadow outline (convex hull) of a box for a given sun position."""
    alt = np.radians(max(altitude, 0.6))
    az = np.radians(azimuth)
    run = 1.0 / np.tan(alt)
    dx, dy = -np.sin(az) * run, -np.cos(az) * run
    proj_top = top[:, :2] + top[:, 2:3] * np.array([dx, dy])
    pts = np.vstack([base[:, :2], proj_top])
    hull = MultiPoint([tuple(p) for p in pts]).convex_hull
    try:
        return np.asarray(hull.exterior.coords)
    except AttributeError:  # degenerate (sun overhead)
        return base[:, :2]


def _fan_mesh(poly_xy: np.ndarray, z: float, color: str, opacity: float,
              name: str) -> go.Mesh3d:
    """Fan-triangulated flat Mesh3d from a convex polygon outline."""
    n = len(poly_xy)
    i = np.zeros(n - 2, dtype=int)
    j = np.arange(1, n - 1)
    k = np.arange(2, n)
    return go.Mesh3d(
        x=poly_xy[:, 0], y=poly_xy[:, 1], z=np.full(n, z),
        i=i, j=j, k=k, color=color, opacity=opacity,
        hoverinfo="name", name=name, showlegend=False,
    )


def _box_mesh(base: np.ndarray, top: np.ndarray, color: str,
              opacity: float = 1.0, name: str = "Building",
              facecolors: dict | None = None) -> list[go.Mesh3d]:
    """Box as Mesh3d traces. With `facecolors`, one trace per facade
    ({'N','E','S','W','roof'} → css color) so faces can encode data."""
    verts = np.vstack([base, top])  # 0-3 base, 4-7 top

    faces = {
        "S": [0, 1, 5, 4],   # -y side at rot=0 → faces south... (see note)
        "E": [1, 2, 6, 5],
        "N": [2, 3, 7, 6],
        "W": [3, 0, 4, 7],
        "roof": [4, 5, 6, 7],
    }
    # note: base order is (-x,-y),(x,-y),(x,y),(-x,y); -y edge faces south.
    traces = []
    for fname, vidx in faces.items():
        v = verts[vidx]
        col = (facecolors or {}).get(fname, color)
        traces.append(go.Mesh3d(
            x=v[:, 0], y=v[:, 1], z=v[:, 2],
            i=[0, 0], j=[1, 2], k=[2, 3],
            color=col, opacity=opacity, name=f"{name} {fname}",
            hoverinfo="name", showlegend=False, flatshading=True,
        ))
    return traces


# ── Figures ───────────────────────────────────────────────────────────────────

def build_shadow_study_figure(length, width, height, rotation, sun_day: pd.DataFrame,
                              neighbor: dict | None = None) -> go.Figure:
    """Animated 3D shadow study for one design day."""
    base, top = box_corners(0, 0, length, width, height, rotation)
    extent = max(length, width) * 1.5 + height * 3.0

    static = []
    # ground
    g = extent
    static.append(go.Mesh3d(
        x=[-g, g, g, -g], y=[-g, -g, g, g], z=[0, 0, 0, 0],
        i=[0, 0], j=[1, 2], k=[2, 3], color="#dde8d8", opacity=1.0,
        hoverinfo="skip", showlegend=False, name="ground",
    ))
    # compass labels
    static.append(go.Scatter3d(
        x=[0, g * 0.92, 0, -g * 0.92], y=[g * 0.92, 0, -g * 0.92, 0],
        z=[0.3] * 4, mode="text", text=["N", "E", "S", "W"],
        textfont=dict(size=16, color="#5c6b73"), hoverinfo="skip",
        showlegend=False,
    ))
    static += _box_mesh(base, top, "#c9b8a8", name="Building")
    # roof edge outline for crispness
    loop = np.vstack([top, top[:1]])
    static.append(go.Scatter3d(
        x=loop[:, 0], y=loop[:, 1], z=loop[:, 2], mode="lines",
        line=dict(color="#8a7a6a", width=3), hoverinfo="skip", showlegend=False,
    ))

    nb = None
    if neighbor:
        th = np.radians(neighbor["bearing"])
        cx = neighbor["distance"] * np.sin(th)
        cy = neighbor["distance"] * np.cos(th)
        nb = box_corners(cx, cy, neighbor["length"], neighbor["width"],
                         neighbor["height"], rotation)
        static += _box_mesh(nb[0], nb[1], "#b8c4cc", name="Neighbour")

    n_static = len(static)

    # animated traces: [our shadow, neighbour shadow (or empty), sun, ray]
    def frame_traces(row):
        traces = []
        poly = shadow_polygon(base, top, row["altitude"], row["azimuth"])
        traces.append(_fan_mesh(poly, 0.03, "#3a4750", 0.42, "Shadow"))
        if nb is not None:
            poly2 = shadow_polygon(nb[0], nb[1], row["altitude"], row["azimuth"])
            traces.append(_fan_mesh(poly2, 0.02, "#3a4750", 0.42, "Neighbour shadow"))
        alt, az = np.radians(row["altitude"]), np.radians(row["azimuth"])
        r = extent * 0.95
        sx, sy, sz = r * np.cos(alt) * np.sin(az), r * np.cos(alt) * np.cos(az), r * np.sin(alt)
        traces.append(go.Scatter3d(
            x=[sx], y=[sy], z=[sz], mode="markers",
            marker=dict(size=10, color="#ffb703", symbol="circle"),
            name="Sun", hovertemplate=(f"{row['time']:%H:%M}<br>"
                                       f"Alt {row['altitude']:.0f}° / "
                                       f"Az {row['azimuth']:.0f}°<extra>Sun</extra>"),
            showlegend=False,
        ))
        traces.append(go.Scatter3d(
            x=[sx, 0], y=[sy, 0], z=[sz, height / 2],
            mode="lines", line=dict(color="rgba(255,183,3,0.55)", width=3),
            hoverinfo="skip", showlegend=False,
        ))
        return traces

    if sun_day.empty:
        fig = go.Figure(data=static)
    else:
        first = frame_traces(sun_day.iloc[0])
        n_anim = len(first)
        fig = go.Figure(data=static + first)
        frames = []
        for _, row in sun_day.iterrows():
            frames.append(go.Frame(
                name=f"{row['time']:%H:%M}",
                data=frame_traces(row),
                traces=list(range(n_static, n_static + n_anim)),
            ))
        fig.frames = frames
        fig.update_layout(
            updatemenus=[dict(
                type="buttons", direction="left", x=0.0, y=1.08,
                buttons=[
                    dict(label="▶ Play day", method="animate",
                         args=[None, dict(frame=dict(duration=120, redraw=True),
                                          transition=dict(duration=0),
                                          fromcurrent=True)]),
                    dict(label="⏸ Pause", method="animate",
                         args=[[None], dict(frame=dict(duration=0, redraw=False),
                                            mode="immediate")]),
                ],
            )],
            sliders=[dict(
                x=0.15, y=1.04, len=0.85, pad=dict(t=0),
                currentvalue=dict(prefix="☀ ", font=dict(size=13, color=BRAND)),
                steps=[dict(label=f.name, method="animate",
                            args=[[f.name], dict(frame=dict(duration=0, redraw=True),
                                                 mode="immediate")])
                       for f in frames],
            )],
        )

    fig.update_layout(
        height=620,
        scene=dict(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            aspectmode="data",
            camera=dict(eye=dict(x=1.15, y=-1.35, z=0.55)),
            bgcolor="rgb(235,243,250)",
        ),
        margin=dict(l=0, r=0, t=60, b=0),
        showlegend=False,
    )
    return fig


def build_irradiated_massing_figure(length, width, height, rotation,
                                    annual: pd.Series) -> go.Figure:
    """Building box with facades colored by annual irradiation (kWh/m²)."""
    import plotly.express as px

    base, top = box_corners(0, 0, length, width, height, rotation)
    vmin, vmax = float(annual.min()), float(annual.max())

    def col(v):
        t = 0.5 if vmax == vmin else (v - vmin) / (vmax - vmin)
        return px.colors.sample_colorscale("Inferno", [0.15 + 0.75 * t])[0]

    facecolors = {
        "N": col(annual["North facade"]), "E": col(annual["East facade"]),
        "S": col(annual["South facade"]), "W": col(annual["West facade"]),
        "roof": col(annual["Roof (flat)"]),
    }
    traces = _box_mesh(base, top, "#ccc", name="Facade", facecolors=facecolors)
    g = max(length, width) * 1.1
    traces.append(go.Mesh3d(
        x=[-g, g, g, -g], y=[-g, -g, g, g], z=[-0.02] * 4,
        i=[0, 0], j=[1, 2], k=[2, 3], color="#e8ece4", opacity=1.0,
        hoverinfo="skip", showlegend=False,
    ))
    traces.append(go.Scatter3d(
        x=[0, g, 0, -g], y=[g, 0, -g, 0], z=[0.3] * 4, mode="text",
        text=["N", "E", "S", "W"], textfont=dict(size=14, color="#5c6b73"),
        hoverinfo="skip", showlegend=False,
    ))
    # colorbar via invisible scatter
    traces.append(go.Scatter3d(
        x=[0], y=[0], z=[0], mode="markers",
        marker=dict(size=0.001, color=[vmin, vmax], colorscale="Inferno",
                    cmin=vmin, cmax=vmax,
                    colorbar=dict(title="kWh/m²·yr", thickness=14, len=0.6)),
        hoverinfo="skip", showlegend=False,
    ))
    fig = go.Figure(data=traces)
    fig.update_layout(
        height=460,
        scene=dict(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(visible=False), aspectmode="data",
            camera=dict(eye=dict(x=1.3, y=-1.3, z=0.65)),
            bgcolor="white",
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        title=dict(text="Annual solar irradiation by surface",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig


def build_monthly_irradiation_figure(irr: pd.DataFrame) -> go.Figure:
    colors = {"North facade": "#4a90d9", "East facade": "#8fbc45",
              "South facade": "#e05c2a", "West facade": "#9467bd",
              "Roof (flat)": "#ffbf00"}
    fig = go.Figure()
    for surf, col in colors.items():
        d = irr[irr["Surface"] == surf]
        fig.add_trace(go.Scatter(
            x=[calendar.month_abbr[m] for m in d["month"]], y=d["kwh_m2"],
            name=surf, mode="lines+markers", line=dict(color=col, width=2.5),
            hovertemplate="%{y:.0f} kWh/m²<extra>" + surf + "</extra>",
        ))
    fig.update_layout(
        height=380, plot_bgcolor="white", yaxis_title="kWh/m² per month",
        legend=dict(orientation="h", y=-0.2, font=dict(size=11)),
        margin=dict(l=40, r=10, t=40, b=10),
        title=dict(text="Monthly irradiation per surface",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig


def facade_wind_exposure(df: pd.DataFrame, rotation: float) -> pd.DataFrame:
    """% of useful-wind hours (≥1 m/s) striking each facade (±60° incidence)."""
    wd = df["wind_direction"].to_numpy(dtype=float)
    ws = df["wind_speed"].to_numpy(dtype=float)
    useful = ws >= 1.0
    rows = []
    for name, az in [("North facade", 0.0), ("East facade", 90.0),
                     ("South facade", 180.0), ("West facade", 270.0)]:
        fa = (az + rotation) % 360
        diff = np.abs(((wd - fa) + 180) % 360 - 180)
        hits = useful & (diff <= 60.0)
        rows.append({
            "Facade": name,
            "facade_azimuth": fa,
            "pct_hours": 100.0 * hits.sum() / max(len(df), 1),
            "mean_speed": float(ws[hits].mean()) if hits.any() else 0.0,
        })
    return pd.DataFrame(rows)


# ── Streamlit render ──────────────────────────────────────────────────────────

def render(df: pd.DataFrame, metadata: dict):
    city = metadata.get("city") or "site"
    lat = float(metadata.get("latitude") or 0.0)
    lon = float(metadata.get("longitude") or 0.0)
    tz = metadata.get("timezone", "UTC")

    st.markdown(
        f'<div class="section-title">Site Analysis — massing, shadows & solar exposure ({city})</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        length = st.number_input("Length E-W (m)", 4.0, 200.0, 30.0, 2.0,
                                 key="site_len")
    with c2:
        width = st.number_input("Width N-S (m)", 4.0, 200.0, 15.0, 2.0,
                                key="site_wid")
    with c3:
        height = st.number_input("Height (m)", 3.0, 150.0, 12.0, 1.0,
                                 key="site_hgt")
    with c4:
        rotation = st.slider("Rotation (° cw from N)", 0, 175, 0, 5,
                             key="site_rot")
    with c5:
        day_label = st.selectbox("Design day", list(DESIGN_DAYS.keys()),
                                 index=11, key="site_day",
                                 help="Dec 21 = worst-case shadows (N hemisphere)")

    with st.expander("➕ Neighbouring building (overshadowing)"):
        n1, n2, n3, n4 = st.columns(4)
        with n1:
            nb_on = st.toggle("Include neighbour", value=False, key="site_nb")
        with n2:
            nb_dist = st.number_input("Distance (m)", 5.0, 300.0, 25.0, 5.0,
                                      key="site_nb_dist")
        with n3:
            nb_bearing = st.slider("Bearing from site (°)", 0, 355, 180, 5,
                                   key="site_nb_bear")
        with n4:
            nb_height = st.number_input("Neighbour height (m)", 3.0, 200.0,
                                        24.0, 1.0, key="site_nb_hgt")

    neighbor = None
    if nb_on:
        neighbor = dict(distance=nb_dist, bearing=float(nb_bearing),
                        height=nb_height, length=20.0, width=15.0)

    sun_day = day_solar_positions(lat, lon, tz, DESIGN_DAYS[day_label])
    st.plotly_chart(
        build_shadow_study_figure(length, width, height, rotation, sun_day,
                                  neighbor),
        use_container_width=True, key="site_shadow",
    )
    st.caption(
        "Press **Play day** to sweep the sun across the chosen design day. "
        "Drag to orbit; the shadow is the exact ground projection of the massing."
    )

    # KPIs for the design day
    if not sun_day.empty:
        noon = sun_day.loc[sun_day["altitude"].idxmax()]
        shadow_len = height / np.tan(np.radians(max(noon["altitude"], 1.0)))
        k1, k2, k3, k4 = st.columns(4)
        for col, label, value, meta in [
            (k1, "Sunrise → sunset", f"{sun_day['time'].iloc[0]:%H:%M} – "
                                     f"{sun_day['time'].iloc[-1]:%H:%M}",
             day_label),
            (k2, "Max solar altitude", f"{noon['altitude']:.0f}°",
             f"at {noon['time']:%H:%M}"),
            (k3, "Noon shadow length", f"{shadow_len:.1f} m",
             f"for {height:.0f} m height"),
            (k4, "Daylight hours", f"{len(sun_day) * 20 / 60:.1f} h",
             "sun above horizon"),
        ]:
            with col:
                st.markdown(
                    f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
                    f'<div class="kpi-value" style="font-size:20px;">{value}</div>'
                    f'<div class="kpi-meta">{meta}</div></div>',
                    unsafe_allow_html=True,
                )

    # ── Solar exposure ────────────────────────────────────────────────────────
    st.markdown('<div class="section-title">Solar exposure by surface</div>',
                unsafe_allow_html=True)

    irr = facade_irradiation(df, lat, lon, tz, float(rotation))
    annual = irr.groupby("Surface")["kwh_m2"].sum()

    ca, cb = st.columns([1.1, 1])
    with ca:
        st.plotly_chart(
            build_irradiated_massing_figure(length, width, height, rotation,
                                            annual),
            use_container_width=True, key="site_irr3d",
        )
    with cb:
        st.plotly_chart(build_monthly_irradiation_figure(irr),
                        use_container_width=True, key="site_irr_monthly")

    hottest = annual.drop("Roof (flat)").idxmax()
    coolest = annual.drop("Roof (flat)").idxmin()
    st.info(
        f"**{hottest}** receives the most annual radiation "
        f"({annual[hottest]:.0f} kWh/m²) — prioritise shading and low SHGC "
        f"glazing there. **{coolest}** is the calmest solar face "
        f"({annual[coolest]:.0f} kWh/m²) — good for larger glazing and views. "
        f"Roof: {annual['Roof (flat)']:.0f} kWh/m²·yr available for PV.  \n"
        "*Irradiation totals are for unshaded surfaces (Hay-Davies sky model); "
        "the neighbour block is visual-only in this calculation.*"
    )

    # ── Wind exposure ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-title">Wind exposure by facade</div>',
                unsafe_allow_html=True)
    wexp = facade_wind_exposure(df, float(rotation))
    fig = go.Figure(go.Bar(
        x=wexp["Facade"], y=wexp["pct_hours"],
        marker_color=["#4a90d9", "#8fbc45", "#e05c2a", "#9467bd"],
        text=[f"{v:.0f}%" for v in wexp["pct_hours"]], textposition="outside",
        customdata=wexp["mean_speed"],
        hovertemplate="%{y:.1f}% of hours<br>mean %{customdata:.1f} m/s<extra></extra>",
    ))
    fig.update_layout(height=320, plot_bgcolor="white",
                      yaxis_title="% of hours with useful wind (≥1 m/s, ±60°)",
                      margin=dict(l=40, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True, key="site_wind")
    best = wexp.loc[wexp["pct_hours"].idxmax()]
    st.caption(
        f"**{best['Facade']}** catches useful wind {best['pct_hours']:.0f}% of "
        f"the year (mean {best['mean_speed']:.1f} m/s) — locate operable "
        "openings there and provide a cross-ventilation outlet on the "
        "opposite face."
    )
