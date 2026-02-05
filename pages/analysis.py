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
        # If images folder is not in current directory, try parent directory
        try:
            with open(f"../{image_path}", "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()
        except:
            return ""

logo_base64 = get_base64_image("images/EDSlogo.jpg")

# STEP 1: Create header using st.columns for proper layout
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
# home icon
with col_button:
    if st.button("← Back to Home", key="analysis_nav"):
        st.switch_page("app.py")

# STEP 2: Add CSS to style everything
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
        padding-bottom: 20px;
        margin-bottom: 30px;
        background-color: white;
    }
    
    /* Style the Analysis button in the header */
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
        width: 170px ;
        
    }
    
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:hover {
        background-color: #8a4a35 !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }
    
    div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:active {
        transform: translateY(0px) !important;
    }
    
    /* Logo hover effect */
    div[data-testid="stHorizontalBlock"]:first-of-type img:hover {
        transform: scale(1.05);
        opacity: 0.85;
        transition: all 0.3s ease;
    }
    
    /* Rest of your existing styles */
    .description {
        text-align: center;
        color: #666;
        font-size: 16px;
        line-height: 1.6;
        margin-bottom: 20px;
        padding: 0 20px;
    }
    .label-text {
        font-size: 18px;
        color: #555;
        font-weight: 500;
        margin-bottom: 5px;
    }
    .section-card {
        background: #ffffff;
        padding: 30px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .section-title {
        font-size: 24px;
        font-weight: bold;
        color: #a85c42;
        margin-bottom: 20px;
        border-bottom: 3px solid #3b82f6;
        padding-bottom: 10px;
        display: inline-block;
    }
    .report-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 30px;
        border-radius: 15px;
        color: white;
        margin-top: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }
    .report-title {
        font-size: 28px;
        font-weight: bold;
        margin-bottom: 20px;
        text-align: center;
    }
    .report-item {
        background: rgba(255,255,255,0.1);
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 15px;
        backdrop-filter: blur(10px);
        box-shadow: 0 4px 15px rgba(0,0,0,0.5);

    }
    .report-label {
        font-size: 14px;
        opacity: 0.9;
        margin-bottom: 5px;
    }
    .report-value {
        font-size: 20px;
        font-weight: bold;
    }
    
    /* Adjust selectbox width */
    .stSelectbox > div > div {
        max-width: 90% !important;
    }
    
    /* nbc Zone Images Styling */
    .climate-zone-header {
        text-align: left;
        font-size: 36px;
        font-weight: 350;
        color: #a85c42;
        margin: 40px 0 30px 0;
        letter-spacing: 2px;
        text-family: sans-serif;
    }
    
    .nbc-images-container {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 20px;
        margin: 30px 0;
        padding: 20px;
        background: #ffffff;
        border-radius: 10px;
    }
    
    .nbc-image-column {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
    }
    
    .nbc-image-title {
        font-size: 22px;
        font-weight: 650;
        color: #333;
        margin-bottom: 15px;
        text-align: center;
    }
    
    .nbc-image-wrapper {
        width: 100%;
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 200px;
    }
    
    .nbc-image-wrapper img {
        max-height: 200px !important;
        width: auto !important;
        object-fit: contain !important;
        display: block !important;
    }
    
    .nbc-image-description {
        font-size: 16px;
        color: #666;
        text-align: center;
        margin-top: 15px;
        line-height: 1.5;
        padding: 0 10px;
        text-align: justify;
    }
    </style>
""", unsafe_allow_html=True)

# CSS for Header
st.markdown(
    f"""
    <style>
        /* Remove default padding */
        .block-container {{
            padding-top: 1.7rem !important;
        }}
        /* Hide the entire sidebar */
        section[data-testid="stSidebar"] {{
            display: none !important;
        }}
        
        /* Remove the sidebar toggle button */
        button[kind="header"] {{
            display: none !important;
        }}
    
    /* Expand main content to full width since sidebar is hidden */
    .main .block-container {{
        max-width: 100% !important;
        padding-left: 3rem !important;
        padding-right: 3rem !important;
    }}
        .app-header {{
            display: grid;
            grid-template-columns: auto 1fr auto;
            align-items: center;
            padding: 5px 0px;
            border-bottom: 1px solid #e6e6e6;
            background-color: white;
            box-shadow: 0 1px 4px rgba(0,0,0,0);
            color: #a85c42;
            font-weight: bold;
        }}

        .app-header img {{
            height: 80px;
        }}
        
        .app-header img:hover {{
            transform: scale(1.1);
            opacity: 0.85;
        }}

        .header-title {{
            text-align: center;
            font-size: 30px;
            font-weight: bold;
            color: #a85c42;
            margin: 0;
        }}
    </style>


    """,
    unsafe_allow_html=True
)

# Back button styling
st.markdown("""
    <style>
    div[data-testid="stHorizontalBlock"] > div:last-child {{
        position: absolute;
        top: 25px;
        right: 30px;
    }}
    
    .stButton > button {{
        background-color: #a85c42 !important;
        color: white !important;
        border: none !important;
        border-radius: 5px !important;
        padding: 10px 25px !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        transition: all 0.3s ease !important;
    }}
    
    .stButton > button:hover {{
        background-color: #8a4a35 !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }}
    </style>
""", unsafe_allow_html=True)



# CSS for styling
st.markdown("""
    <style>
    .block-container {
        padding-top: 2.4rem !important;
        padding-bottom: 0rem !important;
        padding-left: 3rem !important;
        padding-right: 3rem !important;
    }
    
    .coming-soon-container {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 60vh;
        text-align: center;
    }
    
    .coming-soon-title {
        font-size: 72px;
        font-weight: bold;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 20px;
        animation: fadeIn 1s ease-in;
    }
    
    .coming-soon-subtitle {
        font-size: 28px;
        color: #666;
        margin-bottom: 30px;
        animation: fadeIn 1.5s ease-in;
    }
    
    .coming-soon-message {
        font-size: 18px;
        color: #888;
        max-width: 600px;
        line-height: 1.6;
        animation: fadeIn 2s ease-in;
    }
    
    @keyframes fadeIn {
        from {
            opacity: 0;
            transform: translateY(20px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    .feature-preview {
        margin-top: 50px;
        padding: 30px;
        background: #f8f9fa;
        border-radius: 10px;
        animation: fadeIn 2.5s ease-in;
    }
    
    .feature-title {
        font-size: 24px;
        font-weight: bold;
        color: #a85c42;
        margin-bottom: 20px;
    }
    
    .feature-list {
        text-align: left;
        font-size: 16px;
        color: #555;
        line-height: 2;
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







col1, col2 =  st.columns([1,4])
with col1:
    st.header("Data Source")
    uploaded = st.file_uploader("Upload an EPW file", type=["epw"], help="Select an .epw weather file")
    if uploaded is None:
        st.info("Please upload an .epw file to analyze.")
        st.warning("No EPW file uploaded — upload an .epw file in the sidebar to proceed.")
        st.stop()

    try:
        raw = uploaded.getvalue().decode("utf-8", errors="replace")
        df = parse_epw(raw)
        # compute derived date fields used by the charts
        df["doy"] = df["datetime"].dt.dayofyear
        df["day"] = df["datetime"].dt.day
        df["month"] = df["datetime"].dt.month
        df["month_name"] = df["datetime"].dt.strftime("%b")
        st.success("EPW parsed successfully")
    except Exception as e:
        st.error(f"Failed to parse EPW: {e}")
        st.stop()

    def calculate_ashrae_comfort(df: pd.DataFrame) -> tuple:
        """
        Calculate ASHRAE adaptive comfort bands.
        Returns (comfort_80_lower, comfort_80_upper, comfort_90_lower, comfort_90_upper)
        as daily rolling averages.
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
        daily_stats["doy"].astype(str) + f"-{year}",
        format="%j-%Y",
        errors="coerce"
    )

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

    # -----------------------------
    # Variable selector
    # -----------------------------
    variable_map = {
        "Dry Bulb Temperature": "dry_bulb_temperature",
        "Relative Humidity": "relative_humidity",
    }

    selected_label = st.selectbox(
        "Select a variable:",
        list(variable_map.keys()),
    )

    selected_var = variable_map[selected_label]

    # Legend toggle
    col1, col2 = st.columns([0.9, 0.1])
    with col1:
        st.subheader("📈 Yearly Chart")
    with col2:
        show_legend = st.checkbox("Legend", value=True, key="yearly_legend")

    # Build yearly chart for Dry Bulb only (for now)
    if selected_var == "dry_bulb_temperature":
        import plotly.graph_objects as go
        
        fig_yearly = go.Figure()
        
        # Add ASHRAE 80% band
        fig_yearly.add_trace(go.Scatter(
            x=daily_stats["datetime"],
            y=daily_stats["comfort_80_upper"],
            fill=None,
            mode="lines",
            line_color="rgba(128, 128, 128, 0)",
            showlegend=False,
            hoverinfo="skip",
        ))
        fig_yearly.add_trace(go.Scatter(
            x=daily_stats["datetime"],
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
            x=daily_stats["datetime"],
            y=daily_stats["comfort_90_upper"],
            fill=None,
            mode="lines",
            line_color="rgba(128, 128, 128, 0)",
            showlegend=False,
            hoverinfo="skip",
        ))
        fig_yearly.add_trace(go.Scatter(
            x=daily_stats["datetime"],
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
            x=daily_stats["datetime"],
            y=daily_stats["temp_max"],
            fill=None,
            mode="lines",
            line_color="rgba(255, 0, 0, 0)",
            showlegend=False,
            hoverinfo="skip",
        ))
        fig_yearly.add_trace(go.Scatter(
            x=daily_stats["datetime"],
            y=daily_stats["temp_min"],
            fill="tonexty",
            mode="lines",
            line_color="rgba(255, 0, 0, 0)",
            name="Dry bulb temperature Range",
            fillcolor="rgba(255, 0, 0, 0.2)",
        ))
        
        # Add average line
        fig_yearly.add_trace(go.Scatter(
            x=daily_stats["datetime"],
            y=daily_stats["temp_avg"],
            mode="lines",
            name="Average Dry bulb temperature",
            line=dict(color="red", width=2),
        ))
        
        fig_yearly.update_layout(
            title="Yearly Profile – Dry Bulb Temperature",
            xaxis_title="Day",
            yaxis_title="Temperature (°C)",
            hovermode="x unified",
            showlegend=show_legend,
            xaxis_rangeslider_visible=True,
            height=500,
        )
    else:
        # Fallback for RH
        fig_yearly = px.line(
            df,
            x="datetime",
            y=selected_var,
            title=f"Yearly Profile – {selected_label}",
            labels={selected_var: selected_label, "datetime": "Date"},
        )
        fig_yearly.update_layout(xaxis_rangeslider_visible=True, showlegend=show_legend)

    st.plotly_chart(fig_yearly, use_container_width=True)

    # Legend toggle for daily profile
    col1, col2 = st.columns([0.9, 0.1])
    with col1:
        st.subheader("⏰ Daily Profile")
    with col2:
        show_legend_daily = st.checkbox("Legend", value=True, key="daily_legend")

    daily_avg = (
        df.groupby("hour")[selected_var]
        .mean()
        .reset_index()
    )

    fig_daily = px.line(
        daily_avg,
        x="hour",
        y=selected_var,
        markers=True,
        title=f"Average Daily Profile – {selected_label}",
        labels={selected_var: selected_label, "hour": "Hour of Day"},
    )

    fig_daily.update_layout(showlegend=show_legend_daily)
    st.plotly_chart(fig_daily, use_container_width=True)

    # Legend toggle for heatmap
    col1, col2 = st.columns([0.9, 0.1])
    with col1:
        st.subheader("🔥 Heatmap (Hour × Day of Year)")
    with col2:
        show_legend_heatmap = st.checkbox("Legend", value=True, key="heatmap_legend")

    heatmap_df = (
        df.groupby(["doy", "hour"])[selected_var]
        .mean()
        .reset_index()
    )

    fig_heatmap = px.density_heatmap(
        heatmap_df,
        x="doy",
        y="hour",
        z=selected_var,
        color_continuous_scale="Viridis",
        title=f"Heatmap – {selected_label}",
        labels={
            "doy": "Day of Year",
            "hour": "Hour of Day",
            selected_var: selected_label,
        },
    )

    fig_heatmap.update_layout(showlegend=show_legend_heatmap)
    st.plotly_chart(fig_heatmap, use_container_width=True)

    # -----------------------------
    # DESCRIPTIVE STATISTICS
    # -----------------------------
    st.subheader("📊 Descriptive Statistics")

    stats = df[selected_var].describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95])
    st.dataframe(stats.to_frame(name=selected_label))



