from turtle import width
import streamlit as st
import base64

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

# Coming Soon Content
st.markdown("""
    <div class="coming-soon-container">
        <div class="coming-soon-title">Coming Soon...</div>
        <div class="coming-soon-subtitle">Advanced Climate Analysis Tools</div>
        <div class="coming-soon-message">
            We're developing powerful analysis features to support better-informed, climate-responsive decisions.
        </div>
        
""", unsafe_allow_html=True)

st.markdown("<br><br>", unsafe_allow_html=True)