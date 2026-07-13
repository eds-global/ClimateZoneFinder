import streamlit as st
import pandas as pd
import json
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
import io
from datetime import datetime
from PIL import Image as PILImage
import base64

from pages.modules import analytics

st.set_page_config(
    page_title="Climate Zone Finder",
    page_icon="🌍",
    layout="wide"
)

analytics.require_login()
analytics.log_page_view("Home")


# Access URL query parameters
def parse_location_parameter():
    """Parse location parameter from URL query string.
    
    Expected format: "Location, State/Province, Country" or "Location, Country"
    Returns: dict with 'location', 'state', and 'country' keys
    """
    query_params = st.query_params
    location_param = query_params.get("location", None)
    
    if not location_param:
        return {"location": None, "state": None, "country": None}
    
    # Split by comma and strip whitespace
    parts = [p.strip() for p in location_param.split(",")]
    
    if len(parts) == 3:
        # Format: Location, State, Country
        return {"location": parts[0], "state": parts[1], "country": parts[2]}
    elif len(parts) == 2:
        # Format: Location, Country
        return {"location": parts[0], "state": None, "country": parts[1]}
    else:
        # Just location name
        return {"location": parts[0] if parts else None, "state": None, "country": None}

# Get location components from URL if provided
url_location_params = parse_location_parameter()


# Auto-populate EPW URL in session state when ?location= param is present.
# The analysis page already knows how to fetch + unzip from st.session_state["epw_url"].
if url_location_params.get("location") and "epw_url" not in st.session_state:
    try:
        _nbc = pd.read_excel("INDIA-WeatherMapping.xlsx")
        _loc = url_location_params["location"]
        _state = url_location_params.get("state")
        _mask = _nbc["Location"].str.strip() == _loc.strip()
        if _state:
            _state_mask = _nbc["State"].str.strip() == _state.strip()
            _result = _nbc[_mask & _state_mask]
            if _result.empty:
                _result = _nbc[_mask]
        else:
            _result = _nbc[_mask]
        if not _result.empty:
            _epw_url = _result.iloc[0].get("EPW File", None)
            if _epw_url and isinstance(_epw_url, str) and _epw_url.startswith("http"):
                st.session_state["epw_url"] = _epw_url
                st.session_state["epw_auto_location"] = _loc
    except Exception:
        pass


def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

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

with col_button:
    if st.button("Analysis", key="analysis_nav"):
        st.switch_page("pages/analysis.py")

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
    /* Standardize button appearance so text fits and layout is consistent */
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

    /* Link buttons (download links) look like buttons */
    a.stLinkButton {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 8px 14px !important;
        text-decoration: none !important;
        color: inherit !important;
        background: transparent !important;
        border-radius: 8px !important;
        margin-bottom: 18px !important;
    }
    </style>
