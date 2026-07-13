"""Dry Bulb Temperature tab rendering module."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from .config import (
    ASHRAE_T_PMA_MIN, ASHRAE_T_PMA_MAX,
    ASHRAE_COMFORT_NEUTRAL_A, ASHRAE_COMFORT_NEUTRAL_B,
    COMFORT_BAND_80_PCT, COMFORT_BAND_90_PCT,
)


def calculate_ashrae_comfort(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Calculate ASHRAE adaptive comfort bands.

    Returns:
        (comfort_80_lower, comfort_80_upper, comfort_90_lower, comfort_90_upper)
        as daily rolling averages indexed by doy.
    """
    daily_avg = df.groupby("doy")["dry_bulb_temperature"].mean()
    # ASHRAE 55 prevailing mean outdoor temperature (7-day running mean)
    # Clipped to the model's valid applicability range: 10–33.5 °C
    t_running_mean = daily_avg.rolling(window=7, center=True, min_periods=1).mean().clip(
        ASHRAE_T_PMA_MIN, ASHRAE_T_PMA_MAX
    )
    # ASHRAE 55 adaptive comfort neutral temperature
    t_comfort = ASHRAE_COMFORT_NEUTRAL_A * t_running_mean + ASHRAE_COMFORT_NEUTRAL_B
    return (
        t_comfort - COMFORT_BAND_80_PCT,
        t_comfort + COMFORT_BAND_80_PCT,
        t_comfort - COMFORT_BAND_90_PCT,
        t_comfort + COMFORT_BAND_90_PCT,
    )


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
        _render_monthly_trend(df, start_date, end_date)
    elif active_tab == "Diurnal Profile":
        _render_diurnal_profile(df, start_hour, end_hour)
    elif active_tab == "Comfort Analysis":
        _render_comfort_analysis(df, daily_stats, start_date, end_date)
    elif active_tab == "Energy Metrics":
        _render_energy_metrics(df, start_date, end_date, start_hour, end_hour)
    elif active_tab == "Heatmap + 3D":
        _render_heatmap_3d(df)


# ─────────────────────────────────────────────────────────────────────────────
# "Heatmap + 3D" figure builders — pure functions (no Streamlit calls) so they
# can be unit-tested headlessly. humidity_module imports and reuses them.

