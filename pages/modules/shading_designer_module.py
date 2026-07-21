"""Component analysis — interactive shading device designer.

Andrew Marsh-style window shading designer:
  • Parametric window + overhang + side-fin geometry, rendered in 3D
  • Exact shadow polygons on the window plane (shapely boolean geometry,
    not grid sampling) animated hour-by-hour for a design day
  • Shading mask (polar dot-matrix of shade fraction over the whole sky)
    with the sun-path day arcs overlaid
  • Annual performance from the EPW: % of beam energy blocked per month,
    hot-hour vs cold-hour effectiveness, auto-sized overhang suggestion

Wall-plane coordinates: x = lateral (m, + right when viewed from outside),
y = height (m). The facade faces `facade_azimuth` (compass, N=0°, cw).
HSA = horizontal shadow angle (sun azimuth relative to facade normal),
VSA = vertical shadow angle: tan(VSA) = tan(altitude) / cos(HSA).
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pvlib
from .st_compat import st
from shapely.geometry import Polygon
from shapely.ops import unary_union

BRAND = "#a85c42"

ORIENTATIONS = {
    "North (0°)": 0.0, "North-East (45°)": 45.0, "East (90°)": 90.0,
    "South-East (135°)": 135.0, "South (180°)": 180.0,
    "South-West (225°)": 225.0, "West (270°)": 270.0, "North-West (315°)": 315.0,
}


# ── Solar position (cached once per site) ─────────────────────────────────────

def _resolve_tz(tz_str: str):
    try:
        import pytz
        return pytz.timezone(tz_str)
    except Exception:
        return "UTC"


@st.cache_data(show_spinner="Computing solar positions…")
def hourly_sun(lat: float, lon: float, tz_str: str) -> pd.DataFrame:
    """Altitude/azimuth for every hour of year 2020 (matches EPW hours)."""
    tz = _resolve_tz(tz_str)
    times = pd.date_range("2020-01-01 00:30", "2020-12-31 23:30", freq="1h",
                          tz=tz)
    sp = pvlib.solarposition.get_solarposition(times, lat, lon)
    return pd.DataFrame({
        "month": times.month, "day": times.day, "hour": times.hour,
        "altitude": sp["apparent_elevation"].to_numpy(),
        "azimuth": sp["azimuth"].to_numpy(),
    })


@st.cache_data(show_spinner=False)
def design_day_sun(lat: float, lon: float, tz_str: str, month: int,
                   step_min: int = 20) -> pd.DataFrame:
    tz = _resolve_tz(tz_str)
    times = pd.date_range(f"2020-{month:02d}-21 00:00",
                          f"2020-{month:02d}-21 23:59",
                          freq=f"{step_min}min", tz=tz)
    sp = pvlib.solarposition.get_solarposition(times, lat, lon)
    out = pd.DataFrame({
        "time": times.tz_localize(None),
        "altitude": sp["apparent_elevation"].to_numpy(),
        "azimuth": sp["azimuth"].to_numpy(),
    })
    return out[out["altitude"] > 0.5].reset_index(drop=True)


# ── Shadow geometry (exact, wall-plane) ───────────────────────────────────────

def shadow_polys_on_wall(geom: dict, hsa_deg: float, vsa_deg: float):
    """Shadow parallelograms cast on the wall plane by overhang and fins.

    geom keys: win_w, win_h, sill, oh_depth, oh_gap, oh_ext,
               fin_l_depth, fin_r_depth (all metres; 0 disables an element).
    Window is centred at x=0, spans y = sill .. sill+win_h.
    Returns list of shapely Polygons (may be empty).
    """
    tan_h = np.tan(np.radians(np.clip(hsa_deg, -89.9, 89.9)))
    tan_v = np.tan(np.radians(np.clip(vsa_deg, 0.1, 89.9)))
    polys = []

    w2 = geom["win_w"] / 2.0
    head = geom["sill"] + geom["win_h"]

    if geom["oh_depth"] > 0:
        ho = head + geom["oh_gap"]
        xl, xr = -w2 - geom["oh_ext"], w2 + geom["oh_ext"]
        d, dx = geom["oh_depth"], geom["oh_depth"] * tan_h
        polys.append(Polygon([
            (xl, ho), (xr, ho),
            (xr - dx, ho - d * tan_v), (xl - dx, ho - d * tan_v),
        ]))

    for side, depth in (("l", geom["fin_l_depth"]), ("r", geom["fin_r_depth"])):
        if depth <= 0:
            continue
        xf = -w2 if side == "l" else w2
        yb, yt = geom["sill"], head + geom["oh_gap"]
        dx, dy = depth * tan_h, depth * tan_v
        polys.append(Polygon([
            (xf, yb), (xf, yt), (xf - dx, yt - dy), (xf - dx, yb - dy),
        ]))

    return [p for p in polys if p.is_valid and p.area > 1e-9]


def window_shade_fraction(geom: dict, hsa_deg: float, vsa_deg: float) -> float:
    """Fraction of the window area shaded (exact polygon intersection)."""
    w2 = geom["win_w"] / 2.0
    win = Polygon([(-w2, geom["sill"]), (w2, geom["sill"]),
                   (w2, geom["sill"] + geom["win_h"]),
                   (-w2, geom["sill"] + geom["win_h"])])
    polys = shadow_polys_on_wall(geom, hsa_deg, vsa_deg)
    if not polys:
        return 0.0
    shaded = unary_union(polys).intersection(win)
    return float(shaded.area / win.area)


def relative_angles(altitude, azimuth, facade_az):
    """HSA (deg, signed) and VSA (deg) for sun (alt, az) vs facade azimuth.

    Returns (hsa, vsa, facing) where facing=False means the sun is behind
    the facade plane.
    """
    hsa = ((np.asarray(azimuth) - facade_az) + 180) % 360 - 180
    facing = np.abs(hsa) < 90.0
    alt = np.asarray(altitude, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        vsa = np.degrees(np.arctan(
            np.tan(np.radians(np.clip(alt, 0, 89.9)))
            / np.clip(np.cos(np.radians(hsa)), 1e-6, None)
        ))
    return hsa, np.clip(vsa, 0, 89.9), facing & (alt > 0)


# ── Annual performance ────────────────────────────────────────────────────────

def annual_shading_performance(df: pd.DataFrame, sun: pd.DataFrame,
                               geom: dict, facade_az: float) -> pd.DataFrame:
    """Per-hour beam irradiance on the window and fraction blocked.

    Joins EPW hours to solar positions on (month, day, hour).
    """
    d = df.merge(sun, left_on=["month", "Day", "hour"],
                 right_on=["month", "day", "hour"], how="inner")
    hsa, vsa, facing = relative_angles(d["altitude"], d["azimuth"], facade_az)
    cos_inc = (np.cos(np.radians(d["altitude"]))
               * np.cos(np.radians(hsa))).to_numpy()
    beam = np.where(facing, d["direct_normal_irradiance"].to_numpy()
                    * np.clip(cos_inc, 0, None), 0.0)

    frac = np.zeros(len(d))
    active = np.flatnonzero(facing & (beam > 1.0))
    for i in active:
        frac[i] = window_shade_fraction(geom, float(hsa[i]), float(vsa[i]))

    out = d[["month", "hour", "dry_bulb_temperature"]].copy()
    out["beam_wm2"] = beam
    out["blocked_wm2"] = beam * frac
    out["shade_fraction"] = frac
    return out


def build_monthly_block_figure(perf: pd.DataFrame) -> go.Figure:
    m = perf.groupby("month").agg(beam=("beam_wm2", "sum"),
                                  blocked=("blocked_wm2", "sum"))
    m = m.reindex(range(1, 13), fill_value=0.0)
    admitted = (m["beam"] - m["blocked"]) / 1000.0
    blocked = m["blocked"] / 1000.0
    pct = np.where(m["beam"] > 0, 100.0 * m["blocked"] / m["beam"], 0.0)
    x = [calendar.month_abbr[i] for i in m.index]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=blocked, name="Blocked by shading",
                         marker_color="#5c6b73",
                         hovertemplate="%{y:.0f} kWh/m²<extra>blocked</extra>"))
    fig.add_trace(go.Bar(x=x, y=admitted, name="Admitted to glass",
                         marker_color="#ffbf00",
                         hovertemplate="%{y:.0f} kWh/m²<extra>admitted</extra>"))
    fig.add_trace(go.Scatter(x=x, y=pct, name="% blocked", yaxis="y2",
                             mode="lines+markers",
                             line=dict(color=BRAND, width=2.5),
                             hovertemplate="%{y:.0f}%<extra>blocked</extra>"))
    fig.update_layout(
        barmode="stack", height=400, plot_bgcolor="white",
        yaxis=dict(title="Beam energy on window (kWh/m²)"),
        yaxis2=dict(title="% blocked", overlaying="y", side="right",
                    range=[0, 105], showgrid=False),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=40, r=50, t=40, b=10),
        title=dict(text="Monthly beam radiation: blocked vs admitted",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig


# ── Shading mask ──────────────────────────────────────────────────────────────

def build_shading_mask_figure(geom: dict, facade_az: float,
                              day_arcs: dict[int, pd.DataFrame]) -> go.Figure:
    """Polar dot-matrix shading mask with sun-path day arcs overlaid."""
    az_grid = np.arange(-88, 89, 4.0)   # relative to facade
    alt_grid = np.arange(2, 89, 4.0)
    A, H = np.meshgrid(az_grid, alt_grid)
    frac = np.zeros_like(A, dtype=float)
    for i in range(A.shape[0]):
        for j in range(A.shape[1]):
            _, vsa, _ = relative_angles(H[i, j], facade_az + A[i, j], facade_az)
            frac[i, j] = window_shade_fraction(geom, A[i, j], float(vsa))

    theta = (facade_az + A).ravel() % 360
    r = 90.0 - H.ravel()
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        theta=theta, r=r, mode="markers",
        marker=dict(size=6, color=frac.ravel(), colorscale="Greys",
                    cmin=0, cmax=1, symbol="square",
                    colorbar=dict(title="shaded", thickness=12, len=0.6,
                                  tickformat=".0%")),
        customdata=np.stack([H.ravel(), (facade_az + A).ravel() % 360,
                             frac.ravel() * 100], axis=1),
        hovertemplate=("Alt %{customdata[0]:.0f}° / Az %{customdata[1]:.0f}°"
                       "<br>%{customdata[2]:.0f}% of window shaded"
                       "<extra></extra>"),
        name="Shade fraction", showlegend=False,
    ))
    for m, arc in day_arcs.items():
        emphasize = m in (6, 12)
        fig.add_trace(go.Scatterpolar(
            theta=arc["azimuth"], r=90 - arc["altitude"], mode="lines",
            line=dict(color="#e05c2a" if m == 6 else
                      ("#2171b5" if m == 12 else "rgba(100,120,140,0.5)"),
                      width=2.5 if emphasize else 1),
            name=f"{calendar.month_abbr[m]} 21",
            showlegend=emphasize, hoverinfo="name",
        ))
    fig.update_layout(
        height=520,
        polar=dict(
            radialaxis=dict(range=[0, 90], tickvals=[0, 30, 60, 90],
                            ticktext=["90°", "60°", "30°", "0°"],
                            tickfont=dict(size=9)),
            angularaxis=dict(direction="clockwise", rotation=90,
                             tickvals=[0, 45, 90, 135, 180, 225, 270, 315],
                             ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW"]),
        ),
        legend=dict(orientation="h", y=-0.08),
        margin=dict(l=40, r=40, t=40, b=20),
        title=dict(text="Shading mask — darker sky = window protected",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig


# ── 3D window + device visual ─────────────────────────────────────────────────

def _quad(v, color, opacity=1.0, name=""):
    v = np.asarray(v, dtype=float)
    return go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=[0, 0], j=[1, 2],
                     k=[2, 3], color=color, opacity=opacity, name=name,
                     hoverinfo="name" if name else "skip", showlegend=False,
                     flatshading=True)


def build_window_3d_figure(geom: dict, sun_day: pd.DataFrame) -> go.Figure:
    """3D view of wall/window/devices; animated shadow polys on the glass.

    3D axes: x = lateral, y = out from facade, z = height.
    """
    w2 = geom["win_w"] / 2.0
    sill, head = geom["sill"], geom["sill"] + geom["win_h"]
    ho = head + geom["oh_gap"]
    wall_w = geom["win_w"] * 2.2 + 2 * geom["oh_ext"]
    wall_h = ho + max(1.0, geom["win_h"] * 0.4)

    static = [
        _quad([(-wall_w / 2, 0, 0), (wall_w / 2, 0, 0),
               (wall_w / 2, 0, wall_h), (-wall_w / 2, 0, wall_h)],
              "#e8dfd3", name="Wall"),
        _quad([(-w2, 0.01, sill), (w2, 0.01, sill),
               (w2, 0.01, head), (-w2, 0.01, head)],
              "#9ec9e8", opacity=0.95, name="Window"),
    ]
    if geom["oh_depth"] > 0:
        xl, xr = -w2 - geom["oh_ext"], w2 + geom["oh_ext"]
        static.append(_quad([(xl, 0, ho), (xr, 0, ho),
                             (xr, geom["oh_depth"], ho),
                             (xl, geom["oh_depth"], ho)],
                            "#a85c42", name="Overhang"))
    for side, depth in (("l", geom["fin_l_depth"]), ("r", geom["fin_r_depth"])):
        if depth <= 0:
            continue
        xf = -w2 if side == "l" else w2
        static.append(_quad([(xf, 0, sill), (xf, depth, sill),
                             (xf, depth, ho), (xf, 0, ho)],
                            "#a85c42", name=f"Fin {side.upper()}"))

    n_static = len(static)

    win_poly = Polygon([(-w2, sill), (w2, sill), (w2, head), (-w2, head)])

    def shade_traces(row):
        hsa, vsa, facing = relative_angles(row["altitude"], row["azimuth"],
                                           geom["facade_az"])
        traces, pct = [], 0.0
        if bool(facing):
            polys = shadow_polys_on_wall(geom, float(hsa), float(vsa))
            if polys:
                inter = unary_union(polys).intersection(win_poly)
                pct = 100.0 * inter.area / win_poly.area
                geoms = getattr(inter, "geoms", [inter]) if not inter.is_empty else []
                for gpoly in geoms:
                    xy = np.asarray(gpoly.exterior.coords)
                    n = len(xy)
                    traces.append(go.Mesh3d(
                        x=xy[:, 0], y=np.full(n, 0.02), z=xy[:, 1],
                        i=np.zeros(n - 2, dtype=int),
                        j=np.arange(1, n - 1), k=np.arange(2, n),
                        color="#2c3e50", opacity=0.55, name="Shade",
                        hoverinfo="name", showlegend=False,
                    ))
        # pad to fixed trace count (2 shadow slots)
        while len(traces) < 2:
            traces.append(go.Mesh3d(x=[0, 0, 0], y=[0, 0, 0], z=[0, 0, 0],
                                    i=[0], j=[1], k=[2], opacity=0,
                                    hoverinfo="skip", showlegend=False))
        label = go.Scatter3d(
            x=[0], y=[geom["oh_depth"] + 0.4], z=[wall_h + 0.2], mode="text",
            text=[f"{row['time']:%H:%M} — {pct:.0f}% shaded"],
            textfont=dict(size=14, color=BRAND), hoverinfo="skip",
            showlegend=False,
        )
        return traces[:2] + [label]

    if sun_day.empty:
        fig = go.Figure(data=static)
    else:
        mid = sun_day.iloc[len(sun_day) // 2]
        fig = go.Figure(data=static + shade_traces(mid))
        frames = [go.Frame(name=f"{row['time']:%H:%M}",
                           data=shade_traces(row),
                           traces=[n_static, n_static + 1, n_static + 2])
                  for _, row in sun_day.iterrows()]
        fig.frames = frames
        fig.update_layout(
            updatemenus=[dict(
                type="buttons", direction="left", x=0.0, y=1.12,
                buttons=[
                    dict(label="▶ Play day", method="animate",
                         args=[None, dict(frame=dict(duration=150, redraw=True),
                                          transition=dict(duration=0),
                                          fromcurrent=True)]),
                    dict(label="⏸ Pause", method="animate",
                         args=[[None], dict(frame=dict(duration=0, redraw=False),
                                            mode="immediate")]),
                ],
            )],
            sliders=[dict(
                x=0.16, y=1.07, len=0.84, pad=dict(t=0),
                currentvalue=dict(prefix="☀ ", font=dict(size=12, color=BRAND)),
                steps=[dict(label=f.name, method="animate",
                            args=[[f.name], dict(frame=dict(duration=0,
                                                            redraw=True),
                                                 mode="immediate")])
                       for f in frames],
            )],
        )

    fig.update_layout(
        height=560,
        scene=dict(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(visible=False), aspectmode="data",
            camera=dict(eye=dict(x=0.35, y=1.9, z=0.25),
                        up=dict(x=0, y=0, z=1)),
            bgcolor="rgb(240,246,252)",
        ),
        margin=dict(l=0, r=0, t=70, b=0),
        showlegend=False,
    )
    return fig


# ── Streamlit render ──────────────────────────────────────────────────────────

def render(df: pd.DataFrame, metadata: dict):
    city = metadata.get("city") or "site"
    lat = float(metadata.get("latitude") or 0.0)
    lon = float(metadata.get("longitude") or 0.0)
    tz = metadata.get("timezone", "UTC")

    st.markdown(
        f'<div class="section-title">Component Analysis — window shading designer ({city})</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        orient = st.selectbox("Facade orientation", list(ORIENTATIONS.keys()),
                              index=4, key="shd_orient")
        facade_az = ORIENTATIONS[orient]
    with c2:
        win_w = st.number_input("Window width (m)", 0.5, 10.0, 2.4, 0.1,
                                key="shd_ww")
        win_h = st.number_input("Window height (m)", 0.5, 6.0, 1.5, 0.1,
                                key="shd_wh")
    with c3:
        oh_depth = st.number_input("Overhang depth (m)", 0.0, 4.0, 0.6, 0.1,
                                   key="shd_od")
        oh_gap = st.number_input("Gap above window (m)", 0.0, 1.5, 0.1, 0.05,
                                 key="shd_og")
        oh_ext = st.number_input("Overhang side extension (m)", 0.0, 2.0, 0.3,
                                 0.1, key="shd_oe")
    with c4:
        fin_l = st.number_input("Left fin depth (m)", 0.0, 3.0, 0.0, 0.1,
                                key="shd_fl")
        fin_r = st.number_input("Right fin depth (m)", 0.0, 3.0, 0.0, 0.1,
                                key="shd_fr")
        day_label = st.selectbox("Design day",
                                 [f"{calendar.month_abbr[m]} 21"
                                  for m in range(1, 13)],
                                 index=5, key="shd_day")

    geom = dict(win_w=win_w, win_h=win_h, sill=0.9, oh_depth=oh_depth,
                oh_gap=oh_gap, oh_ext=oh_ext, fin_l_depth=fin_l,
                fin_r_depth=fin_r, facade_az=facade_az)

    month = list(calendar.month_abbr).index(day_label.split()[0])
    sun_day = design_day_sun(lat, lon, tz, month)

    col3d, colmask = st.columns([1.15, 1])
    with col3d:
        st.plotly_chart(build_window_3d_figure(geom, sun_day),
                        use_container_width=True, key="shd_3d")
        st.caption("Play the design day: the dark patch is the exact shadow "
                   "your devices cast on the glass.")
    with colmask:
        day_arcs = {m: design_day_sun(lat, lon, tz, m)
                    for m in (3, 6, 9, 12)}
        st.plotly_chart(build_shading_mask_figure(geom, facade_az, day_arcs),
                        use_container_width=True, key="shd_mask")
        st.caption("Where a day arc crosses dark cells, the window is "
                   "protected at that date/time; light cells = exposed.")

    # ── Annual performance ────────────────────────────────────────────────────
    st.markdown('<div class="section-title">Annual shading performance</div>',
                unsafe_allow_html=True)

    sun = hourly_sun(lat, lon, tz)
    perf = annual_shading_performance(df, sun, geom, facade_az)

    hot = perf[perf["dry_bulb_temperature"] >= 28.0]
    cold = perf[perf["dry_bulb_temperature"] <= 18.0]
    tot_beam = perf["beam_wm2"].sum()
    pct_all = 100.0 * perf["blocked_wm2"].sum() / tot_beam if tot_beam else 0.0
    pct_hot = (100.0 * hot["blocked_wm2"].sum() / hot["beam_wm2"].sum()
               if hot["beam_wm2"].sum() else 0.0)
    pct_cold = (100.0 * cold["blocked_wm2"].sum() / cold["beam_wm2"].sum()
                if cold["beam_wm2"].sum() else 0.0)

    # auto-size suggestion: overhang to fully shade at summer-solstice noon
    noon_month = 6 if lat >= 0 else 12
    noon = design_day_sun(lat, lon, tz, noon_month)
    suggestion = None
    if not noon.empty:
        peak = noon.loc[noon["altitude"].idxmax()]
        _, vsa_noon, facing = relative_angles(peak["altitude"], peak["azimuth"],
                                              facade_az)
        if bool(facing) and vsa_noon > 5:
            suggestion = (win_h + oh_gap) / np.tan(np.radians(float(vsa_noon)))

    k1, k2, k3, k4 = st.columns(4)
    for col, label, value, meta in [
        (k1, "Beam blocked (annual)", f"{pct_all:.0f}%",
         f"of {tot_beam/1000:.0f} kWh/m² on glass"),
        (k2, "Blocked when hot (≥28°C)", f"{pct_hot:.0f}%",
         "higher is better"),
        (k3, "Blocked when cold (≤18°C)", f"{pct_cold:.0f}%",
         "lower is better (free solar heat)"),
        (k4, "Full-shade overhang @ solstice noon",
         f"{suggestion:.2f} m" if suggestion else "n/a",
         "depth for 100% noon shade" if suggestion
         else "sun behind facade at noon"),
    ]:
        with col:
            st.markdown(
                f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
                f'<div class="kpi-value" style="font-size:22px;">{value}</div>'
                f'<div class="kpi-meta">{meta}</div></div>',
                unsafe_allow_html=True,
            )

    st.plotly_chart(build_monthly_block_figure(perf),
                    use_container_width=True, key="shd_monthly")
    st.caption(
        "A well-tuned device blocks summer beam and admits winter beam: aim "
        "for a high hot-hour block %, a low cold-hour block %, and a % "
        "blocked line that dips through the heating season."
    )
