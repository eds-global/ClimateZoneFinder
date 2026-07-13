"""Climate Analytics Dashboard – thin orchestrator.

All heavy lifting is delegated to pages/modules/:
  epw_parser      → parse_epw
  ppt_report      → generate_pptx_report, generate_shading_pptx_report
  sun_path        → render_sun_path_section
  dbt_module      → calculate_ashrae_comfort, render (Temperature tabs)
  humidity_module → render (Humidity tabs)

PERFORMANCE FIXES (v2):
  1. @st.cache_data on parse_epw, daily_stats, and ASHRAE comfort – eliminates
     re-computation on every widget interaction.
  2. Removed manual st.rerun() after tab clicks – that was causing a double
     full-page reload on every tab switch.
  3. Replaced manual button-based tab system with st.tabs() – tab switching is
     now client-side only (zero server round-trips).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import base64
import pandas as pd
import streamlit as st
import io
import urllib.request

from modules.epw_parser import parse_epw
from modules.ppt_report import generate_pptx_report, generate_shading_pptx_report
from modules.combined_report import generate_combined_pptx_report
from modules.sun_path import render_sun_path_section
from modules.sun_path_3d import render_sun_path_3d
from modules.dbt_module import calculate_ashrae_comfort
from modules import dbt_module, humidity_module, wind_module, ventilation_module, thermal_comfort_module, rainfall_module, solar_pv_module, utci_module
from modules import psychro_module, site_analysis_module, shading_designer_module
from modules import analytics

# ─── Page configuration ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="Climate Analytics Dashboard",
    layout="wide",
)

analytics.require_login()
analytics.log_page_view("Analysis")


# ─── Cached helpers ───────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Parsing EPW file…")
def cached_parse_epw(raw: str):
    """Parse the EPW text once and cache the result.
    Re-runs only when the file content changes."""
    return parse_epw(raw)


@st.cache_data(show_spinner=False)
def cached_daily_stats(raw: str):
    """Compute daily min/max/avg stats once per file."""
    df, _ = cached_parse_epw(raw)
    df = df.copy()
    df["doy"]   = df["datetime"].dt.dayofyear
    df["month"] = df["datetime"].dt.month

    daily = df.groupby("doy").agg(
        temp_min=("dry_bulb_temperature", "min"),
        temp_max=("dry_bulb_temperature", "max"),
        temp_avg=("dry_bulb_temperature", "mean"),
        rh_min  =("relative_humidity",    "min"),
        rh_max  =("relative_humidity",    "max"),
        rh_avg  =("relative_humidity",    "mean"),
    ).reset_index()

    year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
    daily["datetime"] = pd.to_datetime(
        daily["doy"].astype(str) + f"-{year}", format="%j-%Y", errors="coerce"
    )
    daily["datetime_display"] = daily["datetime"].dt.strftime("%b %d")
    return daily


@st.cache_data(show_spinner=False)
def cached_ashrae_comfort(raw: str):
    """Compute ASHRAE adaptive comfort bands once per file."""
    df, _ = cached_parse_epw(raw)
    df = df.copy()
    df["doy"] = df["datetime"].dt.dayofyear
    return calculate_ashrae_comfort(df)


@st.cache_data(show_spinner=False)
def cached_df_with_derived(raw: str):
    """Return the full dataframe with all derived columns, cached."""
    df, metadata = cached_parse_epw(raw)
    df = df.copy()
    df["doy"]        = df["datetime"].dt.dayofyear
    df["day"]        = df["datetime"].dt.day
    df["month"]      = df["datetime"].dt.month
    df["month_name"] = df["datetime"].dt.strftime("%b")
    return df, metadata


@st.cache_data(show_spinner="Calculating MRT & UTCI…")
def cached_utci_df(raw: str, lat: float, lon: float, tz_str: str,
                    posture: str, sky_view_factor: float,
                    shade_fraction: float, ground_reflectance: float):
    """Compute MRT + UTCI for every EPW hour once per (file, params) combo."""
    df, _ = cached_df_with_derived(raw)
    return utci_module.add_utci_columns(
        df, lat, lon, tz_str,
        posture=posture, sky_view_factor=sky_view_factor,
        shade_fraction=shade_fraction, ground_reflectance=ground_reflectance,
    )


@st.cache_data(show_spinner=False)
def cached_utci_daily_stats(raw: str, lat: float, lon: float, tz_str: str,
                             posture: str, sky_view_factor: float,
                             shade_fraction: float, ground_reflectance: float):
    """Daily UTCI/DBT/MRT stats, cached alongside cached_utci_df."""
    utci_df = cached_utci_df(raw, lat, lon, tz_str, posture, sky_view_factor,
                              shade_fraction, ground_reflectance)
    return utci_module.compute_daily_stats(utci_df)


# ─── Logo helper ──────────────────────────────────────────────────────────────

def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except Exception:
        try:
            with open(f"../{image_path}", "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()
        except Exception:
            return ""


logo_base64 = get_base64_image("images/EDSlogo.jpg")

# ─── Header ───────────────────────────────────────────────────────────────────

col_logo, col_title, col_home = st.columns([1, 4, 1], gap="large")

with col_logo:
    st.markdown(
        f'<img src="data:image/png;base64,{logo_base64}" style="height: 80px; margin-top: 45px;">',
        unsafe_allow_html=True,
    )

with col_title:
    st.markdown(
        '<h2 style="text-align: center; color: #a85c42; margin-top: 45px;">'
        "Climate Analytics Dashboard</h2>",
        unsafe_allow_html=True,
    )

with col_home:
    st.markdown(
        """
        <style>
        div[data-testid="stButton"] > button {
            background-color: #a85c42;
            color: white;
            width: 30px;
            margin-top: 60px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if st.button("←Home", key="home_nav"):
        st.session_state.pop("epw_url", None)
        st.switch_page("app.py")

st.markdown(
    """
    <style>
    div[data-testid="stHorizontalBlock"]:first-of-type {
        border-bottom: 1px solid #e6e6e6;
        padding-bottom: 20px;
        margin-bottom: 0px;
        background-color: white;
    }
    div[data-testid="stHorizontalBlock"]:first-of-type img:hover {
        transform: scale(1.05);
        opacity: 0.70;
        transition: all 0.3s ease;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Global CSS ───────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .block-container { padding-top: 0rem !important; }
    section[data-testid="stSidebar"] { display: none !important; }
    button[kind="header"] { display: none !important; }
    .main .block-container {
        max-width: 100% !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }
    .control-section-header {
        font-size: 15px; font-weight: 700; color: #2c3e50;
        text-transform: uppercase; letter-spacing: 0.5px;
        margin-bottom: 12px; margin-top: 16px;
        display: flex; align-items: center; gap: 8px; width: 200px;
    }
    .control-section-header:first-child { margin-top: 0; }
    [data-testid="fileUploadDropzone"] {
        border-radius: 6px !important; border-color: #cbd5e0 !important;
    }
    .stSlider   { margin-top: 12px; }
    .stDateInput { margin-top: 12px; }
    .stSelectbox { margin-top: 8px; }
    .section-title {
        font-size: 18px; font-weight: 700; color: #2c3e50;
        margin-bottom: 16px; border-bottom: 2px solid #3498db;
        padding-bottom: 8px; display: inline-block;
    }
    .kpi-card {
        background: white; padding: 16px; border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.08); text-align: center;
    }
    .kpi-label {
        font-size: 11px; font-weight: 700; color: #718096;
        text-transform: uppercase; letter-spacing: 0.5px;
        margin-bottom: 8px; opacity: 0.9;
    }
    .kpi-value { font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0; }
    .kpi-meta  { font-size: 11px; color: #718096; opacity: 0.85; }
    div.stButton, div.stDownloadButton { display: flex !important; justify-content: center !important; }
    div.stButton > button, div.stDownloadButton > button,
    a.stLinkButton, a.stLinkButton > button, button[data-baseweb="button"] {
        padding: 8px 14px !important;
        font-size: 14px !important;
        line-height: 1.2 !important;
        white-space: normal !important;
        word-break: break-word !important;
        min-width: 200px !important;
        max-width: 260px !important;
        width: auto !important;
        min-height: 40px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 8px !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        box-sizing: border-box !important;
        margin-bottom: 12px !important;
    }
    a.stLinkButton {
        display: inline-flex !important; align-items: center !important;
        justify-content: center !important; padding: 8px 14px !important;
        text-decoration: none !important; color: inherit !important;
        border-radius: 8px !important; margin-bottom: 12px !important;
    }
    /* Style native st.tabs to match dashboard palette */
    button[data-baseweb="tab"] {
        font-size: 24px !important;
        font-weight: 800 !important;
        color: #495057 !important;
        padding: 12px 20px !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        font-size: 24px !important;
        font-weight: 800 !important;
        color: #a85c42 !important;
        border-bottom-color: #a85c42 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Main layout ──────────────────────────────────────────────────────────────

col_left, col_right = st.columns([0.85, 2.15], gap="small")

with col_left:
    st.write("##### 📤 Upload EPW File")

    uploaded = st.file_uploader("", type=["epw"], label_visibility="collapsed", width=300)

    # Show banner when EPW was auto-loaded from a URL ?location= parameter
    if st.session_state.get("epw_auto_location") and st.session_state.get("epw_url"):
        st.info(
            f"EPW file auto-loaded for **{st.session_state['epw_auto_location']}**. "
            "Upload a different file above to override."
        )

    # If user selected an EPW from the main page (or auto-loaded via URL param), fetch it
    if uploaded is None and st.session_state.get("epw_url"):
        try:
            epw_url = st.session_state.get("epw_url")
            resp    = urllib.request.urlopen(epw_url)
            content = resp.read()

            is_zip = content[:4] == b"PK\x03\x04"
            if not is_zip:
                ct = resp.getheader("Content-Type") or ""
                is_zip = "zip" in ct.lower()

            if is_zip:
                import zipfile
                z         = zipfile.ZipFile(io.BytesIO(content))
                epw_names = [n for n in z.namelist() if n.lower().endswith(".epw")]
                if not epw_names:
                    raise Exception("ZIP did not contain any .epw files")
                uploaded = io.BytesIO(z.read(epw_names[0]))
            else:
                uploaded = io.BytesIO(content)
            st.write(f"EPW name: {epw_names[0] if is_zip else epw_url}")
        except Exception as _e:
            st.error(f"Failed to fetch EPW from URL: {_e}")

    st.write("##### Module")
    selected_parameter = st.selectbox(
        "Select parameter",
        ["Temperature", "Humidity", "Sun Path", "Wind", "Psychrometrics",
         "Ventilation", "Thermal Comfort", "UTCI", "Rainfall", "Solar PV",
         "Site Analysis (3D)", "Shading Designer (3D)"],
        label_visibility="collapsed",
        key="parameter_selector",
        width=300,
    )

if uploaded is None and selected_parameter not in ("Rainfall", "Solar PV"):
    with col_left:
        st.info("Please upload an .epw file to analyze.", width=300)
    st.stop()

# ─── Read raw bytes once (used as cache key) ─────────────────────────────────

if uploaded is not None:
    raw_epw = uploaded.getvalue().decode("utf-8", errors="replace")

    # ─── Parse EPW using cache ────────────────────────────────────────────────

    try:
        df, metadata = cached_df_with_derived(raw_epw)
    except Exception as e:
        with col_left:
            st.error(f"❌ Failed to parse EPW: {e}")
        st.stop()
else:
    raw_epw  = None
    df       = None
    metadata = {}

# ─── Left-panel controls ──────────────────────────────────────────────────────

with col_left:
    if selected_parameter == "Sun Path":
        hour_range = (0, 23)

        st.markdown('<div class="control-section-header">🌡️ Overheating Thresholds</div>', unsafe_allow_html=True)
        st.number_input(
            "Temperature Threshold (°C)", value=28.0, step=0.5,
            key="temp_threshold",
            help="Hours with dry-bulb temperature above this value are flagged",
            width=300,
        )
        st.number_input(
            "Radiation Threshold (W/m²)", value=315.0, step=10.0,
            key="rad_threshold",
            help="Hours with Global Horizontal Irradiance above this value are flagged",
            width=300,
        )

        st.markdown('<div class="control-section-header">📍 Location</div>', unsafe_allow_html=True)
        st.number_input(
            "Latitude (°)",  value=float(metadata.get("latitude")  or 0.0),
            step=0.1, key="shading_lat", help="Auto-read from EPW metadata", width=300,
        )
        st.number_input(
            "Longitude (°)", value=float(metadata.get("longitude") or 0.0),
            step=0.1, key="shading_lon", help="Auto-read from EPW metadata", width=300,
        )

        st.markdown('<div class="control-section-header">📐 Design Parameters</div>', unsafe_allow_html=True)
        st.number_input(
            "Design Cutoff Angle (°)", value=45.0, step=1.0,
            min_value=5.0, max_value=89.0, key="design_cutoff_angle",
            help="Solar altitude cutoff used to assess shading device protection",
            width=300,
        )

    elif selected_parameter == "Wind":
        hour_range = (0, 23)

        st.markdown('<div class="control-section-header">🧭 Direction Sectors</div>', unsafe_allow_html=True)
        st.select_slider(
            "Sectors",
            options=[4, 8, 12, 16, 24, 32, 36],
            value=16,
            key="wind_n_sectors",
            label_visibility="collapsed",
            help="Number of compass sectors in the wind rose (8 or 16 recommended)",
            width=300,
        )

        st.markdown('<div class="control-section-header">⚙️ Wind Options</div>', unsafe_allow_html=True)
        st.toggle(
            "Renormalise by non-calm hours",
            value=False,
            key="wind_exclude_calm",
            help=(
                "Off: bar frequencies = % of total hours. "
                "On: % of non-calm hours only."
            ),
        )

    elif selected_parameter == "Thermal Comfort":
        st.markdown('<div class="control-section-header">⏰ Time Range (Hours)</div>', unsafe_allow_html=True)
        hour_range = st.slider(
            "Select hours (start - end)", min_value=0, max_value=23,
            value=(0, 23), step=1, key="hour_range",
            label_visibility="collapsed", width=300,
        )

        st.markdown('<div class="control-section-header">🌡️ Comfort Model</div>', unsafe_allow_html=True)
        st.selectbox(
            "Comfort model",
            ["Both", "Adaptive", "Static"],
            key="tc_comfort_model",
            label_visibility="collapsed",
            help="Adaptive = ASHRAE 55 running mean; Static = fixed 22–26°C / 30–60% RH zone",
            width=300,
        )

        st.markdown('<div class="control-section-header">💨 Air Speed</div>', unsafe_allow_html=True)
        st.toggle(
            "Wind-speed comfort adjustment",
            value=False,
            key="tc_air_speed_adjust",
            help="Raise upper comfort limit by 1.5°C when wind speed > 1.5 m/s",
        )

    elif selected_parameter == "UTCI":
        hour_range = (0, 23)

        st.markdown('<div class="control-section-header">🧍 Person</div>', unsafe_allow_html=True)
        st.selectbox(
            "Posture",
            ["walking", "standing", "sitting", "supine"],
            key="utci_posture",
            label_visibility="collapsed",
            help=(
                "Body posture used in the ASHRAE 55 SolarCal shortwave MRT model. "
                "\"Walking\" is modeled as standing — ASHRAE 55 has no separate "
                "projected-area-factor table for an ambulatory posture."
            ),
            width=300,
        )

        st.markdown('<div class="control-section-header">☀️ Solar Exposure</div>', unsafe_allow_html=True)
        st.slider(
            "Sky view factor", min_value=0.0, max_value=1.0, value=1.0, step=0.05,
            key="utci_sky_view_factor",
            help="Fraction of sky visible to the person (1 = fully open sky, lower = obstructed by buildings/trees)",
            width=300,
        )
        st.slider(
            "Shade fraction", min_value=0.0, max_value=1.0, value=0.0, step=0.05,
            key="utci_shade_fraction",
            help="Fraction of the body shaded from direct sun (0 = fully sun-exposed)",
            width=300,
        )
        st.slider(
            "Ground reflectance (albedo)", min_value=0.0, max_value=0.6, value=0.2, step=0.05,
            key="utci_ground_reflectance",
            help="Reflectance of the ground surface, used by the shortwave MRT model",
            width=300,
        )

    elif selected_parameter == "Rainfall":
        hour_range = (0, 23)

        st.markdown('<div class="control-section-header">🌧️ Station</div>',
                    unsafe_allow_html=True)
        st.selectbox(
            "Station",
            options=list(rainfall_module.STATIONS.keys()),
            key="rainfall_station",
            label_visibility="collapsed",
            width=300,
        )

        st.markdown('<div class="control-section-header">📅 Year</div>',
                    unsafe_allow_html=True)
        st.number_input(
            "Year", value=2023, min_value=1950, max_value=2024,
            step=1, key="rainfall_year",
            label_visibility="collapsed", width=300,
        )

        st.markdown('<div class="control-section-header">⚠️ Thresholds</div>',
                    unsafe_allow_html=True)
        st.number_input(
            "Heavy rain threshold (mm/day)",
            value=50.0, step=5.0, min_value=10.0, max_value=200.0,
            key="rainfall_heavy_threshold",
            help="Days at or above this are classified as extreme rain events",
            width=300,
        )

        st.markdown('<div class="control-section-header">🏠 Building</div>',
                    unsafe_allow_html=True)
        st.number_input(
            "Roof area (m²)",
            value=200.0, step=10.0, min_value=10.0, max_value=50000.0,
            key="rainfall_roof_area",
            help="Used to estimate monthly roof runoff volume",
            width=300,
        )

        st.markdown('<div class="control-section-header">📊 Report (PowerPoint)</div>',
                    unsafe_allow_html=True)
        try:
            from modules import rainfall_ppt as _rain_ppt
            _rain_station = st.session_state.get("rainfall_station", "New Delhi (Safdarjung)")
            _rain_sid     = rainfall_module.STATIONS[_rain_station]
            _rain_year    = int(st.session_state.get("rainfall_year", 2023))
            _rain_s       = st.session_state.get("start_month_idx", 0) + 1
            _rain_e       = st.session_state.get("end_month_idx", 11) + 1
            _rain_thresh  = float(st.session_state.get("rainfall_heavy_threshold", 50.0))
            _rain_areas   = {
                "roof":  float(st.session_state.get(
                    "runoff_area_roof",
                    st.session_state.get("rainfall_roof_area", 200.0))),
                "paved": float(st.session_state.get("runoff_area_paved", 0.0)),
                "green": float(st.session_state.get("runoff_area_green", 0.0)),
                "water": float(st.session_state.get("runoff_area_water", 0.0)),
            }
            if st.button("🛠️ Build Rainfall Report", key="gen_rainfall_report",
                         width=300):
                with st.spinner("Building rainfall report…"):
                    st.session_state["rainfall_report_bytes"] = (
                        _rain_ppt.generate_rainfall_pptx_report(
                            station_name         = _rain_station,
                            station_id           = _rain_sid,
                            year                 = _rain_year,
                            start_month          = _rain_s,
                            end_month            = _rain_e,
                            heavy_rain_threshold = _rain_thresh,
                            surface_areas        = _rain_areas,
                            gi_percentile        = int(st.session_state.get("balance_percentile", 95)),
                            gi_start_year        = int(st.session_state.get("balance_start_year", 1990)),
                        )
                    )
            if st.session_state.get("rainfall_report_bytes"):
                _rain_downloaded = st.download_button(
                    label="⬇️ Download Rainfall Report",
                    data=st.session_state["rainfall_report_bytes"],
                    file_name=f"Rainfall_Analysis_{_rain_station}_{_rain_year}.pptx",
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    key="download_rainfall_report",
                    width=300,
                )
                if _rain_downloaded:
                    analytics.log_download(
                        "Rainfall Report (PPTX)",
                        detail=f"{_rain_station}, {_rain_year}",
                    )
        except Exception as _re:
            st.error(f"❌ Failed to generate rainfall report: {_re}")

    elif selected_parameter == "Solar PV":
        hour_range = (0, 23)

    elif selected_parameter in ("Psychrometrics", "Site Analysis (3D)",
                                "Shading Designer (3D)"):
        hour_range = (0, 23)
        st.caption("All controls for this module are on the analysis canvas →")

    elif selected_parameter == "Ventilation":
        hour_range = (0, 23)

        st.markdown('<div class="control-section-header">💨 Wind Threshold</div>', unsafe_allow_html=True)
        st.number_input(
            "Cross-vent wind threshold (m/s)",
            value=1.5, step=0.5, min_value=0.5, max_value=10.0,
            key="vent_wind_threshold",
            help="Minimum wind speed for a cross-ventilation usable hour",
            width=300,
        )

        st.markdown('<div class="control-section-header">🌡️ Comfort Temperature</div>', unsafe_allow_html=True)
        st.number_input(
            "Comfort min (°C)",
            value=24.0, step=0.5, min_value=10.0, max_value=32.0,
            key="vent_comfort_min",
            help="Lower bound of indoor comfort temperature band",
            width=300,
        )
        st.number_input(
            "Comfort max (°C)",
            value=26.0, step=0.5, min_value=10.0, max_value=35.0,
            key="vent_comfort_max",
            help="Upper bound — used as stack-ventilation trigger temperature",
            width=300,
        )

        st.markdown('<div class="control-section-header">🔧 ACH Model</div>', unsafe_allow_html=True)
        st.number_input(
            "Opening factor",
            value=0.25, step=0.05, min_value=0.05, max_value=0.80,
            key="vent_opening_factor",
            help="Fraction of wall area assumed open (ACH simplified model)",
            width=300,
        )
        st.number_input(
            "Ventilation effectiveness",
            value=0.50, step=0.05, min_value=0.10, max_value=1.00,
            key="vent_effectiveness",
            help="Wind-to-airflow conversion factor (Cv)",
            width=300,
        )

    else:
        st.markdown('<div class="control-section-header">⏰ Time Range (Hours)</div>', unsafe_allow_html=True)
        hour_range = st.slider(
            "Select hours (start - end)", min_value=0, max_value=23,
            value=(0, 23), step=1, key="hour_range",
            label_visibility="collapsed", width=300,
        )

    # ── Date range ─────────────────────────────────────────────────────────────
    st.markdown('<div class="control-section-header">📅 Date Range</div>', unsafe_allow_html=True)

    months_list = [
        "January","February","March","April","May","June",
        "July","August","September","October","November","December",
    ]
    if "start_month_idx" not in st.session_state:
        st.session_state.start_month_idx = 0
    if "end_month_idx" not in st.session_state:
        st.session_state.end_month_idx = 11

    month_col1, month_col2, month_col3= st.columns([1, 1, 0.8], gap="small")

    with month_col1:
        start_month = st.selectbox(
            "From", options=range(len(months_list)),
            format_func=lambda x: months_list[x],
            key="start_month_select", label_visibility="collapsed", width=150,
        )
        st.session_state.start_month_idx = start_month

    with month_col2:
        end_month_options = list(range(start_month, len(months_list)))
        if st.session_state.end_month_idx < start_month:
            st.session_state.end_month_idx = start_month
        end_month = st.selectbox(
            "To", options=end_month_options,
            format_func=lambda x: months_list[x],
            key="end_month_select",
            index=min(st.session_state.end_month_idx - start_month, len(end_month_options) - 1),
            label_visibility="collapsed", width=150,
        )
        st.session_state.end_month_idx = end_month

    # ── PowerPoint report download ─────────────────────────────────────────────
    if uploaded is not None:
        st.markdown('<div class="control-section-header">📊 Report (PowerPoint)</div>', unsafe_allow_html=True)

        try:
            _year   = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
            _s_num  = st.session_state.start_month_idx + 1
            _e_num  = st.session_state.end_month_idx + 1
            _start_date = pd.to_datetime(f"{_year}-{_s_num}-01").date()
            _end_date   = (
                pd.to_datetime(f"{_year}-12-31").date()
                if _e_num == 12
                else (pd.to_datetime(f"{_year}-{_e_num+1}-01") - pd.Timedelta(days=1)).date()
            )
            _sh, _eh = st.session_state.get("hour_range", (8, 18))

            _full_year_start     = pd.to_datetime(f"{_year}-01-01").date()
            _full_year_end       = pd.to_datetime(f"{_year}-12-31").date()
            _full_day_start_hour = 0
            _full_day_end_hour   = 23

            # Rainfall params — included only when a station has been selected
            _rain_stn = st.session_state.get("rainfall_station")
            _rain_sid = rainfall_module.STATIONS.get(_rain_stn) if _rain_stn else None
            _rain_yr  = int(st.session_state.get("rainfall_year", 2023)) if _rain_stn else None

            _combined_clicked = st.button(
                "🛠️ Build Combined Report", key="gen_combined_report", width=300,
                help="Generates the full climate & shading PPTX (takes a few seconds)",
            )
            if _combined_clicked:
              with st.spinner("Building combined report…"):
                st.session_state["combined_report_bytes"] = generate_combined_pptx_report(
                    df, _full_year_start, _full_year_end, _full_day_start_hour, _full_day_end_hour,
                selected_parameter, metadata=metadata,
                temp_threshold=float(st.session_state.get("temp_threshold", 28.0)),
                rad_threshold=float(st.session_state.get("rad_threshold", 315.0)),
                design_cutoff_angle=float(st.session_state.get("design_cutoff_angle", 45.0)),
                n_sectors=int(st.session_state.get("wind_n_sectors", 16)),
                include_thermal_comfort=True,
                rainfall_station_name=_rain_stn,
                rainfall_station_id=_rain_sid,
                rainfall_year=_rain_yr,
                rainfall_start_month=st.session_state.get("start_month_idx", 0) + 1,
                rainfall_end_month=st.session_state.get("end_month_idx", 11) + 1,
                rainfall_heavy_threshold=float(st.session_state.get("rainfall_heavy_threshold", 50.0)),
                rainfall_surface_areas={
                    "roof":  float(st.session_state.get("runoff_area_roof",
                                   st.session_state.get("rainfall_roof_area", 200.0))),
                    "paved": float(st.session_state.get("runoff_area_paved", 0.0)),
                    "green": float(st.session_state.get("runoff_area_green", 0.0)),
                    "water": float(st.session_state.get("runoff_area_water", 0.0)),
                } if _rain_stn else None,
                rainfall_gi_percentile=int(st.session_state.get("balance_percentile", 95)),
                rainfall_gi_start_year=int(st.session_state.get("balance_start_year", 1990)),
                solar_pv_country=st.session_state.get("solar_pv_country", "India"),
                solar_pv_roof_size_m2=float(st.session_state.get("solar_pv_roof_size", 100)),
                solar_pv_roof_pct=float(st.session_state.get("solar_pv_roof_pct", 80)),
                )

            _combined_filename = (
                f"Climate_Shading_Analysis_Report_"
                f"{_full_year_start.strftime('%Y%m%d')}_to_{_full_year_end.strftime('%Y%m%d')}.pptx"
            )
            if st.session_state.get("combined_report_bytes"):
                _combined_downloaded = st.download_button(
                    label="⬇️ Download Combined Climate & Shading Report",
                    data=st.session_state["combined_report_bytes"],
                    file_name=_combined_filename,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    key="download_combined_report",
                    width=300,
                )
                if _combined_downloaded:
                    analytics.log_download(
                        "Combined Climate & Shading Report (PPTX)",
                        detail=_combined_filename,
                    )
        except Exception as _e:
            st.error(f"❌ Failed to generate report: {_e}")

# ─── Right panel ──────────────────────────────────────────────────────────────

with col_right:
    if selected_parameter == "Sun Path":
        sp_tab_3d, sp_tab_2d = st.tabs(["🌐 3D Interactive Sun Path",
                                        "2D Sun Path & Shading Analysis"])
        with sp_tab_3d:
            render_sun_path_3d(df, metadata)
        with sp_tab_2d:
            render_sun_path_section(df, metadata)

    elif selected_parameter == "Psychrometrics":
        _s = st.session_state.start_month_idx + 1
        _e = st.session_state.end_month_idx + 1
        psychro_module.render(df, metadata, months=list(range(_s, _e + 1)))

    elif selected_parameter == "Site Analysis (3D)":
        site_analysis_module.render(df, metadata)

    elif selected_parameter == "Shading Designer (3D)":
        shading_designer_module.render(df, metadata)

    elif selected_parameter == "Wind":
        _s = st.session_state.start_month_idx + 1
        _e = st.session_state.end_month_idx + 1
        wind_module.render_wind_analysis(
            df,
            months       = list(range(_s, _e + 1)),
            n_sectors    = st.session_state.get("wind_n_sectors", 16),
            exclude_calm = st.session_state.get("wind_exclude_calm", False),
        )

    elif selected_parameter == "Thermal Comfort":
        _s = st.session_state.start_month_idx + 1
        _e = st.session_state.end_month_idx + 1
        _sh, _eh = hour_range
        thermal_comfort_module.render(
            df,
            months           = list(range(_s, _e + 1)),
            comfort_model    = st.session_state.get("tc_comfort_model", "Both"),
            air_speed_adjust = bool(st.session_state.get("tc_air_speed_adjust", False)),
            start_hour       = _sh,
            end_hour         = _eh,
        )

    elif selected_parameter == "UTCI":
        _lat = float(metadata.get("latitude") or 0.0)
        _lon = float(metadata.get("longitude") or 0.0)
        _tz  = metadata.get("timezone", "UTC")
        _posture = st.session_state.get("utci_posture", "walking")
        _svf     = float(st.session_state.get("utci_sky_view_factor", 1.0))
        _shade   = float(st.session_state.get("utci_shade_fraction", 0.0))
        _ground  = float(st.session_state.get("utci_ground_reflectance", 0.2))

        utci_df    = cached_utci_df(raw_epw, _lat, _lon, _tz, _posture, _svf, _shade, _ground)
        utci_daily = cached_utci_daily_stats(raw_epw, _lat, _lon, _tz, _posture, _svf, _shade, _ground)

        _year            = utci_df["datetime"].dt.year.iloc[0] if not utci_df.empty else 2024
        _start_month_num = st.session_state.start_month_idx + 1
        _end_month_num   = st.session_state.end_month_idx + 1
        _utci_start_date = pd.to_datetime(f"{_year}-{_start_month_num}-01").date()
        _utci_end_date   = (
            pd.to_datetime(f"{_year}-12-31").date()
            if _end_month_num == 12
            else (pd.to_datetime(f"{_year}-{_end_month_num+1}-01") - pd.Timedelta(days=1)).date()
        )
        _sh, _eh = hour_range

        tabs_list = ["Overview", "Annual Trend"]
        tab_objects = st.tabs(tabs_list)
        for tab_obj, tab_name in zip(tab_objects, tabs_list):
            with tab_obj:
                utci_module.render(
                    utci_df, utci_daily, tab_name,
                    _utci_start_date, _utci_end_date, _sh, _eh,
                )

    elif selected_parameter == "Rainfall":
        rainfall_module.render(
            station_id  = rainfall_module.STATIONS[st.session_state.get(
                              "rainfall_station",
                              "New Delhi (Safdarjung)")],
            year        = int(st.session_state.get("rainfall_year", 2023)),
            start_month = st.session_state.start_month_idx + 1,
            end_month   = st.session_state.end_month_idx + 1,
            heavy_rain_threshold = float(st.session_state.get(
                                       "rainfall_heavy_threshold", 50.0)),
            roof_area_m2 = float(st.session_state.get(
                                       "rainfall_roof_area", 200.0)),
        )

    elif selected_parameter == "Solar PV":
        solar_pv_module.render()

    elif selected_parameter == "Ventilation":
        _s = st.session_state.start_month_idx + 1
        _e = st.session_state.end_month_idx + 1
        ventilation_module.render(
            df,
            months         = list(range(_s, _e + 1)),
            wind_threshold = float(st.session_state.get("vent_wind_threshold", 1.5)),
            comfort_min    = float(st.session_state.get("vent_comfort_min",    24.0)),
            comfort_max    = float(st.session_state.get("vent_comfort_max",    26.0)),
            opening_factor = float(st.session_state.get("vent_opening_factor",  0.25)),
            effectiveness  = float(st.session_state.get("vent_effectiveness",   0.50)),
        )

    else:
        # ── Resolve date / time range ───────────────────────────────────────────
        year            = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
        start_month_num = st.session_state.start_month_idx + 1
        end_month_num   = st.session_state.end_month_idx + 1

        start_date = pd.to_datetime(f"{year}-{start_month_num}-01").date()
        end_date   = (
            pd.to_datetime(f"{year}-12-31").date()
            if end_month_num == 12
            else (pd.to_datetime(f"{year}-{end_month_num+1}-01") - pd.Timedelta(days=1)).date()
        )
        start_hour, end_hour = st.session_state.get("hour_range", (8, 18))

        # ── Shared computations (cached) ────────────────────────────────────────
        daily_stats = cached_daily_stats(raw_epw)

        # Merge ASHRAE adaptive comfort bands (cached)
        c80lo, c80hi, c90lo, c90hi = cached_ashrae_comfort(raw_epw)
        comfort_df = pd.DataFrame({
            "doy":              c80lo.index,
            "comfort_80_lower": c80lo.values,
            "comfort_80_upper": c80hi.values,
            "comfort_90_lower": c90lo.values,
            "comfort_90_upper": c90hi.values,
        })
        daily_stats = daily_stats.merge(comfort_df, on="doy", how="left")

        # ── Native st.tabs() – zero server round-trips on switch ────────────────
        tabs_list = ["Annual Trend", "Monthly Trend", "Diurnal Profile",
                     "Heatmap + 3D", "Comfort Analysis", "Energy Metrics"]

        tab_objects = st.tabs(tabs_list)

        for tab_obj, tab_name in zip(tab_objects, tabs_list):
            with tab_obj:
                if selected_parameter == "Temperature":
                    dbt_module.render(
                        df, daily_stats, tab_name,
                        start_date, end_date, start_hour, end_hour,
                    )
                elif selected_parameter == "Humidity":
                    humidity_module.render(
                        df, daily_stats, tab_name,
                        start_date, end_date, start_hour, end_hour,
                    )

# ─── Footer ───────────────────────────────────────────────────────────────────

st.markdown("<br><br>", unsafe_allow_html=True)
st.image("images/EDS-footer.png", width=2000)
st.markdown(
    """
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <div style="text-align:center; font-size:14px;">
        Email: <a href="mailto:info@edsglobal.com">info@edsglobal.com</a>   |
        Phone: +91 . 11 . 4056 8633   |
        <a href="https://twitter.com/edsglobal?lang=en" target="_blank">
            <i class="fab fa-twitter" style="color:#1DA1F2; margin:0 6px;"></i></a>
        <a href="https://www.facebook.com/Environmental.Design.Solutions/" target="_blank">
            <i class="fab fa-facebook" style="color:#4267B2; margin:0 6px;"></i></a>
        <a href="https://www.instagram.com/eds_global/?hl=en" target="_blank">
            <i class="fab fa-instagram" style="color:#E1306C; margin:0 6px;"></i></a>
        <a href="https://www.linkedin.com/company/environmental-design-solutions/" target="_blank">
            <i class="fab fa-linkedin" style="color:#0077b5; margin:0 6px;"></i></a>
    </div>
    """,
    unsafe_allow_html=True,
)