_MONTH_LBL = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build_carpet_heatmap(
    df: pd.DataFrame,
    col: str,
    *,
    title: str,
    colorscale: str,
    unit: str,
    series_name: str,
    height: int = 420,
) -> go.Figure:
    """Annual carpet plot: day of year (x, real dates) × hour of day (y), colored by `col`."""
    pivot = df.pivot_table(index="hour", columns="doy", values=col, aggfunc="mean")
    pivot = pivot.reindex(index=range(24))
    # Actual calendar dates for each day-of-year so hover shows e.g. "05 Mar".
    # TMY files stitch months from different source years, so remap every date
    # onto a single leap display year to keep the axis monotonic.
    doy_dates = df.groupby("doy")["datetime"].first()
    x = doy_dates.reindex(pivot.columns).apply(
        lambda t: t.replace(year=2024, hour=0, minute=0) if pd.notna(t) else t
    ).values

    fig = go.Figure(go.Heatmap(
        x=x,
        y=list(pivot.index),
        z=pivot.values,
        colorscale=colorscale,
        colorbar=dict(title=unit),
        hovertemplate=(
            "<b>%{x|%d %b}</b><br>"
            "Hour: %{y:02d}:00<br>"
            + series_name + ": %{z:.1f} " + unit
            + "<extra></extra>"
        ),
    ))
    fig.update_layout(
        title=title,
        xaxis=dict(title=None, tickformat="%d %b", dtick="M1"),
        yaxis=dict(title="Hour of Day", dtick=3),
        height=height,
        template="plotly_white",
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return fig


def build_month_hour_surface(
    df: pd.DataFrame,
    col: str,
    *,
    title: str,
    colorscale: str,
    unit: str,
    series_name: str,
    height: int = 520,
) -> go.Figure:
    """3D surface of the monthly (1-12) × hourly (0-23) mean of `col`."""
    pivot = df.pivot_table(index="hour", columns="month", values=col, aggfunc="mean")
    pivot = pivot.reindex(index=range(24), columns=range(1, 13))

    fig = go.Figure(go.Surface(
        x=list(pivot.columns),
        y=list(pivot.index),
        z=pivot.values,
        colorscale=colorscale,
        colorbar=dict(title=unit, len=0.6),
        contours={"z": {"show": True, "usecolormap": True,
                        "highlightcolor": "#ffffff", "project_z": True}},
        hovertemplate=(
            "Month: %{x}<br>Hour: %{y}:00<br>"
            + f"Mean {series_name}: %{{z:.1f}} {unit}<extra></extra>"
        ),
    ))
    fig.update_layout(
        title=title,
        scene=dict(
            xaxis=dict(title="Month", tickmode="array",
                       tickvals=list(range(1, 13)), ticktext=_MONTH_LBL),
            yaxis=dict(title="Hour of Day", dtick=4),
            zaxis=dict(title=f"{series_name} ({unit})"),
            camera=dict(eye=dict(x=1.6, y=-1.7, z=0.9)),
        ),
        height=height,
        template="plotly_white",
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


def build_animated_diurnal(
    df: pd.DataFrame,
    col: str,
    *,
    title: str,
    unit: str,
    series_name: str,
    line_color: str,
    band_fill: str,
    height: int = 470,
) -> go.Figure:
    """Animated monthly diurnal profile: mean line + P10-P90 band, one frame per month."""
    stats = (
        df.groupby(["month", "hour"])[col]
        .agg(mean="mean",
             p10=lambda s: s.quantile(0.10),
             p90=lambda s: s.quantile(0.90))
        .reset_index()
    )
    months = sorted(int(m) for m in stats["month"].unique())

    # Fixed y-range across all frames so the axis doesn't rescale during play
    y_min = float(stats["p10"].min())
    y_max = float(stats["p90"].max())
    pad = max((y_max - y_min) * 0.08, 0.5)

    def _month_traces(m: int) -> list[go.Scatter]:
        d = stats[stats["month"] == m]
        return [
            go.Scatter(x=d["hour"], y=d["p90"], mode="lines",
                       line=dict(width=0), showlegend=False,
                       hovertemplate="Hour %{x}:00<br>P90: %{y:.1f} " + unit + "<extra></extra>"),
            go.Scatter(x=d["hour"], y=d["p10"], mode="lines",
                       line=dict(width=0), fill="tonexty", fillcolor=band_fill,
                       name="P10–P90 band",
                       hovertemplate="Hour %{x}:00<br>P10: %{y:.1f} " + unit + "<extra></extra>"),
            go.Scatter(x=d["hour"], y=d["mean"], mode="lines+markers",
                       name=f"Mean {series_name}",
                       line=dict(color=line_color, width=2.5), marker=dict(size=6),
                       hovertemplate="Hour %{x}:00<br>Mean: %{y:.1f} " + unit + "<extra></extra>"),
        ]

    frames = [go.Frame(data=_month_traces(m), name=_MONTH_LBL[m - 1]) for m in months]
    fig = go.Figure(data=_month_traces(months[0]), frames=frames)

    slider_steps = [
        dict(method="animate", label=_MONTH_LBL[m - 1],
             args=[[_MONTH_LBL[m - 1]],
                   {"frame": {"duration": 0, "redraw": True},
                    "mode": "immediate", "transition": {"duration": 0}}])
        for m in months
    ]

    fig.update_layout(
        title=title,
        xaxis=dict(title="Hour of Day", dtick=2, range=[-0.5, 23.5]),
        yaxis=dict(title=f"{series_name} ({unit})", range=[y_min - pad, y_max + pad]),
        hovermode="x unified",
        template="plotly_white",
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        updatemenus=[dict(
            type="buttons", direction="left",
            x=0.0, y=-0.22, xanchor="left", yanchor="top",
            pad=dict(r=10, t=10),
            buttons=[
                dict(label="Play", method="animate",
                     args=[None, {"frame": {"duration": 700, "redraw": True},
                                  "fromcurrent": True,
                                  "transition": {"duration": 250}}]),
                dict(label="Pause", method="animate",
                     args=[[None], {"frame": {"duration": 0, "redraw": False},
                                    "mode": "immediate"}]),
            ],
        )],
        sliders=[dict(
            active=0, x=0.18, y=-0.18, len=0.8, xanchor="left", yanchor="top",
            currentvalue=dict(prefix="Month: ", visible=True),
            steps=slider_steps,
        )],
        margin=dict(b=110),
    )
    return fig


def _render_heatmap_3d(df: pd.DataFrame) -> None:
    st.plotly_chart(
        build_carpet_heatmap(
            df, "dry_bulb_temperature",
            title="Annual Temperature Carpet Plot (Day × Hour)",
            colorscale="RdYlBu_r", unit="°C", series_name="Temperature",
        ),
        use_container_width=True,
    )
    st.plotly_chart(
        build_month_hour_surface(
            df, "dry_bulb_temperature",
            title="Mean Temperature Surface — Month × Hour",
            colorscale="RdYlBu_r", unit="°C", series_name="Temperature",
        ),
        use_container_width=True,
    )
    st.plotly_chart(
        build_animated_diurnal(
            df, "dry_bulb_temperature",
            title="Monthly Diurnal Temperature Profile (animated)",
            unit="°C", series_name="Temperature",
            line_color="#d32f2f", band_fill="rgba(255,100,100,0.25)",
        ),
        use_container_width=True,
    )


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
        fig.add_trace(go.Bar(x=before["datetime_display"],
                             y=before["temp_max"] - before["temp_min"],
                             base=before["temp_min"],
                             name="Unselected Period",
                             marker_color="rgba(180,180,180,0.3)",
                             marker_line_width=0,
                             showlegend=True, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["temp_avg"],
                                 mode="lines", line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 showlegend=False, hoverinfo="skip"))

    # ── Active range ──────────────────────────────────────────────────────────
    active = daily_stats[(daily_stats["doy"] >= start_doy) & (daily_stats["doy"] <= end_doy)]

    # ASHRAE 80% band – bars, rendered first so they sit behind the 90% and temp bars
    fig.add_trace(go.Bar(x=active["datetime_display"],
                         y=active["comfort_80_upper"] - active["comfort_80_lower"],
                         base=active["comfort_80_lower"],
                         name="ASHRAE adaptive comfort (80%)",
                         marker_color="rgba(128,128,128,0.18)",
                         marker_line_width=0,
                         customdata=active["comfort_80_upper"],
                         hovertemplate="<b>%{x}</b><br>80% comfort: %{base:.1f}–%{customdata:.1f}°C<extra></extra>"))

    # ASHRAE 90% band
    fig.add_trace(go.Bar(x=active["datetime_display"],
                         y=active["comfort_90_upper"] - active["comfort_90_lower"],
                         base=active["comfort_90_lower"],
                         name="ASHRAE adaptive comfort (90%)",
                         marker_color="rgba(128,128,128,0.38)",
                         marker_line_width=0,
                         customdata=active["comfort_90_upper"],
                         hovertemplate="<b>%{x}</b><br>90% comfort: %{base:.1f}–%{customdata:.1f}°C<extra></extra>"))

    # Temp min/max bars
    fig.add_trace(go.Bar(x=active["datetime_display"],
                         y=active["temp_max"] - active["temp_min"],
                         base=active["temp_min"],
                         name="Dry bulb temperature Range",
                         marker_color="rgba(255,100,100,0.55)",
                         marker_line_width=0,
                         customdata=active["temp_max"],
                         hovertemplate="<b>%{x}</b><br>Min: %{base:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>"))

    # Average line
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["temp_avg"],
                             mode="lines", name="Average Dry bulb temperature",
                             line=dict(color="#d32f2f", width=2),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>"))

    # ── Greyed-out: after selected range ──────────────────────────────────────
    if end_doy < 365:
        after = daily_stats[daily_stats["doy"] > end_doy]
        if not after.empty:
            fig.add_trace(go.Bar(x=after["datetime_display"],
                                 y=after["temp_max"] - after["temp_min"],
                                 base=after["temp_min"],
                                 marker_color="rgba(180,180,180,0.3)",
                                 marker_line_width=0,
                                 showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["temp_avg"],
                                     mode="lines", line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                     showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Annual Dry Bulb Temperature Trend",
        xaxis_title=None,
        yaxis_title="Temperature (°C)",
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_rangeslider_visible=False,
        barmode="overlay",
        height=450,
        template="plotly_white",
        margin=dict(b=80),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    if df.empty:
        st.info("No data available.")
        return

    # Min/Max are annual records — always use the full dataset regardless of
    # the selected date window, so the user always sees the true yearly peaks.
    min_row  = df.loc[df["dry_bulb_temperature"].idxmin()]
    max_row  = df.loc[df["dry_bulb_temperature"].idxmax()]
    temp_min = min_row["dry_bulb_temperature"]
    temp_max = max_row["dry_bulb_temperature"]
    min_ds   = min_row["datetime"].strftime("%b %d")
    max_ds   = max_row["datetime"].strftime("%b %d")
    min_hr   = int(min_row["hour"])
    max_hr   = int(max_row["hour"])

    # Avg and diurnal use only the selected date window
    date_filtered = df[
        (df["datetime"].dt.date >= start_date) &
        (df["datetime"].dt.date <= end_date)
    ]
    temp_avg = date_filtered["dry_bulb_temperature"].mean() if not date_filtered.empty else df["dry_bulb_temperature"].mean()

    # Diurnal range: mean of (daily max − daily min) over the selected period
    daily_source = date_filtered if not date_filtered.empty else df
    daily_extremes = daily_source.groupby(daily_source["datetime"].dt.date).agg(
        d_max=("dry_bulb_temperature", "max"),
        d_min=("dry_bulb_temperature", "min"),
    )
    diurnal = (daily_extremes["d_max"] - daily_extremes["d_min"]).mean()

    # Degree-days: sum hourly differences then divide by 24 → standard °C·day units
    hdd18          = (18 - df["dry_bulb_temperature"]).clip(lower=0).sum() / 24
    cdd24          = (df["dry_bulb_temperature"] - 24).clip(lower=0).sum() / 24
    mean_t         = df["dry_bulb_temperature"].mean()
    comfort_hrs    = len(df[(df["dry_bulb_temperature"] >= mean_t - 3.5) &
                            (df["dry_bulb_temperature"] <= mean_t + 3.5)])
    comfort_80_pct = comfort_hrs / len(df) * 100
    cooling_1pct   = df["dry_bulb_temperature"].quantile(0.99)
    overheat_hrs   = len(df[df["dry_bulb_temperature"] > 28])
    cold_hrs       = len(df[df["dry_bulb_temperature"] < 12])

    def _card(label, value, sub, color):
        return f"""
<div style="background:white;padding:16px;border-radius:8px;border-left:4px solid {color};
            box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">
  <div style="font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
  <div style="font-size:26px;font-weight:700;color:#2c3e50;margin:8px 0;">{value}</div>
  <div style="font-size:11px;color:#718096;">{sub}</div>
</div>"""

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: st.markdown(_card("Min Temp",       f"{temp_min:.2f} °C", f"{min_ds} · {min_hr:02d}:00", "#f59e0b"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Max Temp",       f"{temp_max:.2f} °C", f"{max_ds} · {max_hr:02d}:00", "#ef4444"), unsafe_allow_html=True)
    with c3: st.markdown(_card("Avg Temp",       f"{temp_avg:.2f} °C", "All year average",             "#8b5cf6"), unsafe_allow_html=True)
    with c4: st.markdown(_card("Diurnal Range",  f"{diurnal:.2f} °C",  "",                             "#3b82f6"), unsafe_allow_html=True)
    with c5: st.markdown(_card("1% Cooling",     f"{cooling_1pct:.2f} °C", "",                         "#06b6d4"), unsafe_allow_html=True)

    c6, c7, c8, c9, c10 = st.columns(5)
    with c6:  st.markdown(_card("HDD18",         f"{hdd18:.0f}",         "",             "#dc2626"), unsafe_allow_html=True)
    with c7:  st.markdown(_card("CDD24",         f"{cdd24:.0f}",         "",             "#0891b2"), unsafe_allow_html=True)
    with c8:  st.markdown(_card("Comfort 80%",   f"{comfort_80_pct:.0f} %", "",          "#06b6d4"), unsafe_allow_html=True)
    with c9:  st.markdown(_card("Overheat Hrs",  f"{overheat_hrs}",      "",             "#8b5cf6"), unsafe_allow_html=True)
    with c10: st.markdown(_card("Cold Hrs",      f"{cold_hrs}",          "",             "#3b82f6"), unsafe_allow_html=True)


def _render_monthly_trend(df, start_date, end_date):
    monthly = df.groupby("month").agg(
        temp_min=("dry_bulb_temperature", "min"),
        temp_max=("dry_bulb_temperature", "max"),
        temp_avg=("dry_bulb_temperature", "mean"),
        rh_min=("relative_humidity", "min"),
        rh_max=("relative_humidity", "max"),
        rh_avg=("relative_humidity", "mean"),
    ).reset_index()

    month_lbl = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    monthly["month_name"] = monthly["month"].apply(lambda x: month_lbl[x - 1])

    start_month = start_date.month
    end_month   = end_date.month

    fig = go.Figure()

    if start_month > 1:
        before = monthly[monthly["month"] < start_month]
        fig.add_trace(go.Scatter(x=before["month_name"], y=before["temp_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["month_name"], y=before["temp_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", name="Unselected Period",
                                 showlegend=True, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["month_name"], y=before["temp_avg"],
                                 mode="lines+markers",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 marker=dict(size=4), showlegend=False, hoverinfo="skip"))

    active = monthly[(monthly["month"] >= start_month) & (monthly["month"] <= end_month)]

    fig.add_trace(go.Scatter(x=active["month_name"], y=active["temp_max"],
                             fill=None, mode="lines", line_color="rgba(255,0,0,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["month_name"], y=active["temp_min"],
                             fill="tonexty", mode="lines", line_color="rgba(255,0,0,0)",
                             name="Monthly Temperature Range",
                             fillcolor="rgba(255,173,173,0.4)",
                             customdata=active["temp_max"],
                             hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["month_name"], y=active["temp_avg"],
                             mode="lines+markers", name="Monthly Average Temperature",
                             line=dict(color="#d32f2f", width=2), marker=dict(size=8),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>"))

    if end_month < 12:
        after = monthly[monthly["month"] > end_month]
        fig.add_trace(go.Scatter(x=after["month_name"], y=after["temp_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=after["month_name"], y=after["temp_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=after["month_name"], y=after["temp_avg"],
                                 mode="lines+markers",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 marker=dict(size=4), showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Monthly Temperature Trend",
        xaxis_title="Month", yaxis_title="Temperature (°C)",
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=450, template="plotly_white", margin=dict(b=80),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Monthly Temperature Summary")
    kpi = monthly[["month_name", "temp_min", "temp_max", "temp_avg"]].copy()
    kpi.columns = ["Month", "Min (°C)", "Max (°C)", "Avg (°C)"]
    st.dataframe(kpi, use_container_width=True, hide_index=True,
                 column_config={
                     "Min (°C)": st.column_config.NumberColumn(format="%.2f"),
                     "Max (°C)": st.column_config.NumberColumn(format="%.2f"),
                     "Avg (°C)": st.column_config.NumberColumn(format="%.2f"),
                 })


def _render_diurnal_profile(df, start_hour, end_hour):
    hourly = df.groupby(["month", "hour"]).agg(
        temp_min=("dry_bulb_temperature", "min"),
        temp_max=("dry_bulb_temperature", "max"),
        temp_avg=("dry_bulb_temperature", "mean"),
    ).reset_index()

    avg = hourly.groupby("hour").agg(
        temp_min=("temp_min", "min"),
        temp_max=("temp_max", "max"),
        temp_avg=("temp_avg", "mean"),
    ).reset_index()

    fig = go.Figure()

    if start_hour > 0:
        before = avg[avg["hour"] < start_hour]
        fig.add_trace(go.Scatter(x=before["hour"], y=before["temp_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["hour"], y=before["temp_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)",
                                 name="Unselected Hours", showlegend=True, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["hour"], y=before["temp_avg"],
                                 mode="lines+markers",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 marker=dict(size=4), showlegend=False, hoverinfo="skip"))

    active = avg[(avg["hour"] >= start_hour) & (avg["hour"] <= end_hour)]
    fig.add_trace(go.Scatter(x=active["hour"], y=active["temp_max"],
                             fill=None, mode="lines", line_color="rgba(255,0,0,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["hour"], y=active["temp_min"],
                             fill="tonexty", mode="lines", line_color="rgba(255,0,0,0)",
                             name="Daily Range", fillcolor="rgba(255,173,173,0.3)",
                             customdata=active["temp_max"],
                             hovertemplate="<b>Hour %{x}:00</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["hour"], y=active["temp_avg"],
                             mode="lines+markers", name="Average Temperature",
                             line=dict(color="#d32f2f", width=2), marker=dict(size=6),
                             hovertemplate="<b>Hour %{x}:00</b><br>Avg: %{y:.2f}°C<extra></extra>"))

    if end_hour < 23:
        after = avg[avg["hour"] > end_hour]
        fig.add_trace(go.Scatter(x=after["hour"], y=after["temp_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=after["hour"], y=after["temp_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=after["hour"], y=after["temp_avg"],
                                 mode="lines+markers",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 marker=dict(size=4), showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Diurnal Temperature Profile",
        xaxis_title="Hour of Day", yaxis_title="Temperature (°C)",
        hovermode="x unified", showlegend=True,
        template="plotly_white", height=450,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_comfort_analysis(df, daily_stats, start_date, end_date):
    start_month_num = start_date.month
    end_month_num   = end_date.month

    start_doy = pd.to_datetime(f"2024-{start_month_num:02d}-01").dayofyear
    end_doy   = (
        366
        if end_month_num == 12
        else pd.to_datetime(f"2024-{end_month_num + 1:02d}-01").dayofyear - 1
    )

    fig = go.Figure()

    if start_doy > 1:
        before = daily_stats[daily_stats["doy"] < start_doy]
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["temp_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["temp_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)",
                                 name="Unselected Period", showlegend=True, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["temp_avg"],
                                 mode="lines",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 showlegend=False, hoverinfo="skip"))

    active = daily_stats[(daily_stats["doy"] >= start_doy) & (daily_stats["doy"] <= end_doy)]

    # ASHRAE 90% comfort band
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["comfort_90_upper"],
                             fill=None, mode="lines", line_color="rgba(128,128,128,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["comfort_90_lower"],
                             fill="tonexty", mode="lines", line_color="rgba(128,128,128,0)",
                             name="ASHRAE 90% acceptability",
                             fillcolor="rgba(76,175,80,0.4)", hoverinfo="skip"))

    # Temperature range
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["temp_max"],
                             fill=None, mode="lines", line_color="rgba(255,0,0,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["temp_min"],
                             fill="tonexty", mode="lines", line_color="rgba(255,0,0,0)",
                             name="Daily Temperature Range",
                             fillcolor="rgba(255,173,173,0.3)",
                             customdata=active["temp_max"],
                             hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["temp_avg"],
                             mode="lines", name="Average Temperature",
                             line=dict(color="#d32f2f", width=2),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>"))

    if end_doy < 365:
        after = daily_stats[daily_stats["doy"] > end_doy]
        if not after.empty:
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["temp_max"],
                                     fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["temp_min"],
                                     fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                     fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["temp_avg"],
                                     mode="lines",
                                     line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                     showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Comfort Analysis – ASHRAE Adaptive Comfort",
        xaxis_title="Day", yaxis_title="Temperature (°C)",
        hovermode="x unified", showlegend=True,
        template="plotly_white", height=450,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_energy_metrics(df, start_date, end_date, start_hour, end_hour):
    filtered = df[
        (df["datetime"].dt.date >= start_date) &
        (df["datetime"].dt.date <= end_date) &
        (df["hour"].between(start_hour, end_hour))
    ]

    if filtered.empty:
        st.info("No data in the selected date/hour range.")
        return

    # Degree-days: sum hourly differences then divide by 24 → true °C·day units,
    # consistent with the Annual Trend KPI cards.
    hdd18          = (18 - df["dry_bulb_temperature"]).clip(lower=0).sum() / 24
    cdd24          = (df["dry_bulb_temperature"] - 24).clip(lower=0).sum() / 24
    hdd18_filtered = (18 - filtered["dry_bulb_temperature"]).clip(lower=0).sum() / 24
    cdd24_filtered = (filtered["dry_bulb_temperature"] - 24).clip(lower=0).sum() / 24

    monthly_hdd = df.groupby("month").apply(
        lambda x: (18 - x["dry_bulb_temperature"]).clip(lower=0).sum() / 24
    )
    monthly_cdd = df.groupby("month").apply(
        lambda x: (x["dry_bulb_temperature"] - 24).clip(lower=0).sum() / 24
    )
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    st.markdown("#### Energy Performance Indicators")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("HDD18 (Annual)", f"{hdd18:.0f}",          "Heating Degree-Days")
    with c2: st.metric("CDD24 (Annual)", f"{cdd24:.0f}",          "Cooling Degree-Days")
    with c3: st.metric("HDD18 (Period)", f"{hdd18_filtered:.0f}", "Heating Degree-Days")
    with c4: st.metric("CDD24 (Period)", f"{cdd24_filtered:.0f}", "Cooling Degree-Days")

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=month_names, y=monthly_hdd.values, name="HDD18", marker_color="#2196F3"),
                  secondary_y=False)
    fig.add_trace(go.Bar(x=month_names, y=monthly_cdd.values, name="CDD24", marker_color="#FF9800"),
                  secondary_y=False)
    fig.update_layout(
        title="Monthly Degree-Days Distribution",
        xaxis_title="Month", yaxis_title="Degree-Days",
        hovermode="x unified", height=400, barmode="stack",
    )
    st.plotly_chart(fig, use_container_width=True)
