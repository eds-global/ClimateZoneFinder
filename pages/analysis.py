from turtle import width
import streamlit as st
import base64
import pandas as pd
import plotly.express as px
import io
import re

st.set_page_config(
    page_title="Climate Analytics Dashboard",
    page_icon="🌍",
    layout="wide"
)

def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except:
        try:
            with open(f"../{image_path}", "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()
        except:
            return ""

# === HEADER ===
st.markdown("""
    <style>
    .header-container {
        background: linear-gradient(135deg, #1a3a52 0%, #2c5aa0 100%);
        padding: 0px;
        border-radius: 0;
        margin-top: 50px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        border-bottom: 4px solid #ffffff;

           /* 👈 pushes it below Streamlit toolbar */
        left: 0;
        right: 0;
        z-index: 999;
        width: 100%;
        box-sizing: border-box;
    }
    style>
    /* Hide top toolbar */
    header[data-testid="stHeader"] {
        display: none;
    }

    /* Hide hamburger menu */
    #MainMenu {
        visibility: hidden;
    }

    /* Hide footer */
    footer {
        visibility: hidden;
    }

    /* Remove top padding since header is gone */
    .block-container {
        padding-top: 1rem;
    }

    /* Optional: Remove deploy button */
    div[data-testid="stToolbar"] {
        display: none;
    }
    /* Adjust body spacing to avoid overlap */
    .main > div {
        padding-top: 180px;   /* Increase if needed */
    }

    .header-content {
        display: flex;
        align-items: center;
        gap: 20px;
    }

    .header-icon {
        font-size: 48px;
        display: inline-block;
    }

    .header-title {
        color: #ffffff;
        font-size: 32px;
        font-weight: 800;
        margin: 0;
        letter-spacing: 0.5px;
    }
    </style>

    <div class="header-container">
        <div class="header-content">
            <div class="header-icon">🌍</div>
            <div class="header-title">Climate Analytics Dashboard</div>
        </div>
    </div>
""", unsafe_allow_html=True)

# === INITIALIZE SESSION STATE FOR TABS ===
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Annual Trend"

st.markdown("""
    <style>
    /* Main layout adjustments */
    .block-container {
        padding-top: 0rem !important;
    }
    
    section[data-testid="stSidebar"] {
        display: none !important;
    }
    
    button[kind="header"] {
        display: none !important;
    }
    
    .main .block-container {
        max-width: 100% !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }
    
    
    
    .control-section-header {
        font-size: 13px;
        font-weight: 700;
        color: #2c3e50;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 12px;
        margin-top: 16px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    .control-section-header:first-child {
        margin-top: 0;
    }
    
    /* Upload zone styling */
    .upload-zone {
        border: 2px dashed #cbd5e0;
        border-radius: 6px;
        padding: 12px;
        text-align: center;
        background-color: #f7fafc;
        margin-top: 8px;
    }
    
    .upload-zone.success {
        border-color: #48bb78;
        background-color: #f0fff4;
    }
    
    /* File uploader styling */
    [data-testid="fileUploadDropzone"] {
        border-radius: 6px !important;
        border-color: #cbd5e0 !important;
    }
    
    /* Success message styling */
    .stAlert[data-baseweb="notification"] {
        background-color: #f0fff4 !important;
        border-left: 4px solid #48bb78 !important;
        border-radius: 4px !important;
    }
    
    /* Slider styling */
    .stSlider {
        margin-top: 12px;
    }
    
    /* Date input styling */
    .stDateInput {
        margin-top: 12px;
    }
    
    /* Selectbox styling */
    .stSelectbox {
        margin-top: 8px;
    }
    
    /* Section styling */
    .section-title {
        font-size: 18px;
        font-weight: 700;
        color: #2c3e50;
        margin-bottom: 16px;
        border-bottom: 2px solid #3498db;
        padding-bottom: 8px;
        display: inline-block;
    }
    
    /* KPI Cards */
    .kpi-card {
        background: white;
        padding: 16px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.08);
        text-align: center;
    }
    
    .kpi-label {
        font-size: 11px;
        font-weight: 700;
        color: #718096;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 8px;
        opacity: 0.9;
    }
    
    .kpi-value {
        font-size: 26px;
        font-weight: 700;
        color: #2c3e50;
        margin: 8px 0;
    }
    
    .kpi-meta {
        font-size: 11px;
        color: #718096;
        opacity: 0.85;
    }
    </style>
""", unsafe_allow_html=True)

def parse_epw(epw_text: str) -> pd.DataFrame:
    """Parse EPW formatted text and return a DataFrame with datetime, dry_bulb_temperature, relative_humidity."""
    # split into lines and find first data row (starts with a year integer)
    lines = [ln.strip() for ln in epw_text.splitlines() if ln.strip() != ""]
    data_start = None
    for i, ln in enumerate(lines):
        # consider a line a data line if first token is a 4-digit year
        toks = ln.split(",")
        if len(toks) > 1 and re.fullmatch(r"\d{4}", toks[0].strip()):
            data_start = i
            break
    
    if data_start is None:
        raise ValueError("Could not locate EPW data rows")
    
    data_str = "\n".join(lines[data_start:])
    df_raw = pd.read_csv(io.StringIO(data_str), header=None)
    
    # EPW standard columns (0-based index):
    # 0=year,1=month,2=day,3=hour,4=minute,5=data source,6=dry bulb (C),7=dew point,8=relative humidity (%)
    col_map = {
        "year": 0,
        "month": 1,
        "day": 2,
        "hour": 3,
        "minute": 4,
        "dry_bulb_temperature": 6,
        "relative_humidity": 8,
    }
    
    # safety: ensure DataFrame has enough columns
    max_needed = max(col_map.values())
    if df_raw.shape[1] <= max_needed:
        raise ValueError("EPW data appears to have insufficient columns")
    
    df = pd.DataFrame()
    df["year"] = pd.to_numeric(df_raw.iloc[:, col_map["year"]], errors="coerce").astype("Int64")
    df["month"] = pd.to_numeric(df_raw.iloc[:, col_map["month"]], errors="coerce").astype("Int64")
    df["day"] = pd.to_numeric(df_raw.iloc[:, col_map["day"]], errors="coerce").astype("Int64")
    df["hour_raw"] = pd.to_numeric(df_raw.iloc[:, col_map["hour"]], errors="coerce").astype("Int64")
    df["minute"] = pd.to_numeric(df_raw.iloc[:, col_map["minute"]], errors="coerce").astype("Int64")
    
    # EPW hours are 1-24 representing the hour ending; convert to 0-23 by subtracting 1
    df["hour"] = (df["hour_raw"].fillna(1).astype(int) - 1) % 24
    
    df["dry_bulb_temperature"] = pd.to_numeric(df_raw.iloc[:, col_map["dry_bulb_temperature"]], errors="coerce")
    df["relative_humidity"] = pd.to_numeric(df_raw.iloc[:, col_map["relative_humidity"]], errors="coerce")
    
    # build datetime (note: this may produce NaT for invalid rows)
    df["datetime"] = pd.to_datetime(
        dict(year=df["year"], month=df["month"], day=df["day"], hour=df["hour"], minute=df["minute"]),
        errors="coerce",
    )
    
    df = df.dropna(subset=["datetime"]).reset_index(drop=True)
    return df[["datetime", "dry_bulb_temperature", "relative_humidity", "hour"]]

# === MAIN LAYOUT ===
col_left, col_right = st.columns([0.85, 2.15], gap="large")

with col_left:
    # st.markdown('<div class="control-panel">', unsafe_allow_html=True)
    
    # Upload EPW File Section
    st.markdown('<div class="control-section-header">📤 Upload EPW File</div>', unsafe_allow_html=True)
    # st.markdown('<div class="upload-zone">Limit 200MB per file · EPW</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("", type=["epw"], label_visibility="collapsed")
    
    # Parameter Selection
    st.markdown('<div class="control-section-header">⚙️ Parameter</div>', unsafe_allow_html=True)
    selected_parameter = st.selectbox(
        "Select parameter",
        ["Temperature", "Humidity", "Sun Path"],
        label_visibility="collapsed",
        key="parameter_selector",
    )

if uploaded is None:
    with col_right:
        st.info("Please upload an .epw file to analyze.")
    st.stop()

try:
    raw = uploaded.getvalue().decode("utf-8", errors="replace")
    df = parse_epw(raw)
    
    # compute derived date fields used by the charts
    df["doy"] = df["datetime"].dt.dayofyear
    df["day"] = df["datetime"].dt.day
    df["month"] = df["datetime"].dt.month
    df["month_name"] = df["datetime"].dt.strftime("%b")
    
    with col_left:
        st.markdown("""
            <div style="
                background-color: #f0fff4;
                border-left: 4px solid #48bb78;
                padding: 12px;
                border-radius: 4px;
                margin: 8px 0;
            ">
                <div style="color: #22543d; font-weight: 600; font-size: 12px;">✅ EPW parsed successfully</div>
            </div>
        """, unsafe_allow_html=True)
        
        # Time range (hour of use) slider for diurnal/peak analysis
        st.markdown('<div class="control-section-header">⏰ Time Range</div>', unsafe_allow_html=True)
        hour_range = st.slider(
            "Select hours (start - end)",
            min_value=0,
            max_value=23,
            value=(8, 18),
            step=1,
            key="hour_range",
            label_visibility="collapsed",
        )
        
        # Date range selection
        st.markdown('<div class="control-section-header">📅 Date Range</div>', unsafe_allow_html=True)
        
        months_list = ["January", "February", "March", "April", "May", "June", 
                       "July", "August", "September", "October", "November", "December"]
        
        # Initialize session state for month selection
        if "start_month_idx" not in st.session_state:
            st.session_state.start_month_idx = 0
        if "end_month_idx" not in st.session_state:
            st.session_state.end_month_idx = 11
        
        # Create two columns for start and end month dropdowns
        month_col1, month_col2 = st.columns(2, gap="small")
        
        with month_col1:
            start_month = st.selectbox(
                "From",
                options=range(len(months_list)),
                format_func=lambda x: months_list[x],
                key="start_month_select",
                label_visibility="collapsed"
            )
            st.session_state.start_month_idx = start_month
        
        with month_col2:
            # End month should only show months from start_month onwards
            end_month_options = list(range(start_month, len(months_list)))
            # Ensure selected end_month is valid
            if st.session_state.end_month_idx < start_month:
                st.session_state.end_month_idx = start_month
            
            end_month = st.selectbox(
                "To",
                options=end_month_options,
                format_func=lambda x: months_list[x],
                key="end_month_select",
                index=min(st.session_state.end_month_idx - start_month, len(end_month_options) - 1),
                label_visibility="collapsed"
            )
            st.session_state.end_month_idx = end_month
        
except Exception as e:
    with col_left:
        st.error(f"❌ Failed to parse EPW: {e}")
    st.stop()

def calculate_ashrae_comfort(df: pd.DataFrame) -> tuple:
    """
    Calculate ASHRAE adaptive comfort bands.
    Returns (comfort_80_lower, comfort_80_upper, comfort_90_lower, comfort_90_upper) as daily rolling averages.
    """
    # Simple ASHRAE adaptive comfort model (simplified)
    # For illustration: ranges relative to outdoor mean monthly temperature
    daily_avg = df.groupby("doy")["dry_bulb_temperature"].mean()
    
    # 80% acceptability: ±3.5°C from comfort line
    # 90% acceptability: ±2.5°C from comfort line
    comfort_line = daily_avg.rolling(window=7, center=True).mean()
    
    comfort_80_lower = comfort_line - 3.5
    comfort_80_upper = comfort_line + 3.5
    comfort_90_lower = comfort_line - 2.5
    comfort_90_upper = comfort_line + 2.5
    
    return comfort_80_lower, comfort_80_upper, comfort_90_lower, comfort_90_upper

# Compute ASHRAE comfort bands
ashrae_80_lower, ashrae_80_upper, ashrae_90_lower, ashrae_90_upper = calculate_ashrae_comfort(df)

with col_right:
    # === INTERACTIVE TABS AT TOP ===
    st.markdown("""
        <style>
        .tab-container {
            display: flex;
            gap: 0;
            background-color: #f8f9fa;
            padding: 0;
            margin: -1rem -1rem 1.5rem -1rem;
            border-bottom: 2px solid #e9ecef;
        }
        .tab-button {
            padding: 12px 24px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            color: #495057;
            background-color: #f8f9fa;
            border: none;
            border-bottom: 3px solid transparent;
            transition: all 0.3s ease;
        }
        .tab-button:hover {
            background-color: #e9ecef;
        }
        .tab-button.active {
            color: #2c3e50;
            border-bottom-color: #3498db;
            background-color: white;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Create tab buttons
    tabs_col1, tabs_col2, tabs_col3, tabs_col4, tabs_col5 = st.columns(5, gap="small")
    
    with tabs_col1:
        if st.button("Annual Trend", key="tab_annual", use_container_width=True):
            st.session_state.active_tab = "Annual Trend"
    
    with tabs_col2:
        if st.button("Monthly Trend", key="tab_monthly", use_container_width=True):
            st.session_state.active_tab = "Monthly Trend"
    
    with tabs_col3:
        if st.button("Diurnal Profile", key="tab_diurnal", use_container_width=True):
            st.session_state.active_tab = "Diurnal Profile"
    
    with tabs_col4:
        if st.button("Comfort Analysis", key="tab_comfort", use_container_width=True):
            st.session_state.active_tab = "Comfort Analysis"
    
    with tabs_col5:
        if st.button("Energy Metrics", key="tab_energy", use_container_width=True):
            st.session_state.active_tab = "Energy Metrics"
    
    # Add visual styling to show active tab
    st.markdown(f"""
        <script>
        var active_tab = '{st.session_state.active_tab}';
        </script>
    """, unsafe_allow_html=True)
    # Get the actual year from the data
    year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
    
    # Create start and end dates based on selected months
    start_month_num = st.session_state.start_month_idx + 1
    end_month_num = st.session_state.end_month_idx + 1
    
    start_date = pd.to_datetime(f"{year}-{start_month_num}-01").date()
    
    # For end_date, get the last day of the end month
    if end_month_num == 12:
        end_date = pd.to_datetime(f"{year}-12-31").date()
    else:
        end_date = (pd.to_datetime(f"{year}-{end_month_num+1}-01") - pd.Timedelta(days=1)).date()
    
    start_hour, end_hour = st.session_state.get("hour_range", (8, 18))
    
    # Compute daily min/max and average
    daily_stats = df.groupby("doy").agg({
        "dry_bulb_temperature": ["min", "max", "mean"],
        "relative_humidity": ["min", "max", "mean"],
    }).reset_index()
    
    daily_stats.columns = ["doy", "temp_min", "temp_max", "temp_avg", "rh_min", "rh_max", "rh_avg"]
    daily_stats["datetime_start"] = pd.to_datetime(daily_stats["doy"], format="%j", errors="coerce")
    
    # Map doy back to actual year-dates for chart display
    year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
    daily_stats["datetime"] = pd.to_datetime(
        daily_stats["doy"].astype(str) + f"-{year}", format="%j-%Y", errors="coerce"
    )
    
    # Add a day-month only column for display (without year)
    daily_stats["datetime_display"] = daily_stats["datetime"].dt.strftime("%b %d")
    
    # Map comfort bands to doy
    comfort_df = pd.DataFrame({
        "doy": ashrae_80_lower.index,
        "comfort_80_lower": ashrae_80_lower.values,
        "comfort_80_upper": ashrae_80_upper.values,
        "comfort_90_lower": ashrae_90_lower.values,
        "comfort_90_upper": ashrae_90_upper.values,
    })
    
    # Merge comfort data with daily stats
    daily_stats = daily_stats.merge(comfort_df, on="doy", how="left")

    # === TAB 1: ANNUAL TREND ===
    if st.session_state.active_tab == "Annual Trend":
        if selected_parameter == "Temperature":
            import plotly.graph_objects as go
            
            fig_yearly = go.Figure()
            
            # Add ASHRAE 80% band
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_80_upper"],
                fill=None,
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_80_lower"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                name="ASHRAE adaptive comfort (80%)",
                fillcolor="rgba(128, 128, 128, 0.2)",
                hoverinfo="skip",
            ))
            
            # Add ASHRAE 90% band
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_90_upper"],
                fill=None,
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_90_lower"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                name="ASHRAE adaptive comfort (90%)",
                fillcolor="rgba(128, 128, 128, 0.4)",
                hoverinfo="skip",
            ))
            
            # Add temperature range (min/max)
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_max"],
                fill=None,
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_min"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                name="Dry bulb temperature Range",
                fillcolor="rgba(255, 173, 173, 0.4)",
                customdata=daily_stats["temp_max"],
                hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>",
            ))
            
            # Add average line
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_avg"],
                mode="lines",
                name="Average Dry bulb temperature",
                line=dict(color="#d32f2f", width=2),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>",
            ))
            
            fig_yearly.update_layout(
                title="Annual Dry Bulb Temperature Trend",
                xaxis_title=None,
                yaxis_title="Temperature (°C)",
                hovermode="x unified",
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                xaxis_rangeslider_visible=False,
                height=450,
                template="plotly_white",
                margin=dict(b=80),
            )

            st.plotly_chart(fig_yearly, use_container_width=True)
            
            # Get filtered data for period
            filtered_df = df[
                (df["datetime"].dt.date >= start_date) &
                (df["datetime"].dt.date <= end_date) &
                (df["hour"].between(start_hour, end_hour))
            ]
            
            if not filtered_df.empty:
                # Get min/max values and their corresponding rows with datetime/hour info
                min_idx = filtered_df["dry_bulb_temperature"].idxmin()
                max_idx = filtered_df["dry_bulb_temperature"].idxmax()
                
                min_row = filtered_df.loc[min_idx]
                max_row = filtered_df.loc[max_idx]
                
                temp_min = min_row["dry_bulb_temperature"]
                temp_max = max_row["dry_bulb_temperature"]
                temp_avg = filtered_df["dry_bulb_temperature"].mean()
                diurnal_range = temp_max - temp_min
                
                # Extract date and hour information for min/max
                min_date_str = min_row["datetime"].strftime("%b %d")
                min_hour = int(min_row["hour"])
                
                max_date_str = max_row["datetime"].strftime("%b %d")
                max_hour = int(max_row["hour"])
                
                # Calculate additional metrics using full year data
                # HDD18 (Heating Degree Days at 18°C base)
                hdd18 = (18 - df["dry_bulb_temperature"]).clip(lower=0).sum()
                
                # CDD24 (Cooling Degree Days at 24°C base)
                cdd24 = (df["dry_bulb_temperature"] - 24).clip(lower=0).sum()
                
                # Comfort metrics (using full year data for comfort bands)
                def get_comfort_band_range(temps):
                    """Get comfort band as ±3.5°C from mean"""
                    mean_temp = temps.mean()
                    return mean_temp - 3.5, mean_temp + 3.5
                
                comfort_lower, comfort_upper = get_comfort_band_range(df["dry_bulb_temperature"])
                comfort_hours = len(df[(df["dry_bulb_temperature"] >= comfort_lower) & (df["dry_bulb_temperature"] <= comfort_upper)])
                comfort_80_percent = (comfort_hours / len(df)) * 100
                
                # 1% Cooling (99th percentile)
                cooling_1pct = df["dry_bulb_temperature"].quantile(0.99)
                
                # Overheat hours (hours above 28°C threshold)
                overheat_hrs = len(df[df["dry_bulb_temperature"] > 28])
                
                # Cold hours (hours below 12°C threshold)
                cold_hrs = len(df[df["dry_bulb_temperature"] < 12])
                
                # Temperature metrics cards - Row 1
                kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5 = st.columns(5)
                
                with kpi_col1:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #f59e0b;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #f59e0b; text-transform: uppercase; letter-spacing: 0.5px;">Min Temp</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{temp_min:.2f} °C</div>
                            <div style="font-size: 11px; color: #718096;">{min_date_str} · {min_hour:02d}:00</div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col2:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #ef4444;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #ef4444; text-transform: uppercase; letter-spacing: 0.5px;">Max Temp</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{temp_max:.2f} °C</div>
                            <div style="font-size: 11px; color: #718096;">{max_date_str} · {max_hour:02d}:00</div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col3:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #8b5cf6;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.5px;">Avg Temp</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{temp_avg:.2f} °C</div>
                            <div style="font-size: 11px; color: #718096;">All year average</div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col4:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #3b82f6;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #3b82f6; text-transform: uppercase; letter-spacing: 0.5px;">Diurnal Range</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{diurnal_range:.2f} °C</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col5:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #06b6d4;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #06b6d4; text-transform: uppercase; letter-spacing: 0.5px;">1% Cooling</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{cooling_1pct:.2f} °C</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                # Row 2 - Additional metrics
                kpi_col6, kpi_col7, kpi_col8, kpi_col9, kpi_col10 = st.columns(5)
                
                with kpi_col6:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #dc2626;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #dc2626; text-transform: uppercase; letter-spacing: 0.5px;">HDD18</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{hdd18:.0f}</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col7:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #0891b2;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #0891b2; text-transform: uppercase; letter-spacing: 0.5px;">CDD24</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{cdd24:.0f}</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col8:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #06b6d4;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #06b6d4; text-transform: uppercase; letter-spacing: 0.5px;">Comfort 80%</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{comfort_80_percent:.0f} %</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col9:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #8b5cf6;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.5px;">Overheat Hrs</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{overheat_hrs}</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col10:
                    st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 16px;
                            border-radius: 8px;
                            border-left: 4px solid #3b82f6;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                            text-align: center;
                        ">
                            <div style="font-size: 11px; font-weight: 700; color: #3b82f6; text-transform: uppercase; letter-spacing: 0.5px;">Cold Hrs</div>
                            <div style="font-size: 26px; font-weight: 700; color: #2c3e50; margin: 8px 0;">{cold_hrs}</div>
                            <div style="font-size: 11px; color: #718096;"></div>
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.info(f"No data available between {start_hour:02d}:00 and {end_hour:02d}:00 in the selected date range.")
        
        elif selected_parameter == "Humidity":
            import plotly.graph_objects as go
            
            # Define humidity comfort bands (typical comfort range 30-65%)
            humidity_comfort_lower = 30
            humidity_comfort_upper = 65
            
            fig_yearly = go.Figure()
            
            # Add humidity comfort band
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=[humidity_comfort_upper] * len(daily_stats),
                fill=None,
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=[humidity_comfort_lower] * len(daily_stats),
                fill="tonexty",
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                name="Humidity comfort band",
                fillcolor="rgba(128, 128, 128, 0.2)",
                hoverinfo="skip",
            ))
            
            # Add relative humidity range (min/max)
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["rh_max"],
                fill=None,
                mode="lines",
                line_color="rgba(0, 0, 255, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["rh_min"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(0, 0, 255, 0)",
                name="Relative humidity Range",
                fillcolor="rgba(0, 150, 255, 0.3)",
                hovertemplate="<b>%{x}</b><br>Min: %{y:.1f}%<extra></extra>",
            ))
            
            # Add average line
            fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["rh_avg"],
                mode="lines",
                name="Average Relative humidity",
                line=dict(color="#00a8ff", width=2),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.1f}%<extra></extra>",
            ))
            
            fig_yearly.update_layout(
                title="Annual Profile – Relative Humidity",
                xaxis_title="Day",
                yaxis_title="Relative Humidity (%)",
                hovermode="x unified",
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                xaxis_rangeslider_visible=False,
                height=450,
                template="plotly_white",
                margin=dict(b=80),
            )
            
            st.plotly_chart(fig_yearly, use_container_width=True)
        
        else:
            st.info("Sun Path analysis is not yet implemented.")

    # === TAB 2: MONTHLY TREND ===
    elif st.session_state.active_tab == "Monthly Trend":
        if selected_parameter == "Temperature":
            import plotly.graph_objects as go
            
            # Calculate monthly statistics
            monthly_stats = df.groupby("month").agg({
                "dry_bulb_temperature": ["min", "max", "mean"],
                "relative_humidity": ["min", "max", "mean"],
            }).reset_index()
            
            monthly_stats.columns = ["month", "temp_min", "temp_max", "temp_avg", "rh_min", "rh_max", "rh_avg"]
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            monthly_stats["month_name"] = monthly_stats["month"].apply(lambda x: month_names[x-1])
            
            fig_monthly = go.Figure()
            
            # Add temperature range (min/max)
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["temp_max"],
                fill=None,
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["temp_min"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                name="Monthly Temperature Range",
                fillcolor="rgba(255, 173, 173, 0.4)",
                customdata=monthly_stats["temp_max"],
                hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>",
            ))
            
            # Add average line
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["temp_avg"],
                mode="lines+markers",
                name="Monthly Average Temperature",
                line=dict(color="#d32f2f", width=2),
                marker=dict(size=8),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>",
            ))
            
            fig_monthly.update_layout(
                title="Monthly Temperature Trend",
                xaxis_title="Month",
                yaxis_title="Temperature (°C)",
                hovermode="x unified",
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                height=450,
                template="plotly_white",
                margin=dict(b=80),
            )
            
            st.plotly_chart(fig_monthly, use_container_width=True)
            
            # Display monthly KPI metrics
            st.markdown("#### Monthly Temperature Summary")
            
            # Create a dataframe for monthly metrics
            kpi_data = monthly_stats[["month_name", "temp_min", "temp_max", "temp_avg"]].copy()
            kpi_data.columns = ["Month", "Min (°C)", "Max (°C)", "Avg (°C)"]
            
            # Display as a nice table
            st.dataframe(
                kpi_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Min (°C)": st.column_config.NumberColumn(format="%.2f"),
                    "Max (°C)": st.column_config.NumberColumn(format="%.2f"),
                    "Avg (°C)": st.column_config.NumberColumn(format="%.2f"),
                }
            )
            
        elif selected_parameter == "Humidity":
            import plotly.graph_objects as go
            
            # Calculate monthly humidity statistics
            monthly_stats = df.groupby("month").agg({
                "relative_humidity": ["min", "max", "mean"],
            }).reset_index()
            
            monthly_stats.columns = ["month", "rh_min", "rh_max", "rh_avg"]
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            monthly_stats["month_name"] = monthly_stats["month"].apply(lambda x: month_names[x-1])
            
            fig_monthly = go.Figure()
            
            # Add humidity comfort band
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=[65] * len(monthly_stats),
                fill=None,
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=[30] * len(monthly_stats),
                fill="tonexty",
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                name="Humidity comfort band (30-65%)",
                fillcolor="rgba(128, 128, 128, 0.2)",
                hoverinfo="skip",
            ))
            
            # Add humidity range (min/max)
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["rh_max"],
                fill=None,
                mode="lines",
                line_color="rgba(0, 0, 255, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["rh_min"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(0, 0, 255, 0)",
                name="Monthly Humidity Range",
                fillcolor="rgba(0, 150, 255, 0.3)",
                hovertemplate="<b>%{x}</b><br>Min: %{y:.1f}%<extra></extra>",
            ))
            
            # Add average line
            fig_monthly.add_trace(go.Scatter(
                x=monthly_stats["month_name"],
                y=monthly_stats["rh_avg"],
                mode="lines+markers",
                name="Monthly Average Humidity",
                line=dict(color="#00a8ff", width=2),
                marker=dict(size=8),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.1f}%<extra></extra>",
            ))
            
            fig_monthly.update_layout(
                title="Monthly Relative Humidity Trend",
                xaxis_title="Month",
                yaxis_title="Relative Humidity (%)",
                hovermode="x unified",
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                height=450,
                template="plotly_white",
                margin=dict(b=80),
            )
            
            st.plotly_chart(fig_monthly, use_container_width=True)
            
            # Display monthly KPI metrics
            st.markdown("#### Monthly Humidity Summary")
            
            # Create a dataframe for monthly metrics
            kpi_data = monthly_stats[["month_name", "rh_min", "rh_max", "rh_avg"]].copy()
            kpi_data.columns = ["Month", "Min (%)", "Max (%)", "Avg (%)"]
            
            # Display as a nice table
            st.dataframe(
                kpi_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Min (%)": st.column_config.NumberColumn(format="%.1f"),
                    "Max (%)": st.column_config.NumberColumn(format="%.1f"),
                    "Avg (%)": st.column_config.NumberColumn(format="%.1f"),
                }
            )
        
        else:
            st.info("Monthly trend analysis is not yet implemented for Sun Path.")

    # === TAB 3: DIURNAL PROFILE ===
    elif st.session_state.active_tab == "Diurnal Profile":
        import plotly.graph_objects as go
        
        if selected_parameter == "Temperature":
            # Create hourly averages for each month
            hourly_stats = df.groupby(["month", "hour"]).agg({
                "dry_bulb_temperature": ["min", "max", "mean"],
            }).reset_index()
            
            hourly_stats.columns = ["month", "hour", "temp_min", "temp_max", "temp_avg"]
            hourly_stats["month_name"] = hourly_stats["month"].apply(lambda x: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                                                                                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][x-1])
            
            fig_diurnal = go.Figure()
            
            # Get selected month or show average for selected period
            selected_months = list(range(st.session_state.start_month_idx + 1, st.session_state.end_month_idx + 2))
            filtered_hourly = hourly_stats[hourly_stats["month"].isin(selected_months)]
            
            if not filtered_hourly.empty:
                # Get average across selected months
                avg_hourly = filtered_hourly.groupby("hour").agg({
                    "temp_min": "min",
                    "temp_max": "max",
                    "temp_avg": "mean"
                }).reset_index()
                
                # Add temperature range
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly["hour"],
                    y=avg_hourly["temp_max"],
                    fill=None,
                    mode="lines",
                    line_color="rgba(255, 0, 0, 0)",
                    showlegend=False,
                    hoverinfo="skip",
                ))
                
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly["hour"],
                    y=avg_hourly["temp_min"],
                    fill="tonexty",
                    mode="lines",
                    line_color="rgba(255, 0, 0, 0)",
                    name="Daily Range",
                    fillcolor="rgba(255, 173, 173, 0.3)",
                    customdata=avg_hourly["temp_max"],
                    hovertemplate="<b>Hour %{x}:00</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>",
                ))
                
                # Add average
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly["hour"],
                    y=avg_hourly["temp_avg"],
                    mode="lines+markers",
                    name="Average Temperature",
                    line=dict(color="#d32f2f", width=2),
                    marker=dict(size=6),
                    hovertemplate="<b>Hour %{x}:00</b><br>Avg: %{y:.2f}°C<extra></extra>",
                ))
                
                fig_diurnal.update_layout(
                    title="Diurnal Temperature Profile",
                    xaxis_title="Hour of Day",
                    yaxis_title="Temperature (°C)",
                    hovermode="x unified",
                    showlegend=True,
                    template="plotly_white",
                    height=450,
                )
                
                st.plotly_chart(fig_diurnal, use_container_width=True)
            else:
                st.info("No data available for the selected period.")
        
        elif selected_parameter == "Humidity":
            # Create hourly humidity averages for each month
            hourly_humidity = df.groupby(["month", "hour"]).agg({
                "relative_humidity": ["min", "max", "mean"],
            }).reset_index()
            
            hourly_humidity.columns = ["month", "hour", "rh_min", "rh_max", "rh_avg"]
            
            fig_diurnal = go.Figure()
            
            # Get selected month or show average for selected period
            selected_months = list(range(st.session_state.start_month_idx + 1, st.session_state.end_month_idx + 2))
            filtered_hourly_rh = hourly_humidity[hourly_humidity["month"].isin(selected_months)]
            
            if not filtered_hourly_rh.empty:
                # Get average across selected months
                avg_hourly_rh = filtered_hourly_rh.groupby("hour").agg({
                    "rh_min": "min",
                    "rh_max": "max",
                    "rh_avg": "mean"
                }).reset_index()
                
                # Add humidity comfort band
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly_rh["hour"],
                    y=[65] * len(avg_hourly_rh),
                    fill=None,
                    mode="lines",
                    line_color="rgba(128, 128, 128, 0)",
                    showlegend=False,
                    hoverinfo="skip",
                ))
                
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly_rh["hour"],
                    y=[30] * len(avg_hourly_rh),
                    fill="tonexty",
                    mode="lines",
                    line_color="rgba(128, 128, 128, 0)",
                    name="Comfort band (30-65%)",
                    fillcolor="rgba(128, 128, 128, 0.2)",
                    hoverinfo="skip",
                ))
                
                # Add humidity range
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly_rh["hour"],
                    y=avg_hourly_rh["rh_max"],
                    fill=None,
                    mode="lines",
                    line_color="rgba(0, 0, 255, 0)",
                    showlegend=False,
                    hoverinfo="skip",
                ))
                
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly_rh["hour"],
                    y=avg_hourly_rh["rh_min"],
                    fill="tonexty",
                    mode="lines",
                    line_color="rgba(0, 0, 255, 0)",
                    name="Humidity Range",
                    fillcolor="rgba(0, 150, 255, 0.3)",
                    hovertemplate="<b>Hour %{x}:00</b><br>Min: %{y:.1f}%<extra></extra>",
                ))
                
                # Add average
                fig_diurnal.add_trace(go.Scatter(
                    x=avg_hourly_rh["hour"],
                    y=avg_hourly_rh["rh_avg"],
                    mode="lines+markers",
                    name="Average Humidity",
                    line=dict(color="#00a8ff", width=2),
                    marker=dict(size=6),
                    hovertemplate="<b>Hour %{x}:00</b><br>Avg: %{y:.1f}%<extra></extra>",
                ))
                
                fig_diurnal.update_layout(
                    title="Diurnal Humidity Profile",
                    xaxis_title="Hour of Day",
                    yaxis_title="Relative Humidity (%)",
                    hovermode="x unified",
                    showlegend=True,
                    template="plotly_white",
                    height=450,
                )
                
                st.plotly_chart(fig_diurnal, use_container_width=True)
            else:
                st.info("No data available for the selected period.")
        
        else:
            st.info("Sun Path analysis is not yet implemented.")
    
    # === TAB 4: COMFORT ANALYSIS ===
    elif st.session_state.active_tab == "Comfort Analysis":
        if selected_parameter == "Temperature":
            import plotly.graph_objects as go
            
            # Create comfort analysis visualization
            fig_comfort = go.Figure()
            
            # Add comfort bands
            fig_comfort.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_90_upper"],
                fill=None,
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_comfort.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["comfort_90_lower"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(128, 128, 128, 0)",
                name="ASHRAE 90% acceptability",
                fillcolor="rgba(76, 175, 80, 0.4)",
                hoverinfo="skip",
            ))
            
            # Add temperature data
            fig_comfort.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_max"],
                fill=None,
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                showlegend=False,
                hoverinfo="skip",
            ))
            
            fig_comfort.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_min"],
                fill="tonexty",
                mode="lines",
                line_color="rgba(255, 0, 0, 0)",
                name="Daily Temperature Range",
                fillcolor="rgba(255, 173, 173, 0.3)",
                customdata=daily_stats["temp_max"],
                hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<br>Max: %{customdata:.2f}°C<extra></extra>",
            ))
            
            # Add average
            fig_comfort.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_avg"],
                mode="lines",
                name="Average Temperature",
                line=dict(color="#d32f2f", width=2),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>",
            ))
            
            fig_comfort.update_layout(
                title="Comfort Analysis – ASHRAE Adaptive Comfort",
                xaxis_title="Day",
                yaxis_title="Temperature (°C)",
                hovermode="x unified",
                showlegend=True,
                template="plotly_white",
                height=450,
            )
            
            st.plotly_chart(fig_comfort, use_container_width=True)
        else:
            st.info("Comfort Analysis is only available for Temperature parameter.")
    
    # === TAB 5: ENERGY METRICS ===
    elif st.session_state.active_tab == "Energy Metrics":
        if selected_parameter == "Temperature":
            # Calculate energy metrics
            filtered_df = df[
                (df["datetime"].dt.date >= start_date) &
                (df["datetime"].dt.date <= end_date) &
                (df["hour"].between(start_hour, end_hour))
            ]
            
            if not filtered_df.empty:
                # HDD18 (Heating Degree Days at 18°C base)
                hdd18 = (18 - df["dry_bulb_temperature"]).clip(lower=0).sum()
                
                # CDD24 (Cooling Degree Days at 24°C base)
                cdd24 = (df["dry_bulb_temperature"] - 24).clip(lower=0).sum()
                
                # Additional energy metrics
                hdd18_filtered = (18 - filtered_df["dry_bulb_temperature"]).clip(lower=0).sum()
                cdd24_filtered = (df["dry_bulb_temperature"] - 24).clip(lower=0).sum()
                
                # Degree days by month
                monthly_hdd = df.groupby("month").apply(lambda x: (18 - x["dry_bulb_temperature"]).clip(lower=0).sum())
                monthly_cdd = df.groupby("month").apply(lambda x: (x["dry_bulb_temperature"] - 24).clip(lower=0).sum())
                
                month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                
                # Display metrics in cards
                st.markdown("#### Energy Performance Indicators")
                
                energy_col1, energy_col2, energy_col3, energy_col4 = st.columns(4)
                
                with energy_col1:
                    st.metric("HDD18 (Annual)", f"{hdd18:.0f}", "Heating Degree-Days")
                
                with energy_col2:
                    st.metric("CDD24 (Annual)", f"{cdd24:.0f}", "Cooling Degree-Days")
                
                with energy_col3:
                    st.metric("HDD18 (Period)", f"{hdd18_filtered:.0f}", "Heating Degree-Days")
                
                with energy_col4:
                    st.metric("CDD24 (Period)", f"{cdd24_filtered:.0f}", "Cooling Degree-Days")
                
                # Monthly breakdown chart
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots
                
                fig_energy = make_subplots(specs=[[{"secondary_y": True}]])
                
                fig_energy.add_trace(
                    go.Bar(x=month_names, y=monthly_hdd.values, name="HDD18", marker_color="#2196F3"),
                    secondary_y=False,
                )
                
                fig_energy.add_trace(
                    go.Bar(x=month_names, y=monthly_cdd.values, name="CDD24", marker_color="#FF9800"),
                    secondary_y=False,
                )
                
                fig_energy.update_layout(
                    title="Monthly Degree-Days Distribution",
                    xaxis_title="Month",
                    yaxis_title="Degree-Days",
                    hovermode="x unified",
                    height=400,
                    barmode="group",
                )
                
                st.plotly_chart(fig_energy, use_container_width=True)
        else:
            st.info("Energy Metrics is only available for Temperature parameter.")

# Adding extra space at the bottom
st.markdown("<br><br>", unsafe_allow_html=True)