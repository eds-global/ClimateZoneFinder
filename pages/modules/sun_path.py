"""Sun Path interactive chart and Sun Path section UI rendering."""

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytz
from .st_compat import st
import plotly.graph_objects as go

from .shading_helpers import (
    _MONTH_SHORT,
    _ORIENTATIONS,
    build_thermal_matrix,
    get_overheating_hours,
    compute_solar_angles,
    build_orientation_table,
    make_shading_mask_chart,
)


class SunPathInputError(ValueError):
    """Raised by the pure figure builders when input data/metadata is unusable."""


def build_sun_path_figure(
    data: pd.DataFrame,
    metadata: dict,
    chart_type: str = "Sun Path",
    temp_threshold: float = 28.0,
    rad_threshold: float = 315.0,
    on_message: Callable[[str, str], None] | None = None,
) -> tuple[go.Figure, dict]:
    """
    Build the interactive Sun Path Diagram figure (pure — no Streamlit calls).

    Args:
        data: hourly EPW DataFrame with a 'datetime' column (or DatetimeIndex).
        metadata: EPW header dict; requires 'latitude' and 'longitude',
            optional 'timezone' (IANA name or numeric UTC offset, default "UTC").
        chart_type: one of "Sun Path", "Dry Bulb Temperature",
            "Direct Normal Radiation", "Global Horizontal Radiation", "Shading".
        temp_threshold: dry-bulb temperature threshold (°C) for shading need.
        rad_threshold: GHI threshold (W/m²) for shading need.
        on_message: optional callback receiving (level, text) for the
            diagnostic "info"/"warning" messages the Streamlit page displays.

    Returns:
        (figure, metrics) — metrics is
        {"total_sunshine_hours", "required_shading_hours"} when
        chart_type == "Shading", else an empty dict.

    Raises:
        ImportError: pvlib is not installed.
        SunPathInputError: missing latitude/longitude, missing datetime
            column/index, or no daytime solar positions.
    """
    from pvlib import solarposition

    def _notify(level: str, text: str) -> None:
        if on_message is not None:
            on_message(level, text)

    lat = metadata.get("latitude")
    lon = metadata.get("longitude")
    tz_str = metadata.get("timezone", "UTC")

    if lat is None or lon is None:
        raise SunPathInputError("Location information (latitude/longitude) not found in EPW file.")

    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        try:
            tz_offset = float(tz_str)
            hours = int(tz_offset)
            minutes = int((tz_offset - hours) * 60)
            sign = "+" if tz_offset >= 0 else "-"
            tz_for_localize = f"UTC{sign}{abs(hours):02d}:{abs(minutes):02d}"
            tz = pytz.timezone(tz_for_localize)
        except Exception:
            tz = pytz.UTC

    times = pd.date_range(
        "2020-01-01 00:00:00",
        "2021-01-01 00:00:00",
        freq="h",
        tz=tz,
        inclusive="left",
    )

    solpos = solarposition.get_solarposition(times, lat, lon)
    solpos = solpos[solpos["apparent_elevation"] > 0]

    if solpos.empty:
        raise SunPathInputError("No daytime solar positions found. Check timezone or location.")

    solpos["r"] = 90 - solpos["apparent_elevation"]
    solpos["theta"] = solpos["azimuth"]

    # --- Robust EPW handling: column aliasing, timezone, resampling ---
    df = data.copy()

    # Ensure datetime exists
    if "datetime" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "datetime"})
        else:
            raise SunPathInputError("EPW data missing 'datetime' column or DatetimeIndex.")

    # common aliases mapping (helps when EPW has different field names)
    common_aliases = {
        'dni': 'direct_normal_irradiance',
        'direct normal': 'direct_normal_irradiance',
        'direct_normal': 'direct_normal_irradiance',
        'dhi': 'diffuse_horizontal_irradiance',
        'diffuse horizontal': 'diffuse_horizontal_irradiance',
        'ghi': 'global_horizontal_irradiance',
        'global horizontal': 'global_horizontal_irradiance',
        'dry bulb': 'dry_bulb_temperature',
        'dry_bulb': 'dry_bulb_temperature',
        'drybulb': 'dry_bulb_temperature',
        'temperature': 'dry_bulb_temperature',
        'temp': 'dry_bulb_temperature',
    }

    # Build rename map by matching aliases against actual column names
    cols_lower = {c: c.lower() for c in df.columns}
    rename_map = {}
    for alias, target in common_aliases.items():
        if target in df.columns:
            continue
        for orig, low in cols_lower.items():
            if alias == low or alias in low:
                if orig != 'datetime' and target not in df.columns:
                    rename_map[orig] = target
                    break

    if rename_map:
        df = df.rename(columns=rename_map)
        _notify("info", f"Detected and remapped EPW columns: {', '.join([f'{v}←{k}' for k,v in rename_map.items()])}")

    # columns we'll try to join
    join_cols = [c for c in ["dry_bulb_temperature", "direct_normal_irradiance", "diffuse_horizontal_irradiance"] if c in df.columns]

    epw_data = df.set_index("datetime")
    if epw_data.index.tz is None:
        epw_data.index = epw_data.index.tz_localize(tz)
    else:
        epw_data.index = epw_data.index.tz_convert(tz)
    epw_data.index = epw_data.index.map(lambda x: x.replace(year=2020))

    # Resample to hourly if timestamps are not on the hour (many EPW are half-hour)
    epw_hourly = epw_data
    try:
        has_half_hour = any(t.minute != 0 for t in epw_hourly.index)
    except Exception:
        has_half_hour = False

    if has_half_hour:
        # Resample only numeric join columns to avoid mean() failing on object dtype
        num_cols = [c for c in join_cols if c in epw_hourly.columns]
        if num_cols:
            epw_num = epw_hourly[num_cols].apply(pd.to_numeric, errors='coerce')
            epw_num = epw_num.resample('h').mean()
            epw_hourly = epw_num.dropna(how='all')
        else:
            # nothing numeric to resample; fall back to hourly index dedupe
            epw_hourly = epw_hourly.resample('h').first().dropna(how='all')

    # Join solar positions to hourly EPW values
    solpos_merged = solpos.copy()
    if join_cols:
        solpos_merged = solpos_merged.join(epw_hourly[join_cols], how="left")
    else:
        solpos_merged = solpos_merged.copy()

    # compute GHI robustly
    altitude_rad = np.radians(solpos_merged["apparent_elevation"])
    dni = solpos_merged.get("direct_normal_irradiance", pd.Series(0, index=solpos_merged.index)).fillna(0)
    dhi = solpos_merged.get("diffuse_horizontal_irradiance", pd.Series(0, index=solpos_merged.index)).fillna(0)
    solpos_merged["global_horizontal_irradiance"] = (
        np.maximum(0, np.sin(altitude_rad)) * dni + dhi
    )

    # Diagnostics: warn if expected series are missing or zero
    if chart_type == "Direct Normal Radiation" and (solpos_merged.get("direct_normal_irradiance") is None or solpos_merged["direct_normal_irradiance"].dropna().sum() == 0):
        _notify("warning", "Direct Normal Irradiance appears missing or zero for this EPW.")
    if chart_type == "Global Horizontal Radiation" and solpos_merged["global_horizontal_irradiance"].dropna().sum() == 0:
        _notify("warning", "Global Horizontal Irradiance appears zero for daytime—check DHI/DNI fields.")
    if chart_type == "Dry Bulb Temperature" and (solpos_merged.get("dry_bulb_temperature") is None or solpos_merged["dry_bulb_temperature"].dropna().sum() == 0):
        _notify("warning", "Dry bulb temperature appears missing for this EPW.")

    fig = go.Figure()

    if chart_type == "Direct Normal Radiation":
        color_data = solpos_merged.get(
            "direct_normal_irradiance", pd.Series(0, index=solpos_merged.index)
        ).fillna(0)
        colorscale = "YlOrRd"
        colorbar_title = "DNR (Wh/m²)"
        colorbar_min, colorbar_max = 0, 1000
        tickvals = [0, 200, 400, 600, 800, 1000]
        ticktext = ["0", "200", "400", "600", "800", "1000"]
        hover_template = (
            "<b>%{customdata[0]} %{customdata[1]}</b><br>"
            "Time: %{customdata[2]}<br>"
            "Altitude: %{customdata[3]:.1f}°<br>"
            "Azimuth: %{customdata[4]:.1f}°<br>"
            "DNR: %{customdata[5]:.0f} Wh/m²"
            "<extra></extra>"
        )
    elif chart_type == "Global Horizontal Radiation":
        color_data = solpos_merged.get(
            "global_horizontal_irradiance", pd.Series(0, index=solpos_merged.index)
        ).fillna(0)
        colorscale = "YlOrRd"
        colorbar_title = "GHR (Wh/m²)"
        colorbar_min, colorbar_max = 0, 1200
        tickvals = [0, 200, 400, 600, 800, 1000, 1200]
        ticktext = ["0", "200", "400", "600", "800", "1000", "1200"]
        hover_template = (
            "<b>%{customdata[0]} %{customdata[1]}</b><br>"
            "Time: %{customdata[2]}<br>"
            "Altitude: %{customdata[3]:.1f}°<br>"
            "Azimuth: %{customdata[4]:.1f}°<br>"
            "GHR: %{customdata[5]:.0f} Wh/m²"
            "<extra></extra>"
        )
    elif chart_type == "Dry Bulb Temperature":
        color_data = solpos_merged.get(
            "dry_bulb_temperature", pd.Series(20, index=solpos_merged.index)
        ).fillna(20)
        colorscale = "YlOrRd"
        colorbar_title = "Temperature (°C)"
        colorbar_min, colorbar_max = 5, 40
        tickvals = [5, 10, 15, 20, 25, 30, 35, 40]
        ticktext = ["5", "10", "15", "20", "25", "30", "35", "40"]
        hover_template = (
            "<b>%{customdata[0]} %{customdata[1]}</b><br>"
            "Time: %{customdata[2]}<br>"
            "Altitude: %{customdata[3]:.1f}°<br>"
            "Azimuth: %{customdata[4]:.1f}°<br>"
            "Temperature: %{customdata[5]:.1f}°C"
            "<extra></extra>"
        )
    elif chart_type == "Shading":
        ghi  = solpos_merged.get(
            "global_horizontal_irradiance", pd.Series(0, index=solpos_merged.index)
        ).fillna(0)
        temp = solpos_merged.get(
            "dry_bulb_temperature", pd.Series(20, index=solpos_merged.index)
        ).fillna(20)
        _tthr = float(temp_threshold)
        _rthr = float(rad_threshold)
        color_data = ((temp > _tthr) & (ghi > _rthr)).astype(int)
        colorscale = [[0, "#FFF9C4"], [1, "#E65100"]]
        colorbar_title = "Shading Need"
        colorbar_min, colorbar_max = 0, 1
        tickvals = [0, 1]
        ticktext = ["No Shading", "Shading Required"]
        hover_template = (
            "<b>%{customdata[0]} %{customdata[1]}</b><br>"
            "Time: %{customdata[2]}<br>"
            "Altitude: %{customdata[3]:.1f}°<br>"
            "Azimuth: %{customdata[4]:.1f}°<br>"
            "Temp: %{customdata[5]:.1f}°C<br>"
            "GHI: %{customdata[6]:.0f} Wh/m²<br>"
            "Shading: %{customdata[7]}"
            "<extra></extra>"
        )
    else:  # "Sun Path" – colour by day of year
        color_data = pd.Series(
            solpos_merged.index.dayofyear, index=solpos_merged.index
        )
        colorscale = "YlOrRd"
        colorbar_title = "Day of Year"
        colorbar_min, colorbar_max = 1, 365
        tickvals = [1, 91, 182, 273, 365]
        ticktext = ["1", "91", "182", "273", "365"]
        hover_template = (
            "<b>%{customdata[0]} %{customdata[1]}</b><br>"
            "Time: %{customdata[2]}<br>"
            "Altitude: %{customdata[3]:.1f}°<br>"
            "Azimuth: %{customdata[4]:.1f}°"
            "<extra></extra>"
        )

    # ── Analemma traces (one per hour of day) ─────────────────────────────
    first_trace = True
    for hour in range(24):
        subset_idx = solpos_merged.index.hour == hour
        subset = solpos_merged[subset_idx]
        subset_colors = color_data[subset_idx]

        if len(subset) == 0:
            continue

        month_names    = subset.index.strftime("%B")
        hour_formatted = subset.index.strftime("%I:00 %p")
        temp_values = subset.get("dry_bulb_temperature",
                                 pd.Series(np.nan, index=subset.index))
        dnr_values  = subset.get("direct_normal_irradiance",
                                 pd.Series(np.nan, index=subset.index))
        ghr_values  = subset.get("global_horizontal_irradiance",
                                 pd.Series(np.nan, index=subset.index))

        if chart_type == "Direct Normal Radiation":
            customdata = np.stack(
                (month_names, subset.index.day, hour_formatted,
                 subset["apparent_elevation"], subset["azimuth"],
                 dnr_values.fillna(0)),
                axis=-1,
            )
        elif chart_type == "Global Horizontal Radiation":
            customdata = np.stack(
                (month_names, subset.index.day, hour_formatted,
                 subset["apparent_elevation"], subset["azimuth"],
                 ghr_values.fillna(0)),
                axis=-1,
            )
        elif chart_type == "Dry Bulb Temperature":
            customdata = np.stack(
                (month_names, subset.index.day, hour_formatted,
                 subset["apparent_elevation"], subset["azimuth"],
                 temp_values.fillna(20)),
                axis=-1,
            )
        elif chart_type == "Shading":
            shading_temp = subset.get(
                "dry_bulb_temperature", pd.Series(np.nan, index=subset.index)
            ).fillna(20)
            shading_ghi = subset.get(
                "global_horizontal_irradiance", pd.Series(np.nan, index=subset.index)
            ).fillna(0)
            shading_labels = np.where(
                (shading_temp > float(temp_threshold))
                & (shading_ghi > float(rad_threshold)),
                "Required", "Not Required",
            )
            customdata = np.stack(
                (month_names, subset.index.day, hour_formatted,
                 subset["apparent_elevation"], subset["azimuth"],
                 shading_temp, shading_ghi, shading_labels),
                axis=-1,
            )
        else:  # Sun Path
            customdata = np.stack(
                (month_names, subset.index.day, hour_formatted,
                 subset["apparent_elevation"], subset["azimuth"]),
                axis=-1,
            )

        fig.add_trace(
            go.Scatterpolar(
                r=subset["r"],
                theta=subset["theta"],
                mode="lines+markers",
                marker=dict(
                    size=4,
                    color=subset_colors.values,
                    colorscale=colorscale,
                    cmin=colorbar_min,
                    cmax=colorbar_max,
                    showscale=(first_trace and chart_type != "Shading"),
                    colorbar=dict(
                        title=dict(
                            text=colorbar_title,
                            side="right",
                            font=dict(size=12, color="#333"),
                        ),
                        thickness=20,
                        len=0.7,
                        x=1.1,
                        tickvals=tickvals,
                        ticktext=ticktext,
                        tickmode="array",
                    ) if chart_type != "Shading" else None,
                    opacity=0.7,
                    line=dict(width=0.5),
                ),
                line=dict(width=1, color="rgba(100, 100, 100, 0.3)"),
                showlegend=False,
                customdata=customdata,
                hovertemplate=hover_template,
            )
        )
        first_trace = False

    # ── Shading metrics & legend traces ───────────────────────────────────
    shading_metrics = {}
    if chart_type == "Shading":
        ghi_col  = solpos_merged.get(
            "global_horizontal_irradiance", pd.Series(0, index=solpos_merged.index)
        ).fillna(0)
        temp_col = solpos_merged.get(
            "dry_bulb_temperature", pd.Series(20, index=solpos_merged.index)
        ).fillna(20)
        # Hours per timestep from the actual data frequency (1.0 for
        # hourly EPWs, 0.5 for half-hourly) — was a hardcoded /2 before.
        _step_h = (
            solpos_merged.index.to_series().diff().median().total_seconds() / 3600.0
            if len(solpos_merged) > 1 else 1.0
        )
        if not np.isfinite(_step_h) or _step_h <= 0:
            _step_h = 1.0
        _tthr = float(temp_threshold)
        _rthr = float(rad_threshold)
        sunshine_mask = ghi_col > 300
        sunshine_hours = sunshine_mask.sum() * _step_h
        shading_mask   = (temp_col > _tthr) & (ghi_col > _rthr)
        shading_hours  = shading_mask.sum() * _step_h
        shading_metrics = {
            "total_sunshine_hours":   sunshine_hours,
            "required_shading_hours": shading_hours,
        }

        for label, colour in [("No Shading", "#FFF9C4"), ("Shading Required", "#E65100")]:
            fig.add_trace(
                go.Scatterpolar(
                    r=[None], theta=[None], mode="markers",
                    marker=dict(size=12, color=colour, symbol="square"),
                    name=label, showlegend=True, hoverinfo="skip",
                )
            )

    # ── Key solar-date curves ──────────────────────────────────────────────
    key_dates = {
        "Mar 21 (Spring)": ("2020-03-21", "#FF9500"),
        "Jun 21 (Summer)": ("2020-06-21", "#FF0000"),
        "Dec 21 (Winter)": ("2020-12-21", "#0066CC"),
    }

    for label, (date_str, color) in key_dates.items():
        date = pd.Timestamp(date_str)
        day_times = pd.date_range(
            date, date + pd.Timedelta("1D"), freq="5min", tz=tz, inclusive="left"
        )
        sol = solarposition.get_solarposition(day_times, lat, lon)
        sol = sol[sol["apparent_elevation"] > 0]
        if sol.empty:
            continue

        sol_merged = sol.copy()
        if 'epw_hourly' in locals() and len(epw_hourly) > 0:
            try:
                nearest = epw_hourly[[c for c in ["dry_bulb_temperature", "direct_normal_irradiance", "diffuse_horizontal_irradiance"] if c in epw_hourly.columns]].reindex(sol.index, method='nearest', tolerance=pd.Timedelta('30min'))
            except Exception:
                nearest = epw_hourly[[c for c in ["dry_bulb_temperature", "direct_normal_irradiance", "diffuse_horizontal_irradiance"] if c in epw_hourly.columns]].reindex(sol.index)
            sol_merged = sol_merged.join(nearest, how='left')
        else:
            sol_merged = sol_merged.join(
                epw_data[["dry_bulb_temperature", "direct_normal_irradiance", "diffuse_horizontal_irradiance"]],
                how="left",
            )
        alt_r = np.radians(sol_merged["apparent_elevation"])
        sol_merged["global_horizontal_irradiance"] = (
            np.maximum(0, np.sin(alt_r)) * sol_merged["direct_normal_irradiance"].fillna(0)
            + sol_merged["diffuse_horizontal_irradiance"].fillna(0)
        )

        r     = 90 - sol["apparent_elevation"]
        theta = sol["azimuth"]

        fig.add_trace(
            go.Scatterpolar(
                r=r, theta=theta, mode="lines",
                line=dict(width=0.8, color=color),
                name=label, showlegend=True,
            )
        )

        if label == "Jun 21 (Summer)":
            label_times = [
                pd.Timestamp(date_str).tz_localize(tz) + pd.Timedelta(hours=h)
                for h in [6, 9, 12, 15, 18]
            ]
            lbl_sol = solarposition.get_solarposition(label_times, lat, lon)
            lbl_sol = lbl_sol[lbl_sol["apparent_elevation"] > 0]
            if not lbl_sol.empty:
                fig.add_trace(
                    go.Scatterpolar(
                        r=90 - lbl_sol["apparent_elevation"],
                        theta=lbl_sol["azimuth"],
                        mode="text",
                        text=[str(h) for h in [6, 9, 12, 15, 18]],
                        textfont=dict(size=11, color="black"),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

    # ── Layout ────────────────────────────────────────────────────────────
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(240, 240, 240, 0.3)",
            radialaxis=dict(
                visible=True, range=[0, 90],
                showticklabels=False, showline=False, ticks="",
                gridcolor="rgba(128, 128, 128, 0.2)",
            ),
            angularaxis=dict(
                tickfont=dict(size=11),
                rotation=90, direction="clockwise",
                gridcolor="rgba(128, 128, 128, 0.3)",
                tickvals=[0, 45, 90, 135, 180, 225, 270, 315],
                ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
            ),
        ),
        showlegend=True,
        legend=dict(
            x=0.02, y=0.98,
            bgcolor="rgba(255, 255, 255, 0.9)",
            bordercolor="black", borderwidth=1,
            font=dict(size=10),
        ),
        hovermode="closest",
        height=700,
        margin=dict(l=80, r=140, t=100, b=80),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Arial, sans-serif", size=12, color="black"),
    )

    fig.add_trace(
        go.Scatterpolar(
            r=[0, 15, 30, 45, 60, 75, 90],
            theta=[45] * 7,
            mode="lines+text",
            line=dict(color="black", width=1),
            text=["90° (Zenith)", "75°", "60°", "45°", "30°", "15°", "0° (Horizon)"],
            textposition="middle right",
            textfont=dict(size=10),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    return fig, (shading_metrics if chart_type == "Shading" else {})


def plot_sun_path(data: pd.DataFrame, metadata: dict, chart_type: str = "Sun Path") -> dict:
    """
    Generate and display an interactive Sun Path Diagram using Plotly.

    Thin Streamlit wrapper around :func:`build_sun_path_figure` — reads the
    shading thresholds from ``st.session_state``, displays the figure, and
    surfaces any errors/diagnostics via Streamlit.

    Returns:
        dict with shading metrics when chart_type == "Shading", else empty dict.
    """
    temp_threshold = float(st.session_state.get("temp_threshold", 28.0))
    rad_threshold = float(st.session_state.get("rad_threshold", 315.0))

    def _show(level: str, text: str) -> None:
        (st.info if level == "info" else st.warning)(text)

    try:
        fig, metrics = build_sun_path_figure(
            data,
            metadata,
            chart_type,
            temp_threshold=temp_threshold,
            rad_threshold=rad_threshold,
            on_message=_show,
        )
        st.plotly_chart(fig, use_container_width=True)
        return metrics
    except ImportError:
        st.error("Required packages not found. Please ensure pvlib and plotly are installed.")
        return {}
    except SunPathInputError as e:
        st.error(str(e))
        return {}
    except Exception as e:
        st.error(f"Error generating Sun Path Diagram: {str(e)}")
        return {}


def build_thermal_matrix_figure(
    data: pd.DataFrame,
    temp_threshold: float = 28.0,
    rad_threshold: float = 315.0,
) -> go.Figure:
    """
    Build the 24h x 12mo Thermal & Radiation Matrix heatmap (pure — no
    Streamlit calls). Cells exceeding both thresholds get a black border and
    are counted in the side annotation.
    """
    temp_matrix, rad_matrix, overheat_mask = build_thermal_matrix(data, temp_threshold, rad_threshold)
    hours_labels = [f"{h:02d}:00" for h in range(24)]

    hover_text = []
    for h_idx in range(24):
        row_txt = []
        for m_idx in range(12):
            t_v = temp_matrix.iloc[h_idx, m_idx]
            r_v = rad_matrix.iloc[h_idx, m_idx]
            warn = " ⚠️ OVERHEATING" if overheat_mask.iloc[h_idx, m_idx] else ""
            row_txt.append(
                f"<b>{hours_labels[h_idx]}, {_MONTH_SHORT[m_idx]}</b><br>"
                f"Avg Temp: {t_v:.1f}°C<br>"
                f"Avg GHI: {r_v:.0f} W/m²{warn}"
            )
        hover_text.append(row_txt)

    fig_matrix = go.Figure(
        go.Heatmap(
            z=temp_matrix.values,
            x=_MONTH_SHORT,
            y=hours_labels,
            colorscale="RdYlBu_r",
            colorbar=dict(title=dict(text="°C", side="right"), thickness=14, len=0.8),
            text=hover_text,
            hovertemplate="%{text}<extra></extra>",
            showscale=True,
        )
    )

    shapes = []
    for h_idx in range(24):
        for m_idx in range(12):
            if overheat_mask.iloc[h_idx, m_idx]:
                shapes.append(dict(
                    type="rect", xref="x", yref="y",
                    x0=m_idx - 0.5, x1=m_idx + 0.5,
                    y0=h_idx - 0.5, y1=h_idx + 0.5,
                    line=dict(color="#080808", width=2.5),
                    fillcolor="rgba(0,0,0,0)",
                ))

    n_overheat = int(overheat_mask.values.sum())
    fig_matrix.update_layout(
        xaxis_title="Month",
        yaxis_title="Hour",
        height=540,
        shapes=shapes,
        template="plotly_white",
        margin=dict(l=70, r=90, t=20, b=50),
        annotations=[dict(
            x=1.13, y=0.5, xref="paper", yref="paper",
            text=f"<b>{n_overheat}</b><br>cells<br>overheat",
            showarrow=False,
            font=dict(size=11, color="#E65100"),
            align="center",
        )],
    )

    return fig_matrix


def compute_orientation_shading_stats(
    data: pd.DataFrame,
    lat: float,
    lon: float,
    tz_str: str = "UTC",
    temp_threshold: float = 28.0,
    rad_threshold: float = 315.0,
    cutoff_angle: float = 45.0,
) -> dict:
    """
    Compute the data behind the Orientation Shading Analysis (pure — no
    Streamlit calls).

    Returns a dict with keys:
        overheating_rows  (int) – EPW rows exceeding both thresholds
        sun_positions     (int) – above-horizon solar positions among those rows
        solar_positions   (pd.DataFrame) – from compute_solar_angles (empty if none)
        orientation_table (pd.DataFrame) – from build_orientation_table (empty if none)
    """
    overheat_df = get_overheating_hours(data, temp_threshold, rad_threshold)
    if overheat_df.empty:
        return {
            "overheating_rows": 0,
            "sun_positions": 0,
            "solar_positions": pd.DataFrame(),
            "orientation_table": pd.DataFrame(),
        }

    solar_pos = compute_solar_angles(overheat_df, lat, lon, tz_str)
    if solar_pos.empty:
        return {
            "overheating_rows": len(overheat_df),
            "sun_positions": 0,
            "solar_positions": solar_pos,
            "orientation_table": pd.DataFrame(),
        }

    return {
        "overheating_rows": len(overheat_df),
        "sun_positions": len(solar_pos),
        "solar_positions": solar_pos,
        "orientation_table": build_orientation_table(solar_pos, cutoff_angle),
    }


# ─────────────────────────────────────────────────────────────────────────────


def render_sun_path_section(df: pd.DataFrame, metadata: dict) -> None:
    """
    Render the complete Sun Path section inside col_right.

    Handles chart type selector, the interactive sun path chart, shading metrics,
    and the extended shading analysis (thermal matrix, orientation table, shading masks).
    """
    st.markdown(
        "<h3 style='text-align: left; margin-top: 20px;'>Sun Path Analysis</h3>",
        unsafe_allow_html=True,
    )

    # using tabs for better UX and to free up space for the extended shading analysis
    tabs = st.tabs(
        ["Sun Path", "Dry Bulb Temp", "Direct Normal Rad", "Global Hor Rad", "Shading"]
    )
    tab1, tab2, tab3, tab4, tab5 = tabs
    chart_types = ["Sun Path", "Dry Bulb Temperature", "Direct Normal Radiation", "Global Horizontal Radiation", "Shading"]

    with tab1:
        st.session_state["sun_chart_type"] = "Sun Path"
        metrics = plot_sun_path(df, metadata, "Sun Path")

    with tab2:
        st.session_state["sun_chart_type"] = "Dry Bulb Temperature"
        metrics = plot_sun_path(df, metadata, "Dry Bulb Temperature")

    with tab3:
        st.session_state["sun_chart_type"] = "Direct Normal Radiation"
        metrics = plot_sun_path(df, metadata, "Direct Normal Radiation")

    with tab4:
        st.session_state["sun_chart_type"] = "Global Horizontal Radiation"
        metrics = plot_sun_path(df, metadata, "Global Horizontal Radiation")

    with tab5:
        st.session_state["sun_chart_type"] = "Shading"
        metrics = plot_sun_path(df, metadata, "Shading")

        # ── Shading metrics KPI cards ──────────────────────────────────────────────
        if metrics:
            st.markdown("---")
            st.markdown(
                "<h4 style='text-align: left;'>📊 Shading Metrics</h4>",
                unsafe_allow_html=True,
            )
            metric_col1, metric_col2 = st.columns(2)
            with metric_col1:
                st.metric(
                    label="Total Sunshine Hours",
                    value=f"{metrics.get('total_sunshine_hours', 0):.1f}",
                    help="Hours with Global Horizontal Irradiance > 300 Wh/m²",
                )
            with metric_col2:
                st.metric(
                    label="Required Shading Hours",
                    value=f"{metrics.get('required_shading_hours', 0):.1f}",
                    help="Hours where Temperature > 28°C AND GHI > 315 Wh/m²",
                )

        # ── Extended Shading Analysis ───────────────────────────────────────────

        _temp_thr = float(st.session_state.get("temp_threshold", 28.0))
        _rad_thr  = float(st.session_state.get("rad_threshold", 315.0))
        _lat   = float(st.session_state.get("shading_lat",  metadata.get("latitude")  or 0.0))
        _lon   = float(st.session_state.get("shading_lon",  metadata.get("longitude") or 0.0))
        _cutoff   = float(st.session_state.get("design_cutoff_angle", 45.0))
        _tz_str   = metadata.get("timezone", "UTC")

        # ── 1. THERMAL & RADIATION MATRIX ─────────────────────────────────────────
        st.markdown("---")
        st.markdown(
            "<h4 style='margin-bottom:4px;'>Thermal &amp; Radiation Matrix"
            f"<span style='font-size:13px; font-weight:400; color:#666; margin-left:12px;'>"
            f"Black border = Temp &gt; {_temp_thr}°C AND GHI &gt; {_rad_thr} W/m²</span></h4>",
            unsafe_allow_html=True,
        )

        try:
            fig_matrix = build_thermal_matrix_figure(df, _temp_thr, _rad_thr)
            st.plotly_chart(fig_matrix, use_container_width=True)

        except Exception as _e:
            st.error(f"Could not build Thermal Matrix: {_e}")

        # ── 2. ORIENTATION SHADING ANALYSIS ───────────────────────────────────────
        st.markdown("---")
        st.markdown(
            "<h4 style='margin-bottom:4px;'>🏗️ Orientation Shading Analysis"
            f"<span style='font-size:13px; font-weight:400; color:#666; margin-left:12px;'>"
            f"Design cutoff angle: {_cutoff}°</span></h4>",
            unsafe_allow_html=True,
        )

        try:
            with st.spinner("Computing solar positions for overheating hours…"):
                _shading_stats = compute_orientation_shading_stats(
                    df, _lat, _lon, _tz_str, _temp_thr, _rad_thr, _cutoff
                )

            if _shading_stats["overheating_rows"] == 0:
                st.info("No overheating hours found with current thresholds.")
            elif _shading_stats["sun_positions"] == 0:
                st.info("No daytime overheating sun positions found.")
            else:
                solar_pos = _shading_stats["solar_positions"]
                n_total = _shading_stats["sun_positions"]
                st.caption(
                    f"**{n_total}** overheating daytime sun positions "
                    f"({_shading_stats['overheating_rows']} EPW rows → {n_total} above horizon)"
                )

                orient_df = _shading_stats["orientation_table"]

                def _protection_badge(pct):
                    if pct is None:
                        return "—"
                    color = (
                        "#4caf50" if pct >= 95
                        else ("#ff9800" if pct >= 80 else "#f44336")
                    )
                    return (
                        f'<span style="background:{color}; color:white; padding:2px 8px; '
                        f'border-radius:10px; font-weight:600; font-size:12px;">'
                        f'{pct:.1f}%</span>'
                    )

                html_rows = ""
                for _, _row in orient_df.iterrows():
                    badge = _protection_badge(_row.get("Protection (%)"))
                    _dh  = f"{_row['D/H (Overhang)']:.3f}" if _row["D/H (Overhang)"] is not None else "—"
                    _dw  = f"{_row['D/W (Fin)']:.3f}"       if _row["D/W (Fin)"] is not None else "—"
                    _min_vsa = f"{_row['Min VSA (°)']:.1f}°"    if _row["Min VSA (°)"] is not None else "—"
                    _max_hsa = f"{_row['Max |HSA| (°)']:.1f}°"  if _row["Max |HSA| (°)"] is not None else "—"
                    html_rows += (
                        "<tr>"
                        f"<td style='padding:6px 12px; font-weight:600;'>{_row['Orientation']}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{_row['Rays Hitting']}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{_min_vsa}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{_max_hsa}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{_dh}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{_dw}</td>"
                        f"<td style='padding:6px 12px; text-align:center;'>{badge}</td>"
                        "</tr>"
                    )

                st.markdown(
                    f"""
<style>
.shading-table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.shading-table th {{
    background: #1a3a52; color: white; padding: 8px 12px;
    text-align: center; font-weight: 600; letter-spacing: 0.3px;
}}
.shading-table th:first-child {{ text-align: left; }}
.shading-table tr:nth-child(even) {{ background: #f5f7fa; }}
.shading-table tr:hover {{ background: #e8f4f8; }}
.shading-table td {{ border-bottom: 1px solid #e0e0e0; }}
</style>
<table class="shading-table">
<thead>
  <tr>
    <th>Orientation</th><th>Rays Hitting</th><th>Min VSA</th><th>Max |HSA|</th>
    <th>D/H (Overhang)</th><th>D/W (Fin)</th><th>Protection %</th>
  </tr>
</thead>
<tbody>
  {html_rows}
</tbody>
</table>
<p style='font-size:11px; color:#888; margin-top:6px;'>
  <b>D/H</b> = horizontal overhang depth-to-height ratio &nbsp;|&nbsp;
  <b>D/W</b> = vertical fin depth-to-width ratio &nbsp;|&nbsp;
  Protection % = overheating rays blocked at design cutoff angle &nbsp;
  <span style='color:#4caf50; font-weight:600;'>■</span> ≥95% &nbsp;
  <span style='color:#ff9800; font-weight:600;'>■</span> 80–95% &nbsp;
  <span style='color:#f44336; font-weight:600;'>■</span> &lt;80%
</p>
""",
                    unsafe_allow_html=True,
                )

                # ── 3. SHADING MASK MINI DIAGRAMS ─────────────────────────────
                st.markdown(
                    "<h5 style='margin-top:22px; margin-bottom:4px;'>"
                    "🔵 Shading Mask Diagrams</h5>"
                    "<p style='font-size:12px; color:#666; margin-bottom:10px;'>"
                    "<span style='color:#E65100; font-weight:600;'>●</span> Overheating (hits façade) &nbsp;|&nbsp; "
                    "<span style='color:lightgrey; font-weight:600;'>●</span> Overheating (other side) &nbsp;|&nbsp; "
                    "<span style='color:#1565C0; font-weight:600;'>- -</span> VSA cutoff arc &nbsp;|&nbsp; "
                    "<span style='color:#388E3C; font-weight:600;'>—</span> Façade direction</p>",
                    unsafe_allow_html=True,
                )

                orient_pairs = list(_ORIENTATIONS.items())
                for _row_start in range(0, 8, 4):
                    dial_cols = st.columns(4)
                    for _col_idx, (_oname, _faz) in enumerate(
                        orient_pairs[_row_start: _row_start + 4]
                    ):
                        with dial_cols[_col_idx]:
                            st.markdown(
                                f"<p style='font-size:11px; font-weight:600; "
                                f"text-align:center; margin-bottom:2px;'>{_oname}</p>",
                                unsafe_allow_html=True,
                            )
                            _mini_fig = make_shading_mask_chart(solar_pos, _faz, _cutoff)
                            _safe_key = (
                                _oname.replace(" ", "_")
                                      .replace("(", "")
                                      .replace(")", "")
                                      .replace("°", "deg")
                            )
                            st.plotly_chart(
                                _mini_fig,
                                use_container_width=True,
                                key=f"mini_{_safe_key}",
                            )

        except Exception as _e:
            st.error(f"Could not build Orientation Shading Analysis: {_e}")
