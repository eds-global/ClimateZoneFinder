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

# CSS for Header
st.markdown(
    f"""
    <style>
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

    <div class="app-header">
        <img src="data:image/png;base64,{logo_base64}" />
        <h2 class="header-title">CLIMATE ZONE FINDER - ANALYSIS</h2>
        <div style="width: 150px;"></div>
    </div>
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

# Create header with back button
header_col1, header_col2, header_col3 = st.columns([1, 2.5, 0.5])
with header_col3:
    if st.button("← Back to Home", key="back_nav"):
        st.switch_page("app.py")

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
            We're working hard to bring you powerful analysis features to help you make 
            better informed decisions about climate-responsive building design.
        </div>
        
        <div class="feature-preview">
            <div class="feature-title">📊 Upcoming Features</div>
            <div class="feature-list">
                ✓ Detailed climate data visualization<br>
                ✓ Comparative analysis across locations<br>
                ✓ Energy consumption predictions<br>
                ✓ Custom design recommendations<br>
                ✓ Advanced reporting tools
            </div>
        </div>
    </div>
""", unsafe_allow_html=True)

st.markdown("<br><br>", unsafe_allow_html=True)