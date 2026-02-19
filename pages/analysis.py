from turtle import width
import streamlit as st
import base64
import pandas as pd
import plotly.express as px
import io
import re

st.set_page_config(
    page_title="Analysis - Climate Zone Finder",
    page_icon="📊",
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

logo_base64 = get_base64_image("images/EDSlogo.jpg")

col_logo, col_title, col_button = st.columns([1, 4, 1])

with col_logo:
    st.markdown(
        f'<img src="data:image/png;base64,{logo_base64}" style="height: 80px; margin-top: 30px;">',
        unsafe_allow_html=True
    )

with col_title:
    st.markdown(
        '<h2 style="text-align: center; color: #a85c42; margin-top: 35px;">CLIMATE ZONE FINDER</h2>',
        unsafe_allow_html=True
    )

with col_button:
    if st.button("← Back to Home", key="analysis_nav"):
        st.switch_page("app.py")

st.markdown("""
    <style>
    /* Remove default padding */
    .block-container {
        padding-top: 1.7rem !important;
    }
    
    /* Hide the entire sidebar */
    section[data-testid="stSidebar"] {
        display: none !important;
    }
    
    /* Remove the sidebar toggle button */
    button[kind="header"] {
        display: none !important;
    }
    
    /* Expand main content to full width since sidebar is hidden */
    .main .block-container {
        max-width: 100% !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
    }
    
    /* Style the header columns container */
    div[data-testid="stHorizontalBlock"]:first-of-type {
        border-bottom: 1px solid #e6e6e6;
        padding-bottom: 0px;
        margin-bottom: 10px;
        background-color: white;
    }
    
    /* Style the Back to Home button in the header ONLY */
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton {
        display: flex;
        justify-content: flex-end;
        align-items: center;
        height: 150%;
    }
    
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button {
        background-color: #a85c42 !important;
        color: white !important;
        border: none !important;
        padding: 12px 50px !important;
        font-size: 22px !important;
        font-weight: 500 !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
        margin-top: 20px !important;
        height: 50px !important;
        width: 170px;
    }
    
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:hover {
        background-color: #8a4a35 !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }
    
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:active {
        transform: translateY(0px) !important;
    }
    
    /* Reset other buttons to default Streamlit styling */
    div[data-testid="stHorizontalBlock"]:not(:first-of-type) .stButton > button,
    div[data-testid="stVerticalBlock"] .stButton > button {
        background-color: initial !important;
        color: initial !important;
        padding: initial !important;
        font-size: initial !important;
        font-weight: initial !important;
        height: initial !important;
        width: initial !important;
        margin-top: initial !important;
    }
    
    /* Logo hover effect */
    div[data-testid="stHorizontalBlock"]:first-of-type img:hover {
        transform: scale(1.05);
        opacity: 0.85;
        transition: all 0.3s ease;
    }
    
    /* Section styling */
    .section-title {
        font-size: 24px;
        font-weight: bold;
        color: #a85c42;
        margin-bottom: 20px;
        border-bottom: 3px solid #3b82f6;
        padding-bottom: 10px;
        display: inline-block;
    }
    
    .label-text {
        font-size: 18px;
        color: #555;
        font-weight: 500;
        margin-bottom: 5px;
    }
    
    /* Chart section styling */
    .chart-section {
        background: #ffffff;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        margin-bottom: 20px;
    }
    
    /* Legend checkbox styling */
    .stCheckbox {
        margin-top: 25px;
    }
    
    /* Success/Info/Warning message styling */
    .stAlert {
        border-radius: 8px;
    }
    
    /* Dataframe styling */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
    }
    
    /* Variable selection buttons styling */
    div[data-testid="column"]:has(button[key="btn_temp"]),
    div[data-testid="column"]:has(button[key="btn_humidity"]) {
        padding: 0 2px;
    }
    
    /* Style for variable buttons - make them more prominent */
    button[key="btn_temp"],
    button[key="btn_humidity"] {
        font-size: 15px !important;
        font-weight: 500 !important;
        padding: 10px 16px !important;
        border-radius: 6px !important;
        transition: all 0.2s ease !important;
        height: 45px !important;
        width: 100px !important;
        border: 2px solid transparent !important;
    }
    
    /* Primary (selected) state - white background with colored border */
    button[key="btn_temp"][kind="primary"],
    button[key="btn_humidity"][kind="primary"] {
        background-color: #ffffff !important;
        border: 2px solid #a85c42 !important;
        color: #a85c42 !important;
        box-shadow: 0 2px 6px rgba(168, 92, 66, 0.2) !important;
        font-weight: 600 !important;
    }
    
    button[key="btn_temp"][kind="primary"]:hover,
    button[key="btn_humidity"][kind="primary"]:hover {
        background-color: #fff5f0 !important;
        border: 2px solid #8a4a35 !important;
        color: #8a4a35 !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 3px 8px rgba(168, 92, 66, 0.25) !important;
    }
    
    /* Secondary (unselected) state - light gray */
    button[key="btn_temp"][kind="secondary"],
    button[key="btn_humidity"][kind="secondary"] {
        background-color: #f8f9fa !important;
        border: 2px solid #e9ecef !important;
        color: #6c757d !important;
        width: 150px !important;
    }
    
    button[key="btn_temp"][kind="secondary"]:hover,
    button[key="btn_humidity"][kind="secondary"]:hover {
        background-color: #ffffff !important;
        border: 2px solid #d0d4d9 !important;
        color: #495057 !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08) !important;
        width: 150px !important;
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

col1, col2 = st.columns([1,3.5])

with col1:
    st.markdown('<div class="label-text">Upload an EPW file</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("", type=["epw"], label_visibility="collapsed")

if uploaded is None:
    with col1:
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
    
    with col1:
        st.success("✅ EPW parsed successfully")
        # Time range (hour of use) slider for diurnal/peak analysis
        st.markdown('<div class="label-text">Time range (hour of use)</div>', unsafe_allow_html=True)
        # default to typical daytime hours
        hour_range = st.slider(
            "Select hours (start - end)",
            min_value=0,
            max_value=23,
            value=(8, 18),
            step=1,
            key="hour_range",
            help="Select the building occupied or peak-use hours to analyse diurnal patterns and peak usage",
        )
        
        # Date range selection
        st.markdown('<div class="label-text">Select a date range</div>', unsafe_allow_html=True)
        
        # Get min and max dates from the data
        min_date = df["datetime"].dt.date.min()
        max_date = df["datetime"].dt.date.max()
        
        date_range = st.date_input(
            "Pick a date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="selected_date_range",
            label_visibility="collapsed",
        )
        
        # Handle date range input (can be tuple or single date)
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            # If only one date is selected, use it as both start and end
            start_date = date_range
            end_date = date_range
except Exception as e:
    with col1:
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

with col2:
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
    

    # st.markdown('<div class="label-text">Select Variable</div>', unsafe_allow_html=True)
    
    variable_map = {
        "Dry Bulb Temperature": "dry_bulb_temperature",
        "Relative Humidity": "relative_humidity",
        "Sun Path Diagram": "sunpath",
        "Wind Speed": "wind_speed",
        "Precipitation" : "precipitation"
    }
    
    # Create horizontal button layout with better spacing
    col_btn1, col_btn2, col_btn3, col_btn4, col_btn5 = st.columns([1, 1, 1, 1, 1])

    # Initialize session state for selected variable if not exists
    if 'selected_variable' not in st.session_state:
        st.session_state.selected_variable = "Dry Bulb Temperature"

    # Temporary variable to track if any button was clicked this run
    new_selection = None

    with col_btn1:
        if st.button("Dry Bulb Temperature", 
                    key="btn_temp",
                    use_container_width=True,
                    type="primary" if st.session_state.selected_variable == "Dry Bulb Temperature" else "secondary"):
            new_selection = "Dry Bulb Temperature"

    with col_btn2:
        if st.button("Relative Humidity", 
                    key="btn_humidity",
                    use_container_width=True,
                    type="primary" if st.session_state.selected_variable == "Relative Humidity" else "secondary"):
            new_selection = "Relative Humidity"

    with col_btn3:
        if st.button("Sun Path Diagram", 
                    key="btn_solar",
                    use_container_width=True,
                    type="primary" if st.session_state.selected_variable == "Sun Path Diagram" else "secondary"):
            new_selection = "Sun Path Diagram"

    with col_btn4:
        if st.button("Wind Speed", 
                    key="btn_wind",
                    use_container_width=True,
                    type="primary" if st.session_state.selected_variable == "Wind Speed" else "secondary"):
            new_selection = "Wind Speed"

    with col_btn5:
        if st.button("Precipitation", 
                    key="btn_dewpoint",
                    width='content',
                    type="primary" if st.session_state.selected_variable == "Precipitation" else "secondary"):
            new_selection = "Precipitation"

    # Update session state if a button was clicked
    if new_selection:
        st.session_state.selected_variable = new_selection

    selected_label = st.session_state.selected_variable
    selected_var = variable_map[selected_label]
    # --- Diurnal analysis (hourly averages) ---
    hourly = df.groupby("hour").agg({
        "dry_bulb_temperature": "mean",
        "relative_humidity": "mean",
    }).reset_index()

    # read selected hour range from session state (set by slider in col1)
    start_hour, end_hour = st.session_state.get("hour_range", (8, 18))
    hourly["in_use"] = hourly["hour"].between(start_hour, end_hour)

    # choose numeric column to plot for diurnal profile
    val_col = selected_var

    # small diurnal chart highlighting selected hours
    try:
        color_map = {True: "#a85c42", False: "#d6d6d6"}
        fig_diurnal = px.bar(
            hourly,
            x="hour",
            y=val_col,
            color="in_use",
            color_discrete_map=color_map,
            title=f"Diurnal profile – {selected_label} (hourly avg)",
            labels={val_col: selected_label, "hour": "Hour of day"},
        )
        fig_diurnal.update_layout(xaxis=dict(dtick=1), showlegend=False, height=320)
        st.plotly_chart(fig_diurnal, use_container_width=True)

        # Compute peak within selected hours
        sel_hours = hourly[hourly["in_use"]]
        if not sel_hours.empty:
            peak_idx = sel_hours[val_col].idxmax()
            peak_row = sel_hours.loc[peak_idx]
            peak_hour = int(peak_row["hour"])
            peak_val = float(peak_row[val_col])
            unit = "°C" if val_col == "dry_bulb_temperature" else "%"
            st.markdown(f"**Peak hour (within selected range):** {peak_hour}:00 — {peak_val:.2f} {unit}")
        else:
            st.markdown("**No data in the selected hour range**")
    except Exception:
        # if plotting fails for non-numeric variables, skip diurnal chart
        pass
        # Add some spacing after buttons
        
        # Legend toggle for yearly chart
        # col1_chart, col2_chart = st.columns([0.9, 0.1])
        # with col1_chart:
        #     st.markdown('<div class="section-title">📈 Yearly Chart</div>', unsafe_allow_html=True)
        # with col2_chart:
        # show_legend = st.checkbox("Legend", value=True, key="yearly_legend")
        
        # Build yearly chart for Dry Bulb only (for now)
    if selected_var == "dry_bulb_temperature":
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
                fillcolor="rgba(255, 0, 0, 0.2)",
                hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}°C<extra></extra>",
            ))
            
            # Add average line
        fig_yearly.add_trace(go.Scatter(
                x=daily_stats["datetime_display"],
                y=daily_stats["temp_avg"],
                mode="lines",
                name="Average Dry bulb temperature",
                line=dict(color="red", width=2),
                hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}°C<extra></extra>",
            ))
            
        fig_yearly.update_layout(
                title="Annual Profile – Dry Bulb Temperature",
                xaxis_title="Day",
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
                xaxis_rangeslider_visible=True,
                height=500,
            )

        st.plotly_chart(fig_yearly, use_container_width=True)
        
        # --- Cards showing min/max dry bulb for selected date range + time range ---
        start_date = st.session_state.get("selected_date_range")
        if isinstance(start_date, tuple) and len(start_date) == 2:
            start_date, end_date = start_date
        else:
            # If only one date was selected, use it as both
            end_date = start_date
        
        start_hour, end_hour = st.session_state.get("hour_range", (8, 18))
        
        # Filter data: date range + hour range
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
            min_date = min_row["datetime"].strftime("%b %d, %Y")
            min_hour = int(min_row["hour"])
            
            max_date = max_row["datetime"].strftime("%b %d, %Y")
            max_hour = int(max_row["hour"])
            
            # Display date range and time range summary
            start_str = start_date.strftime("%b %d, %Y") if start_date else "N/A"
            end_str = end_date.strftime("%b %d, %Y") if end_date else "N/A"
            st.markdown(f"**Period: {start_str} to {end_str}, {start_hour:02d}:00–{end_hour:02d}:00**")
            
            # === ROW 1: TEMPERATURE METRICS ===
            st.markdown('<div class="section-title">Temperature</div>', unsafe_allow_html=True)
            temp_col1, temp_col2, temp_col3, temp_col4 = st.columns(4)
            
            with temp_col1:
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        padding: 15px;
                        border-radius: 10px;
                        text-align: center;
                        color: white;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    ">
                        <div style="font-size: 12px; opacity: 0.9;">Min Temp</div>
                        <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{temp_min:.2f}°C</div>
                        <div style="font-size: 11px; opacity: 0.85;">{min_date}</div>
                        <div style="font-size: 11px; opacity: 0.85;">{min_hour:02d}:00</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            
            with temp_col2:
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                        padding: 15px;
                        border-radius: 10px;
                        text-align: center;
                        color: white;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    ">
                        <div style="font-size: 12px; opacity: 0.9;">Max Temp</div>
                        <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{temp_max:.2f}°C</div>
                        <div style="font-size: 11px; opacity: 0.85;">{max_date}</div>
                        <div style="font-size: 11px; opacity: 0.85;">{max_hour:02d}:00</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            
            with temp_col3:
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                        padding: 15px;
                        border-radius: 10px;
                        text-align: center;
                        color: white;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    ">
                        <div style="font-size: 12px; opacity: 0.9;">Avg Temp</div>
                        <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{temp_avg:.2f}°C</div>
                        <div style="font-size: 11px; opacity: 0.85;">Average</div>
                        <div style="font-size: 11px; opacity: 0.85;">Period</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            
            with temp_col4:
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
                        padding: 15px;
                        border-radius: 10px;
                        text-align: center;
                        color: white;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    ">
                        <div style="font-size: 12px; opacity: 0.9;">Diurnal Range</div>
                        <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{diurnal_range:.2f}°C</div>
                        <div style="font-size: 11px; opacity: 0.85;">Max - Min</div>
                        <div style="font-size: 11px; opacity: 0.85;">Amplitude</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            
            # === ROW 2: COMFORT METRICS ===
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div class="section-title">Comfort</div>', unsafe_allow_html=True)
            
            # Calculate comfort metrics
            # Get ASHRAE comfort bands for the filtered data
            ashrae_data = df.merge(
                pd.DataFrame({
                    "doy": ashrae_80_lower.index,
                    "comfort_80_lower": ashrae_80_lower.values,
                    "comfort_80_upper": ashrae_80_upper.values,
                    "comfort_90_lower": ashrae_90_lower.values,
                    "comfort_90_upper": ashrae_90_upper.values,
                }), on="doy", how="left"
            )
            
            # Filter for the same date and time range
            comfort_df_filtered = ashrae_data[
                (ashrae_data["datetime"].dt.date >= start_date) &
                (ashrae_data["datetime"].dt.date <= end_date) &
                (ashrae_data["hour"].between(start_hour, end_hour))
            ]
            
            if not comfort_df_filtered.empty:
                # Total occupied hours
                total_hours = len(comfort_df_filtered)
                
                # Comfort 80% calculation
                comfort_80 = (
                    (comfort_df_filtered["dry_bulb_temperature"] >= comfort_df_filtered["comfort_80_lower"]) &
                    (comfort_df_filtered["dry_bulb_temperature"] <= comfort_df_filtered["comfort_80_upper"])
                ).sum()
                comfort_80_pct = (comfort_80 / total_hours * 100) if total_hours > 0 else 0
                
                # Comfort 90% calculation
                comfort_90 = (
                    (comfort_df_filtered["dry_bulb_temperature"] >= comfort_df_filtered["comfort_90_lower"]) &
                    (comfort_df_filtered["dry_bulb_temperature"] <= comfort_df_filtered["comfort_90_upper"])
                ).sum()
                comfort_90_pct = (comfort_90 / total_hours * 100) if total_hours > 0 else 0
                
                # Overheating hours (above comfort 80% upper limit)
                overheating = (comfort_df_filtered["dry_bulb_temperature"] > comfort_df_filtered["comfort_80_upper"]).sum()
                
                # Underheating hours (below comfort 80% lower limit)
                underheating = (comfort_df_filtered["dry_bulb_temperature"] < comfort_df_filtered["comfort_80_lower"]).sum()
                
                # Display Comfort cards
                comfort_col1, comfort_col2, comfort_col3, comfort_col4 = st.columns(4)
                
                with comfort_col1:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">Comfort (80%)</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{comfort_80_pct:.1f}%</div>
                            <div style="font-size: 11px; opacity: 0.85;">of occupied</div>
                            <div style="font-size: 11px; opacity: 0.85;">hours</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with comfort_col2:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">Comfort (90%)</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{comfort_90_pct:.1f}%</div>
                            <div style="font-size: 11px; opacity: 0.85;">of occupied</div>
                            <div style="font-size: 11px; opacity: 0.85;">hours</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with comfort_col3:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">Overheating Hours</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{overheating}</div>
                            <div style="font-size: 11px; opacity: 0.85;">hours above</div>
                            <div style="font-size: 11px; opacity: 0.85;">comfort limit</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with comfort_col4:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">Cold Hours</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{underheating}</div>
                            <div style="font-size: 11px; opacity: 0.85;">hours below</div>
                            <div style="font-size: 11px; opacity: 0.85;">comfort limit</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                # === ROW 3: ENERGY METRICS ===
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown('<div class="section-title">Energy</div>', unsafe_allow_html=True)
                
                # Calculate energy metrics
                # Get full year daily stats for HDD/CDD
                full_year_daily = df.groupby("doy").agg({
                    "dry_bulb_temperature": "mean"
                }).reset_index()
                full_year_daily.columns = ["doy", "temp_avg"]
                
                # HDD18 - Heating Degree Days (base 18°C)
                hdd18 = (18 - full_year_daily["temp_avg"]).clip(lower=0).sum()
                
                # CDD24 - Cooling Degree Days (base 24°C)
                cdd24 = (full_year_daily["temp_avg"] - 24).clip(lower=0).sum()
                
                # 1% Cooling Temp (99th percentile)
                cooling_temp_1pct = filtered_df["dry_bulb_temperature"].quantile(0.99)
                
                # 99% Heating Temp (1st percentile)
                heating_temp_99pct = filtered_df["dry_bulb_temperature"].quantile(0.01)
                
                energy_col1, energy_col2, energy_col3, energy_col4 = st.columns(4)
                
                with energy_col1:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">HDD18</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{hdd18:.0f}</div>
                            <div style="font-size: 11px; opacity: 0.85;">Heating</div>
                            <div style="font-size: 11px; opacity: 0.85;">Degree Days</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with energy_col2:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">CDD24</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{cdd24:.0f}</div>
                            <div style="font-size: 11px; opacity: 0.85;">Cooling</div>
                            <div style="font-size: 11px; opacity: 0.85;">Degree Days</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with energy_col3:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">1% Cooling Temp</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{cooling_temp_1pct:.2f}°C</div>
                            <div style="font-size: 11px; opacity: 0.85;">99th percentile</div>
                            <div style="font-size: 11px; opacity: 0.85;">temperature</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                
                with energy_col4:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                            padding: 15px;
                            border-radius: 10px;
                            text-align: center;
                            color: white;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        ">
                            <div style="font-size: 12px; opacity: 0.9;">99% Heating Temp</div>
                            <div style="font-size: 28px; font-weight: bold; margin: 10px 0;">{heating_temp_99pct:.2f}°C</div>
                            <div style="font-size: 11px; opacity: 0.85;">1st percentile</div>
                            <div style="font-size: 11px; opacity: 0.85;">temperature</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
        else:
            st.info(f"No data available for {selected_date} between {start_hour:02d}:00 and {end_hour:02d}:00")
    elif selected_var == "sunpath":
        st.write("Sun Path analysis is not yet implemented.")

    elif selected_var == "relative_humidity":
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
            xaxis_rangeslider_visible=True,
            height=500,
        )
        
        st.plotly_chart(fig_yearly, use_container_width=True)

# Adding extra space at the bottom
st.markdown("<br><br>", unsafe_allow_html=True)