""", unsafe_allow_html=True)

# store the dataset in cache to reduce load time
@st.cache_data
def load_ashrae_data():
    df = pd.read_excel("ASHRAE-ClimateZoneMapping.xlsx")
    return df


@st.cache_data
def load_nbc_data():
    df = pd.read_excel("INDIA-WeatherMapping.xlsx")
    return df


# Climate zone data for NBC
CLIMATE_ZONE_DATA = {
    "Cold": {
        "images": [
            "images/climate_zone_finder.png",
            "images/sun_space.png",
            "images/trombe_wall.png"
        ],
        "titles": ["Surface area to volume ratio", "Sun Space", "Trombe Wall"],
        "descriptions": [
            "In cold regions, building's shape needs to be compact to reduce heat gain and losses, respectively. The surface to volume(S/V) ratio of the building should be as low as possible to minimize heat loss.",
            "The south facing sun space to catch maximum heat inside. The trapped heat keeps the indoor warm in the cold climate.",
            "The hot air between the glazing and the wall gets heated up and enters inside to store sensible heat."
        ]
    },
    "Composite": {
        "images": [
            "images/shading_windows.png",
            "images/Cool_Roof.png",
            "images/Light_shelf.png"
        ],
        "titles": ["Shading", "Cool Roof", "Light Shelf"],
        "descriptions": [
            "Extended roof, horizontal overhangs over the windows are effective in shading. These devices are designed to block the summer sun but allowing the winter sun.",
            "Cool roofs reflect most of the solar radiation and efficiently emit some of the absorbed radiation back into the atmosphere, instead of conducting it to the building below.",
            "The external light shelves to penetrate diffused light inside the space. They serve the dual purpose by acting as a shading device."
        ]
    },
    "Hot-Dry": {
        "images": [
            "images/climate_zone_finder.png",
            "images/Evaporative_Cooling.png",
            "images/Cool_Roof.png"
        ],
        "titles": ["Surface area to volume ratio", "Evaporative Cooling", "Cool Roof"],
        "descriptions": [
            "In hot & dry regions, building's shape needs to be compact to reduce heat gain and losses, respectively. The surface to volume(S/V) ratio of the building should be as low as possible to minimize heat gain.",
            "Evaporative cooling is mostly effective in hot and dry climate where the humidity is low. Water in pools and fountains can be used as a cooling element along with cross-ventilating arrangement of openings.",
            "Cool roofs reflect most of the solar radiation and efficiently emit some of the absorbed radiation back into the atmosphere, instead of conducting it to the building below."
        ]
    },
    "Temperate": {
        "images": [
            "images/natural_ventilation.png",
            "images/Shaded_verandahs.png",
            "images/orientation.png"
        ],
        "titles": ["Natural ventilation", "Shaded Verandahs", "Orientation"],
        "descriptions": [
            "Naturally ventilated buildings rely on wind that is naturally prevalent at the site. The fenestrations of the building should be designed to capture the breeze for effective ventilation.",
            "Extended roof, horizontal overhangs over the windows are effective in shading. These devices can be designed to be fixed or moveable, so you can adjust them according.",
            "By orienting the shorter sides of the building in the direction of strongest solar radiation, the thermal impact from solar radiation is minimised."
        ]
    },
    "Warm-Humid": {
        "images": [
            "images/Siting_Prevailing_wind.png",
            "images/Shaded_verandahs.png",
            "images/natural_ventilation.png"
        ],
        "titles": ["Siting- Design for prevalent wind patterns", "Shaded Verandahs", "Natural ventilation"],
        "descriptions": [
            "In warm and humid climates, buildings are placed on site to catch maximum wind.\n The plantations help channelize and filter the wind.",
            "Extended roof, horizontal overhangs over the windows are effective in shading. These devices can be designed to be fixed or moveable, so you can adjust them according.",
            "In humid climates such as that prevailing in Coastal regions, ventilation can bring in much needed relief. Naturally ventilated buildings rely on wind that is naturally prevalent at the site."
        ]
    }
}

# Manual color mapping for aligning with climate zone nams
def get_ashrae_zone_color(df, climate_zone):
    """Get color for ASHRAE climate zone"""
    df["Climate Zone"] = df["Climate Zone"].astype(str).str.strip()
    climate_zone = str(climate_zone).strip()
    
    zone_list = sorted(df["Climate Zone"].unique())
    
    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#005f99", "#cc5500", "#009933", "#990000", "#663399",
        "#00b3b3", "#b30047", "#ff66b2", "#66ff66", "#ffd966"
    ]
    
    while len(palette) < len(zone_list):
        palette = palette + palette
    
    zone_color_map = {z: palette[i] for i, z in enumerate(zone_list)}
    return zone_color_map.get(climate_zone, "#444444")


def get_nbc_zone_color(climate_zone):
    """Get color for NBC climate zone"""
    climate_zone = str(climate_zone).strip()
    
    nbc_colors = {
        "Cold": "#02a0c5",
        "Composite": "#dec45e",
        "Hot-Dry": "#c60102",
        "Temperate": "#f89cc9",
        "Warm-Humid": "#e59704",
    }
    
    return nbc_colors.get(climate_zone, "#444444")


def amcharts_world_globe(df, lat_sel, lon_sel, location_name, country_name, climate_zone, climate_zone_name):
    """Globe visualization for ASHRAE (World)"""
    # Normalize zone values
    df["Climate Zone"] = df["Climate Zone"].astype(str).str.strip()
    climate_zone = str(climate_zone).strip()

    # Generate color map and zone name mapping
    zone_list = sorted(df["Climate Zone"].unique())
    
    zone_name_map = {}
    for zone in zone_list:
        zone_data = df[df["Climate Zone"] == zone].iloc[0]
        zone_name_map[zone] = zone_data["Climate Zone Name"]

    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#005f99", "#cc5500", "#009933", "#990000", "#663399",
        "#00b3b3", "#b30047", "#ff66b2", "#66ff66", "#ffd966"
    ]

    while len(palette) < len(zone_list):
        palette = palette + palette

    zone_color_map = {z: palette[i] for i, z in enumerate(zone_list)}
    default_color = "#444444"

    # Build JS dataset
    df_js = json.dumps([
        {
            "lat": float(row["Latitude"]),
            "lon": float(row["Longitude"]),
            "title": row["Location"],
            "country": row["Country"],
            "zone": row["Climate Zone"],
            "zone_name": row["Climate Zone Name"],
            "color": zone_color_map.get(row["Climate Zone"], default_color)
        }
        for _, row in df.iterrows()
    ])

    selected_js = json.dumps({
        "lat": float(lat_sel),
        "lon": float(lon_sel),
        "title": location_name,
        "zone": climate_zone,
        "zone_name": climate_zone_name,
        "color": "#ff0000"
    })
    
    rotation_x = -float(lon_sel)
    rotation_y = -float(lat_sel)

    # CSS for globe and legend
    html_code = f"""
    <style>
        #container {{
            display: flex;
            flex-direction: row;
            height: 100%;
            gap: 20px;
        }}
        #chartdiv {{
            flex: 1;
            height: 700px;
            min-height: 700px;
        }}
        #legend {{
            width: 200px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
            border: 1px solid #dee2e6;
            max-height: 600px;
            overflow-y: auto;
        }}
      
        #legend::-webkit-scrollbar {{
            width: 5px;
        }}



        #legend h4 {{
            margin: 0 0 15px 0;
            font-size: 18px;
            color: #333;
            font-weight: bold;
            text-align: center;
        }}

        .legend-grid {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            font-size: 13px;
            padding: 8px 10px;
            background: white;
            border-radius: 5px;
            transition: all 0.2s;
            gap: 10px;
            font-family: sans-serif;
        }}
        .legend-item:hover {{
            transform: translateX(5px);
            background: #fff5f0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
            border: 1px solid #ccc;
            flex-shrink: 0;
        }}
        .legend-text {{
            font-size: 12px;
            color: #333;
            line-height: 1.3;
            
        }}
    </style>

    <script src="https://cdn.amcharts.com/lib/5/index.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/map.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/geodata/worldLow.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>

    <div id="container">
        <div id="chartdiv"></div>
        <div id="legend">
            <h4>Climate Zone</h4>
            <div class="legend-grid">
                {''.join([
                    f'<div class="legend-item">'
                    f'<div class="legend-color" style="background:{zone_color_map.get(z, default_color)};"></div>'
                    f'<span class="legend-text">{z} - {zone_name_map.get(z, "")}</span>'
                    f'</div>'
                    for z in zone_list
                ])}
            </div>
        </div>
    </div>

    <script>
    (function() {{
        if (window.globeChart && window.globeRoot) {{
            var selectedData = [{selected_js}];
            window.selectedSeries.data.setAll(selectedData);
            
            window.globeChart.animate({{
                key: "rotationX",
                to: {rotation_x},
                duration: 1500,
                easing: am5.ease.inOut(am5.ease.cubic)
            }});

            window.globeChart.animate({{
                key: "rotationY",
                to: {rotation_y},
                duration: 1500,
                easing: am5.ease.inOut(am5.ease.cubic)
            }});
        }} else {{
            am5.ready(function() {{
                var root = am5.Root.new("chartdiv");
                root.setThemes([ am5themes_Animated.new(root) ]);

                var chart = root.container.children.push(
                    am5map.MapChart.new(root, {{
                        projection: am5map.geoOrthographic(),
                        panX: "rotateX",
                        panY: "rotateY"
                    }})
                );

                var polygonSeries = chart.series.push(
                    am5map.MapPolygonSeries.new(root, {{
                        geoJSON: am5geodata_worldLow
                    }})
                );

                polygonSeries.mapPolygons.template.setAll({{
                    fill: am5.color("#f5f5dc"),
                    stroke: am5.color("#8b7355"),
                    strokeWidth: 0.5
                }});

                var pointSeries = chart.series.push(am5map.MapPointSeries.new(root, {{
                    latitudeField: "lat",
                    longitudeField: "lon"
                }}));

                pointSeries.bullets.push(function(root, series, dataItem) {{
                    return am5.Bullet.new(root, {{
                        sprite: am5.Circle.new(root, {{
                            radius: 5,
                            fill: am5.color(dataItem.dataContext.color),
                            stroke: am5.color("#ffffff"),
                            strokeWidth: 1.3,
                            tooltipText:
                                "[bold]{{title}}[/]\\n" +
                                "{{country}}\\n" +
                                "Zone: {{zone}}\\n" +
                                "{{zone_name}}"
                        }})
                    }});
                }});

                pointSeries.data.setAll({df_js});

                var selectedSeries = chart.series.push(am5map.MapPointSeries.new(root, {{
                    latitudeField: "lat",
                    longitudeField: "lon"
                }}));

                selectedSeries.bullets.push(function(root, series, dataItem) {{
                    return am5.Bullet.new(root, {{
                        sprite: am5.Circle.new(root, {{
                            radius: 10,
                            fill: am5.color("#ff0000"),
                            stroke: am5.color("#ffffff"),
                            strokeWidth: 2,
                            tooltipText:
                                "[bold]{{title}}[/] (Selected)\\n" +
                                "Zone: {{zone}}\\n" +
                                "{{zone_name}}"
                        }})
                    }});
                }});

                selectedSeries.data.setAll([{selected_js}]);

                window.globeRoot = root;
                window.globeChart = chart;
                window.selectedSeries = selectedSeries;

                chart.animate({{
                    key: "rotationX",
                    to: {rotation_x},
                    duration: 2000,
                    easing: am5.ease.inOut(am5.ease.cubic)
                }});

                chart.animate({{
                    key: "rotationY",
                    to: {rotation_y},
                    duration: 2000,
                    easing: am5.ease.inOut(am5.ease.cubic)
                }});
            }});
        }}
    }})();
    </script>
    """

    st.components.v1.html(html_code, height=730, scrolling=False)


def amcharts_india_map(df, lat_sel, lon_sel, location_name, state_name, climate_zone):
    """India Map visualization for NBC"""
    # Normalize zone values
    df["Climate Zone"] = df["Climate Zone"].astype(str).str.strip()
    climate_zone = str(climate_zone).strip()

    # Get unique climate zones for nbc
    zone_list = sorted(df["Climate Zone"].unique())
    
    # nbc Climate Zone Colors
    nbc_colors = {
        "Cold": "#02a0c5",
        "Composite": "#dec45e",
        "Hot-Dry": "#c60102",
        "Temperate": "#f89cc9",
        "Warm-Humid": "#e59704",
    }

    zone_color_map = {z: nbc_colors.get(z, "#444444") for z in zone_list}
    default_color = "#444444"

    # Filter out rows with missing coordinates
    df_valid = df.dropna(subset=['Latitude', 'Longitude'])
    
    # Build JS dataset
    df_js = json.dumps([
        {
            "lat": float(row["Latitude"]),
            "lon": float(row["Longitude"]),
            "title": row["Location"],
            "state": row["State"],
            "zone": row["Climate Zone"],
            "color": zone_color_map.get(row["Climate Zone"], default_color)
        }
        for _, row in df_valid.iterrows()
    ])

    selected_js = json.dumps({
        "lat": float(lat_sel),
        "lon": float(lon_sel),
        "title": location_name,
        "state": state_name,
        "zone": climate_zone,
        "color": "#ff0000"
    })

    # CSS for India map and legend
    html_code = f"""
    <style>
        #container {{
            display: flex;
            flex-direction: row;
            height: 100%;
            gap: 20px;
        }}
        #chartdiv {{
            flex: 1;
            height: 700px;
            min-height: 700px;
        }}
        #legend-nbc {{
            width: 220px;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 8px;
            border: 1px solid #dee2e6;
            max-height: 250px;
            overflow-y: auto;
        }}
        #legend-nbc::-webkit-scrollbar {{
            width: 5px;
        }}
        #legend-nbc::-webkit-scrollbar-track {{
            background: #f1f1f1;
            border-radius: 10px;
        }}
        #legend-nbc::-webkit-scrollbar-thumb {{
            background: #ff6b35;
            border-radius: 10px;
        }}
        #legend-nbc h4 {{
            margin: 0 0 15px 0;
            font-size: 18px;
            color: #333;
            font-weight: bold;
        }}
        .legend-grid {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            font-size: 13px;
            padding: 8px 10px;
            background: white;
            border-radius: 5px;
            transition: all 0.2s;
            gap: 10px;
            font-family: sans-serif;

        }}
        .legend-item:hover {{
            transform: translateX(5px);
            background: #fff5f0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
            border: 1px solid #ccc;
            flex-shrink: 0;
        }}
        .legend-text {{
            font-size: 12px;
            color: #333;
            line-height: 1.3;
        }}
    </style>

    <script src="https://cdn.amcharts.com/lib/5/index.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/map.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/geodata/indiaLow.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>

    <div id="container">
        <div id="chartdiv"></div>
        <div id="legend-nbc">
            <h4>NBC Climate Zones</h4>
            <div class="legend-grid">
                {''.join([
                    f'<div class="legend-item">'
                    f'<div class="legend-color" style="background:{zone_color_map.get(z, default_color)};"></div>'
                    f'<span class="legend-text">{z}</span>'
                    f'</div>'
                    for z in zone_list
                ])}
            </div>
        </div>
    </div>

    <script>
    (function() {{
        if (window.indiaChart && window.indiaRoot) {{
            var selectedData = [{selected_js}];
            window.indiaSelectedSeries.data.setAll(selectedData);
            
            // Animate to the selected location
            window.indiaChart.animate({{
                key: "rotationX",
                to: 0,
                duration: 1000,
                easing: am5.ease.out(am5.ease.cubic)
            }});
        }} else {{
            am5.ready(function() {{
                var root = am5.Root.new("chartdiv");
                root.setThemes([ am5themes_Animated.new(root) ]);

                var chart = root.container.children.push(
                    am5map.MapChart.new(root, {{
                        projection: am5map.geoMercator(),
                        panX: "translateX",
                        panY: "translateY",
                        wheelY: "zoom"
                    }})
                );

                var polygonSeries = chart.series.push(
                    am5map.MapPolygonSeries.new(root, {{
                        geoJSON: am5geodata_indiaLow
                    }})
                );

                polygonSeries.mapPolygons.template.setAll({{
                    fill: am5.color("#e8f4ea"),
                    stroke: am5.color("#2d5f3f"),
                    strokeWidth: 1.5,
                    tooltipText: "{{name}}"
                }});

                polygonSeries.mapPolygons.template.states.create("hover", {{
                    fill: am5.color("#c7e6cc")
                }});

                var pointSeries = chart.series.push(am5map.MapPointSeries.new(root, {{
                    latitudeField: "lat",
                    longitudeField: "lon"
                }}));

                pointSeries.bullets.push(function(root, series, dataItem) {{
                    var circle = am5.Circle.new(root, {{
                        radius: 7,
                        fill: am5.color(dataItem.dataContext.color),
                        stroke: am5.color("#ffffff"),
                        strokeWidth: 2,
                        tooltipText:
                            "[bold]{{title}}[/]\\n" +
                            "State: {{state}}\\n" +
                            "Climate Zone: {{zone}}"
                    }});
                    
                    circle.states.create("hover", {{
                        scale: 1.3
                    }});
                    
                    return am5.Bullet.new(root, {{
                        sprite: circle
                    }});
                }});

                pointSeries.data.setAll({df_js});

                var selectedSeries = chart.series.push(am5map.MapPointSeries.new(root, {{
                    latitudeField: "lat",
                    longitudeField: "lon"
                }}));

                selectedSeries.bullets.push(function(root, series, dataItem) {{
                    var container = am5.Container.new(root, {{}});
                    
                    // Outer pulse circle
                    var outerCircle = container.children.push(am5.Circle.new(root, {{
                        radius: 20,
                        fill: am5.color("#ff0000"),
                        fillOpacity: 0.3,
                        strokeWidth: 0
                    }}));
                    
                    // Animate pulse
                    outerCircle.animate({{
                        key: "scale",
                        from: 1,
                        to: 1.5,
                        duration: 1000,
                        easing: am5.ease.out(am5.ease.cubic),
                        loops: Infinity
                    }});
                    
                    outerCircle.animate({{
                        key: "opacity",
                        from: 0.5,
                        to: 0,
                        duration: 1000,
                        easing: am5.ease.out(am5.ease.cubic),
                        loops: Infinity
                    }});
                    
                    // Main circle
                    var mainCircle = container.children.push(am5.Circle.new(root, {{
                        radius: 14,
                        fill: am5.color("#ff0000"),
                        stroke: am5.color("#ffffff"),
                        strokeWidth: 3,
                        tooltipText:
                            "[bold]{{title}}[/] (Selected)\\n" +
                            "State: {{state}}\\n" +
                            "Climate Zone: {{zone}}"
                    }}));
                    
                    return am5.Bullet.new(root, {{
                        sprite: container
                    }});
                }});

                selectedSeries.data.setAll([{selected_js}]);

                // Zoom to fit India
                chart.set("zoomLevel", 1);
                chart.set("zoomControl", am5map.ZoomControl.new(root, {{}}));

                window.indiaRoot = root;
                window.indiaChart = chart;
                window.indiaSelectedSeries = selectedSeries;
                window.indiaPointSeries = pointSeries;
            }});
        }}
    }})();
    </script>
    """

    st.components.v1.html(html_code, height=730, scrolling=False)

def add_page_header(canvas, doc):
    """Add header with logo to each page"""
    canvas.saveState()
    
    # Add logo to header
    try:
        logo_path = "images/EDSlogo.jpg"
        # Draw logo in top-left corner
        canvas.drawImage(logo_path, 0.5*inch, letter[1] - 1*inch, 
                        width=1.2*inch, height=0.6*inch, 
                        preserveAspectRatio=True, mask='auto')
    except:
        pass  # If logo not found, continue without it
    
    # Add title text in header
    canvas.setFont('Helvetica-Bold', 16)
    canvas.setFillColor(colors.HexColor('#a85c42'))
    canvas.drawCentredString(letter[0]/2, letter[1] - 0.7*inch, "CLIMATE ZONE FINDER")
    
    # Add horizontal line below header
    canvas.setStrokeColor(colors.grey)
    canvas.setLineWidth(1)
    canvas.line(0.5*inch, letter[1] - 1.1*inch, letter[0] - 0.5*inch, letter[1] - 1.1*inch)
    
    canvas.restoreState()


def generate_nbc_pdf_report(location_name, state_name, climate_zone, latitude, longitude, zone_info):
    """Generate a comprehensive PDF report for NBC climate zone"""
    
    pdf_buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=1.3*inch,  # Increased to accommodate header
        bottomMargin=0.5*inch
    )
    
    # Container for PDF elements
    story = []
    
    # Define styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.black,
        spaceAfter=12,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=18,
        textColor=colors.HexColor('#1f1f1f'),
        spaceAfter=10,
        spaceBefore=10,
        fontName='Helvetica-Bold'
    )
    
    section_heading_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading3'],
        fontSize=14,
        textColor=colors.HexColor('#333333'),
        spaceAfter=8,
        spaceBefore=8,
        fontName='Helvetica-Bold'
    )
    
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontSize=11,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
        leading=14
    )
    
    # Title
    story.append(Paragraph("Climate Analysis", title_style))
    story.append(Spacer(1, 0.2*inch))

    # Report Header
    story.append(Paragraph("Project Information", heading_style))
    
    
    # Table
    location_data = [
        ['Property', 'Value'],
        ['Location', location_name],
        ['State', state_name],
        ['Country', 'India'],
        ['Latitude', f'{latitude:.2f}'],
        ['Longitude', f'{longitude:.2f}'],
        ['Climate Zone', climate_zone]
    ]
    
    story.append(Spacer(1, 0.1*inch))


    location_table = Table(location_data, colWidths=[3*inch, 3*inch], hAlign='LEFT')
    location_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')]),
        ('BOX', (0,0), (-1,-1), 2, colors.grey)
    ]))
    
    story.append(location_table)
    story.append(Spacer(1, 0.1*inch))
    
    # Climate Zone Information
    story.append(Paragraph("Climate Zone Designation", heading_style))
    
    zone_info_text = f"""
    This location falls under the <b>{climate_zone}</b> climate classification as per the 
    National Building Code (NBC-2016) of India. Understanding the climatic characteristics 
    of this zone is essential for designing energy-efficient buildings by following the Passive Design Strategies.
    """
    story.append(Paragraph(zone_info_text, body_style))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph("Passive Design Strategies:", heading_style))


    # Strategy images and descriptions
    if zone_info and 'images' in zone_info:
        for i, (img_path, title, description) in enumerate(zip(
            zone_info['images'], 
            zone_info['titles'], 
            zone_info['descriptions']
        )):
            story.append(Paragraph(f"{title}", section_heading_style))
            
            try:
                img = Image(img_path, width=3.5*inch, height=2.5*inch)
                story.append(img)
                story.append(Spacer(1, 0.10*inch))
            except:
                story.append(Paragraph("[Image not available]", body_style))
                story.append(Spacer(1, 0.1*inch))
            
            # Description
            story.append(Paragraph(description, body_style))
            story.append(Spacer(1, 0.3*inch))
    
    story.append(PageBreak())
    
    # Footer information
    footer_text = f"""
    <b>Report Generated On:</b> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}<br/>
    <b>Classification Standard:</b> National Building Code (NBC-2016)<br/>
    <br/>
    <i>This report provides climate-specific design strategies for sustainable and energy-efficient buildings. 
    For more information, visit the Climate Zone Finder dashboard.</i>
    """
    story.append(Paragraph(footer_text, body_style))
    
    # Build PDF
    doc.build(story, onFirstPage=add_page_header, onLaterPages=add_page_header)
    pdf_buffer.seek(0)
    
    return pdf_buffer

# Climate zone strategy images and descriptions only for NBC
def display_climate_zone_images(climate_zone):
    if climate_zone in CLIMATE_ZONE_DATA:
        zone_info = CLIMATE_ZONE_DATA[climate_zone]
        zone_color = get_nbc_zone_color(climate_zone)
        
        st.markdown(
            f'''
            <div class="climate-zone-header">
                Passive Design Strategies for 
                <span style="color: {zone_color};">{climate_zone}</span> 
                Climate Zone
            </div>
            ''',
            unsafe_allow_html=True
        )
            
        # CSS for iamge styling
        st.markdown("""
            <style>
            [data-testid="stImage"] {
                display: flex !important;
                justify-content: center !important;
                align-items: center !important;
                min-height: 300px !important;
                background: #ffffff;
                border-radius: 0px;
                padding: 10px;
                margin: 15px 0;
            }
            
            [data-testid="stImage"] img {
                height: 260px !important;
                width: auto !important;
                max-width: 100% !important;
                object-fit: contain !important;
            }
            
            .nbc-image-title {
                font-size: 20px !important;
                font-weight: 600 !important;
                color: #333 !important;
                margin-bottom: 10px !important;
                text-align: center !important;
            }
            
            .nbc-image-description {
                font-size: 15px !important;
                color: #555 !important;
                text-align: justify !important;
                margin-top: 15px !important;
                line-height: 1.6 !important;
                padding: 0 5px !important;
            }
            </style>
        """, unsafe_allow_html=True)
        
        img_col1, img_col2, img_col3 = st.columns(3)
        
        with img_col1:
            st.markdown(f'<div class="nbc-image-title">{zone_info["titles"][0]}</div>', unsafe_allow_html=True)
            st.image(zone_info["images"][0], use_container_width=True)
            st.markdown(f'<div class="nbc-image-description">{zone_info["descriptions"][0]}</div>', unsafe_allow_html=True)
        
        with img_col2:
            st.markdown(f'<div class="nbc-image-title">{zone_info["titles"][1]}</div>', unsafe_allow_html=True)
            st.image(zone_info["images"][1], use_container_width=True)
            st.markdown(f'<div class="nbc-image-description">{zone_info["descriptions"][1]}</div>', unsafe_allow_html=True)
        
        with img_col3:
            st.markdown(f'<div class="nbc-image-title">{zone_info["titles"][2]}</div>', unsafe_allow_html=True)
            st.image(zone_info["images"][2], use_container_width=True)
            st.markdown(f'<div class="nbc-image-description">{zone_info["descriptions"][2]}</div>', unsafe_allow_html=True)




left_col, right_col = st.columns([1, 2.5])

with left_col:
    st.markdown('<div class="label-text">Climate Classification Standard</div>', unsafe_allow_html=True)
    standard_options = ["ASHRAE-169 (2013)", "NBC-2016"]
    select_standard = st.selectbox("Select Standard", standard_options, key="standard", label_visibility="collapsed", width=250)

    # ASHRAE Standard
    if select_standard == "ASHRAE-169 (2013)":
        df = load_ashrae_data()

        st.markdown('<div class="section-title">Location Selection</div>', unsafe_allow_html=True)

        # Country
        st.markdown('<div class="label-text">Country</div>', unsafe_allow_html=True)
        countries = sorted(df["Country"].unique())
        country_index = 0
        # Use URL param if provided and exists in list
        if url_location_params["country"] and url_location_params["country"] in countries:
            country_index = countries.index(url_location_params["country"])
        selected_country = st.selectbox("Country", countries, index=country_index, key="country", label_visibility="collapsed", width=250)

        # Location
        st.markdown('<div class="label-text">Location</div>', unsafe_allow_html=True)
        locations = sorted(df[df["Country"] == selected_country]["Location"].unique())
        location_index = 0
        # Use URL param if provided and exists in current country's locations
        if url_location_params["location"] and url_location_params["location"] in locations:
            location_index = locations.index(url_location_params["location"])
        selected_location = st.selectbox("Location", locations, index=location_index, key="location", label_visibility="collapsed", width=250)

        # Climate Zone
        result = df[(df["Country"] == selected_country) & (df["Location"] == selected_location)]
        st.markdown('<div class="label-text">Climate Zone:</div>', unsafe_allow_html=True)
        if not result.empty:
            climate_zone = result.iloc[0]["Climate Zone"]
            zone_color = get_ashrae_zone_color(df, climate_zone)
            st.markdown(
                f'<p style="font-size: 28px; font-weight: bold; color: {zone_color}; margin: 10px 0;">{climate_zone}</p>',
                unsafe_allow_html=True,
            )
        else:
            climate_zone = None
            st.markdown('<p style="font-size: 28px; font-weight: bold; color: #dc3545; margin: 10px 0;">-</p>',
                        unsafe_allow_html=True)

        # Climate Zone Name
        st.markdown('<div class="label-text">Climate Zone Name:</div>', unsafe_allow_html=True)
        if not result.empty:
            climate_zone_name = result.iloc[0]["Climate Zone Name"]
            zone_color = get_ashrae_zone_color(df, climate_zone)
            st.markdown(
                f'<p style="font-size: 18px; font-weight: 500; color: {zone_color}; margin: 10px 0;">{climate_zone_name}</p>',
                unsafe_allow_html=True,
            )
        else:
            climate_zone_name = None
            st.markdown('<p style="font-size: 18px; font-weight: 500; color: #dc3545; margin: 10px 0;">-</p>',
                        unsafe_allow_html=True)

        # Buttons

        # st.markdown("""
        # <style>
        # div.stButton > button:first-child {
        #     background-color: #a85c42;
        #     color: white;
        #     height: 3em;
        #     font-size: 16px;
        # }
        # div.stButton > button:first-child:hover {
        #     background-color: #E63E3E;
        #     color: white;
        # }
        # </style>
        # """, unsafe_allow_html=True)

        report_clicked = st.button("Generate Report", type="secondary", width=200)
        if not result.empty and pd.notna(result.iloc[0].get("EPW File", None)):
            epw_url = result.iloc[0]["EPW File"]
            if epw_url and str(epw_url).strip() != "" and str(epw_url) != "0":
                st.link_button("Download EPW", epw_url, type="primary", width=200)
                if st.button("Analyze in Dashboard", key="analyze_epw_ashrae", width=200, type="secondary"):
                    st.session_state["epw_url"] = epw_url
                    st.switch_page("pages/analysis.py")
            else:
                st.button("Download EPW", type="primary", disabled=True, width=200)
        else:
            st.button("Download EPW", type="primary", disabled=True, width=200)
        if report_clicked and not result.empty:
            st.info("Report generation for ASHRAE is under development. Please check back soon.")

    # NBC Standard (India)
    elif select_standard == "NBC-2016":
        df = load_nbc_data()
        
        st.markdown('<div class="section-title">Location Selection</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # Country
        st.markdown('<div class="label-text">Country</div>', unsafe_allow_html=True)
        country = "India"
        selected_country = st.selectbox(country, [country], index=0, key="country_nbc", label_visibility="collapsed", disabled=True, width=300)

        st.markdown('<div class="label-text">State</div>', unsafe_allow_html=True)
        states = sorted(df["State"].unique())

        # Default state selection - use URL param if provided and valid, else use "Delhi"
        default_state = url_location_params["state"] if (url_location_params["state"] and url_location_params["state"] in states) else "Delhi"
        default_index = states.index(default_state) if default_state in states else 0

        selected_state = st.selectbox("State", states, index=default_index, key="state", label_visibility="collapsed", width=300)
        
        st.markdown('<div class="label-text">Location</div>', unsafe_allow_html=True)
        locations = sorted(df[df["State"] == selected_state]["Location"].unique())
        location_index = 0
        # Use URL param if provided and exists in current state's locations
        if url_location_params["location"] and url_location_params["location"] in locations:
            location_index = locations.index(url_location_params["location"])
        selected_location = st.selectbox("Location", locations, index=location_index, key="nbc_location", label_visibility="collapsed", width=300)
        
        result = df[(df["State"] == selected_state) & (df["Location"] == selected_location)]
        
        st.markdown('<div class="label-text">Climate Zone:</div>', unsafe_allow_html=True)
        if not result.empty:
            climate_zone = result.iloc[0]["Climate Zone"]
            zone_color = get_nbc_zone_color(climate_zone)
            st.markdown(
                f'<p style="font-size: 28px; font-weight: bold; color: {zone_color}; margin: 10px 0;">{climate_zone}</p>',
                unsafe_allow_html=True,
            )
        else:
            climate_zone = None
            st.markdown('<p style="font-size: 28px; font-weight: bold; color: #dc3545; margin: 10px 0;">-</p>',
                        unsafe_allow_html=True)
        
        # report_clicked = st.button("Generate Report", type="primary", use_container_width=False)
        if not result.empty and pd.notna(result.iloc[0].get("EPW File", None)):
            epw_url = result.iloc[0]["EPW File"]
            if epw_url and str(epw_url).strip() != "" and str(epw_url) != "0":
                st.link_button("Download EPW", epw_url, type="primary", width=200)
                if st.button("Analyze in Dashboard", key="analyze_epw_nbc", width=200, type="secondary"):
                    st.session_state["epw_url"] = epw_url
                    st.switch_page("pages/analysis.py")
            else:
                st.link_button("Download EPW", type="primary", disabled=True, width=200)
        else:
            st.link_button("Download EPW", type="primary", disabled=True, width=200)
        
        # report_clicked and not result.empty:
        epw_file = result.iloc[0].get("EPW File", "Not Available")
        lat_selected = result.iloc[0]["Latitude"]
        lon_selected = result.iloc[0]["Longitude"]
        
        # Use the CLIMATE_ZONE_DATA to display the image and discriptions
        if climate_zone in CLIMATE_ZONE_DATA:
            zone_info = CLIMATE_ZONE_DATA[climate_zone]
            
            # Generate PDF
            pdf_buffer = generate_nbc_pdf_report(
                selected_location,
                selected_state,
                climate_zone,
                lat_selected,
                lon_selected,
                zone_info
            )
            
            pdf_buffer.seek(0)
            pdf_data = pdf_buffer.getvalue()
            filename = f"Climate_Zone_Report_{selected_location}_{climate_zone}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            
            downloaded = st.download_button(
            label="Generate Report",
            data=pdf_data,
            file_name=filename,
            mime="application/pdf",
            type="secondary",
            width=200,
            key="download_nbc_report",
            )
            if downloaded:
                analytics.log_download(
                    "NBC Climate Zone PDF Report",
                    detail=f"{selected_location}, {selected_state} — {climate_zone}",
                )

            
        else:
            st.error("Climate zone data not available for PDF generation.")


# Right Column - Map Display
with right_col:
    if select_standard == "ASHRAE-169 (2013)":
        if not result.empty:
            lat_selected = result.iloc[0]["Latitude"]
            lon_selected = result.iloc[0]["Longitude"]
                
            # Check if coordinates are valid
            if pd.notna(lat_selected) and pd.notna(lon_selected):
                amcharts_world_globe(
                    df,
                    lat_selected,
                    lon_selected,
                    selected_location,
                    selected_country,
                    climate_zone,
                    climate_zone_name
                )
            else:
                st.warning(f"⚠️ Coordinates not available for {selected_location}. Please select a different location.")
        else:
            st.info("Please select a location to view on the map.")
    
    elif select_standard == "NBC-2016":
        if not result.empty:
            lat_selected = result.iloc[0]["Latitude"]
            lon_selected = result.iloc[0]["Longitude"]
            
            # Check if coordinates are valid
            if pd.notna(lat_selected) and pd.notna(lon_selected):
                amcharts_india_map(
                    df,
                    lat_selected,
                    lon_selected,
                    selected_location,
                    selected_state,
                    climate_zone
                )
            else:
                st.warning(f"⚠️ Coordinates not available for {selected_location}. Please select a different location.")
        else:
            st.info("Please select a location to view on the map.")


# Images Section - Display below the map (outside columns)
if select_standard == "NBC-2016":
    if not result.empty and climate_zone:
        display_climate_zone_images(climate_zone)


# Adding extra space at the bottom
st.markdown("<br><br>", unsafe_allow_html=True)

# Footer Section
st.image("images/EDS-footer.png", width=2000)
st.markdown(
    """
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <style>
        .footer {
            background-color: #f8f9fa;
            padding: 20px 0;
            color: #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
            text-align: center;
        }
        .footer .logo {
            flex: 1;
        }
        .footer .logo img {
            max-width: 150px;
            height: auto;
        }
        .footer .social-media {
            flex: 2;
        }
        .footer .social-media p {
            margin: 0;
            font-size: 16px;
        }
        .footer .icons {
            margin-top: 10px;
        }
        .footer .icons a {
            margin: 0 10px;
            color: #666;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .footer .icons a:hover {
            color: #0077b5; /* LinkedIn color as default */
        }
        .footer .icons a .fab {
            font-size: 28px;
        }
        .footer .additional-content {
            margin-top: 10px;
        }
        .footer .additional-content h4 {
            margin: 0;
            font-size: 18px;
            color: #007bff;
        }
        .footer .additional-content p {
            margin: 5px 0;
            font-size: 16px;
        }
    </style>
    <div style="text-align:center; font-size:14px;">
        Email: <a href="mailto:info@edsglobal.com">info@edsglobal.com</a>   |   
        Phone: +91 . 11 . 4056 8633   |   
        <a href="https://twitter.com/edsglobal?lang=en" target="_blank"><i class="fab fa-twitter" style="color:#1DA1F2; margin:0 6px;"></i></a>
        <a href="https://www.facebook.com/Environmental.Design.Solutions/" target="_blank"><i class="fab fa-facebook" style="color:#4267B2; margin:0 6px;"></i></a>
        <a href="https://www.instagram.com/eds_global/?hl=en" target="_blank"><i class="fab fa-instagram" style="color:#E1306C; margin:0 6px;"></i></a>
        <a href="https://www.linkedin.com/company/environmental-design-solutions/" target="_blank"><i class="fab fa-linkedin" style="color:#0077b5; margin:0 6px;"></i></a>
    </div>
    """,
    unsafe_allow_html=True
)