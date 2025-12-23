# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from typing import List, Dict
import time
import sqlite3
# 導入你的模組（需與你原專案一致）
from weather_crawler import PortWeatherCrawler
from weather_parser import WeatherParser, WeatherRecord

# =========================
# App Config
# =========================
st.set_page_config(
    page_title="海技部-港口氣象監控系統",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================
# Brand Tokens（萬海官網風格：白底 + Navy + Red）
# =========================
BRAND = {
    "NAVY": "#0B2E5B",         # 深海軍藍
    "NAVY_2": "#0A2342",       # 更深一階
    "RED": "#E60012",          # 萬海紅（常見品牌紅近似）
    "SKY": "#1F6FEB",          # 藍色互動/連結
    "BG": "#F6F8FC",           # 乾淨淺灰白背景
    "CARD": "#FFFFFF",
    "TEXT": "#0F172A",
    "MUTED": "#5B667A",
    "BORDER": "rgba(15, 23, 42, 0.10)",
}

# Logo（萬海官網 Logo）
LOGO_URL = "https://www.wanhai.com/upload/2021/09/20210929112345678.png"

# =========================
# CSS (Wan Hai-like Corporate Style - Enhanced)
# =========================
def load_css():
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');

        :root {{
          --navy: {BRAND['NAVY']};
          --navy2: {BRAND['NAVY_2']};
          --red: {BRAND['RED']};
          --sky: {BRAND['SKY']};
          --bg: {BRAND['BG']};
          --card: {BRAND['CARD']};
          --text: {BRAND['TEXT']};
          --muted: {BRAND['MUTED']};
          --border: {BRAND['BORDER']};

          --radius: 16px;
          --radius-sm: 12px;
          --radius-lg: 20px;

          --shadow-sm: 0 1px 2px rgba(2, 6, 23, 0.06);
          --shadow-md: 0 8px 24px rgba(2, 6, 23, 0.12);
          --shadow-lg: 0 16px 48px rgba(2, 6, 23, 0.16);
          --shadow-xl: 0 24px 64px rgba(2, 6, 23, 0.20);
        }}

        html, body, [class*="css"] {{
          font-family: 'Inter', 'Microsoft JhengHei', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
          color: var(--text);
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
        }}

        /* App 背景：官網系乾淨底色 + 頂部淡淡品牌漸層 */
        .stApp {{
          background:
            radial-gradient(1200px 600px at 20% 0%, rgba(11,46,91,0.08), transparent 65%),
            radial-gradient(1000px 600px at 85% 0%, rgba(230,0,18,0.05), transparent 65%),
            linear-gradient(180deg, #FFFFFF 0%, var(--bg) 35%, var(--bg) 100%);
        }}

        .block-container {{
          max-width: 1280px;
          padding-top: 1.5rem;
          padding-bottom: 2.5rem;
        }}

        h1,h2,h3,h4 {{
          color: var(--text) !important;
          font-weight: 900 !important;
          letter-spacing: -0.025em;
        }}
        
        h1 {{ font-size: 2.2rem !important; }}
        h2 {{ font-size: 1.75rem !important; }}
        h3 {{ font-size: 1.35rem !important; }}
        
        p, li, label, span {{ color: var(--text); }}
        .stCaption, [data-testid="stCaptionContainer"] {{
          color: var(--muted) !important;
        }}
        hr {{ border-color: rgba(15, 23, 42, 0.10) !important; }}

        /* =========================
           Sidebar：白底 + 精緻品牌區塊
        ========================== */
        section[data-testid="stSidebar"] {{
          background: linear-gradient(180deg, #FFFFFF 0%, #FAFBFC 100%);
          border-right: 1px solid rgba(15, 23, 42, 0.08);
        }}
        
        section[data-testid="stSidebar"] .block-container {{
          padding-top: 1.2rem;
        }}

        /* Sidebar 品牌區塊 - 放大 Logo 並優化布局 */
        .sidebar-brand {{
          position: relative;
          border-radius: var(--radius-lg);
          padding: 24px 20px;
          background: linear-gradient(135deg, rgba(11,46,91,1) 0%, rgba(10,35,66,1) 100%);
          box-shadow: var(--shadow-lg);
          color: #fff;
          margin-bottom: 20px;
          overflow: hidden;
        }}
        
        /* 品牌區塊背景裝飾 */
        .sidebar-brand::before {{
          content: '';
          position: absolute;
          top: -50%;
          right: -20%;
          width: 200px;
          height: 200px;
          background: radial-gradient(circle, rgba(230,0,18,0.15) 0%, transparent 70%);
          border-radius: 50%;
        }}
        
        .sidebar-brand::after {{
          content: '';
          position: absolute;
          bottom: -30%;
          left: -10%;
          width: 150px;
          height: 150px;
          background: radial-gradient(circle, rgba(31,111,235,0.12) 0%, transparent 70%);
          border-radius: 50%;
        }}
        
        .sidebar-brand-content {{
          position: relative;
          z-index: 1;
        }}
        
        .sidebar-brand .logo-container {{
          display: flex;
          align-items: center;
          gap: 16px;
          margin-bottom: 16px;
        }}
        
        .sidebar-brand .logo-wrapper {{
          flex-shrink: 0;
          width: 64px;
          height: 64px;
          border-radius: 14px;
          background: rgba(255,255,255,0.15);
          backdrop-filter: blur(10px);
          border: 2px solid rgba(255,255,255,0.25);
          padding: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 8px 24px rgba(0,0,0,0.15);
          transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}
        
        .sidebar-brand .logo-wrapper:hover {{
          transform: translateY(-2px) scale(1.02);
          box-shadow: 0 12px 32px rgba(0,0,0,0.20);
        }}
        
        .sidebar-brand .logo-wrapper img {{
          width: 100%;
          height: 100%;
          object-fit: contain;
          filter: brightness(0) invert(1);
        }}
        
        .sidebar-brand .text-content {{
          flex: 1;
        }}
        
        .sidebar-brand .title {{
          margin: 0 0 6px 0;
          font-weight: 900;
          font-size: 1.1rem;
          color: #fff;
          line-height: 1.3;
          letter-spacing: -0.01em;
        }}
        
        .sidebar-brand .sub {{
          margin: 0;
          font-size: 0.85rem;
          color: rgba(255,255,255,0.80);
          line-height: 1.4;
          font-weight: 500;
        }}
        
        .sidebar-brand .badge {{
          display: inline-flex;
          align-items: center;
          gap: 8px;
          margin-top: 14px;
          padding: 8px 14px;
          border-radius: 999px;
          background: rgba(255,255,255,0.12);
          backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.20);
          color: rgba(255,255,255,0.95);
          font-size: 0.82rem;
          font-weight: 800;
          transition: all 0.2s ease;
        }}
        
        .sidebar-brand .badge:hover {{
          background: rgba(255,255,255,0.18);
          border-color: rgba(255,255,255,0.30);
          transform: translateX(2px);
        }}

        /* Inputs / Select：明亮官網風 */
        .stTextInput input, .stNumberInput input, .stTextArea textarea {{
          border-radius: 12px !important;
          border: 1px solid rgba(15, 23, 42, 0.14) !important;
          background: #FFFFFF !important;
          color: var(--text) !important;
          box-shadow: 0 1px 2px rgba(2,6,23,0.04) !important;
          transition: all 0.2s ease !important;
        }}
        
        .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {{
          border-color: rgba(31,111,235,0.50) !important;
          box-shadow: 0 0 0 4px rgba(31,111,235,0.10), 0 2px 8px rgba(2,6,23,0.08) !important;
          outline: none !important;
        }}
        
        .stTextInput input::placeholder, .stTextArea textarea::placeholder {{
          color: rgba(91,102,122,0.60) !important;
        }}

        /* Autofill */
        input:-webkit-autofill,
        input:-webkit-autofill:hover,
        input:-webkit-autofill:focus {{
          -webkit-text-fill-color: var(--text) !important;
          transition: background-color 9999s ease-in-out 0s !important;
          box-shadow: 0 0 0px 1000px #FFFFFF inset !important;
          border: 1px solid rgba(15, 23, 42, 0.14) !important;
        }}

        [data-baseweb="select"] > div {{
          border-radius: 12px !important;
          border-color: rgba(15, 23, 42, 0.14) !important;
          background: #FFFFFF !important;
          color: var(--text) !important;
          transition: all 0.2s ease !important;
        }}
        
        [data-baseweb="select"] > div:focus-within {{
          border-color: rgba(31,111,235,0.50) !important;
          box-shadow: 0 0 0 4px rgba(31,111,235,0.10) !important;
        }}

        /* Buttons：官網 CTA（紅）+ 次要（白） */
        .stButton > button {{
          border-radius: 12px;
          border: 1px solid rgba(15, 23, 42, 0.14);
          background: #FFFFFF;
          color: var(--text);
          font-weight: 800;
          padding: 0.65rem 1.1rem;
          transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
          box-shadow: var(--shadow-sm);
        }}
        
        .stButton > button:hover {{
          transform: translateY(-1px);
          border-color: rgba(15, 23, 42, 0.24);
          box-shadow: var(--shadow-md);
        }}
        
        .stButton > button:active {{
          transform: translateY(0px);
        }}
        
        .stButton > button[kind="primary"] {{
          background: linear-gradient(135deg, var(--red) 0%, #C80010 100%);
          border-color: rgba(230,0,18,0.40);
          color: #FFFFFF;
          box-shadow: 0 8px 24px rgba(230,0,18,0.25), 0 2px 8px rgba(230,0,18,0.15);
        }}
        
        .stButton > button[kind="primary"]:hover {{
          box-shadow: 0 12px 32px rgba(230,0,18,0.30), 0 4px 12px rgba(230,0,18,0.20);
          transform: translateY(-2px);
        }}

        /* Cards / Panels */
        .card {{
          background: var(--card);
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius);
          box-shadow: var(--shadow-sm);
          transition: all 0.3s ease;
        }}
        
        .card.pad {{ 
          padding: 20px 22px; 
        }}
        
        .card:hover {{
          border-color: rgba(15, 23, 42, 0.16);
          box-shadow: var(--shadow-md);
          transform: translateY(-2px);
        }}

        /* Top Bar */
        .topbar {{
          position: relative;
          background: linear-gradient(135deg, rgba(11,46,91,1) 0%, rgba(10,35,66,1) 100%);
          border-radius: var(--radius-lg);
          box-shadow: var(--shadow-lg);
          padding: 24px 26px;
          margin-bottom: 20px;
          color: #fff;
          overflow: hidden;
        }}
        
        .topbar::before {{
          content: '';
          position: absolute;
          top: -50%;
          right: -10%;
          width: 300px;
          height: 300px;
          background: radial-gradient(circle, rgba(230,0,18,0.12) 0%, transparent 70%);
          border-radius: 50%;
        }}
        
        .topbar-content {{
          position: relative;
          z-index: 1;
        }}
        
        .topbar .h {{
          margin: 0 0 10px 0;
          font-size: 1.5rem;
          font-weight: 900;
          color: #fff;
          letter-spacing: -0.02em;
        }}
        
        .topbar .p {{
          margin: 0;
          color: rgba(255,255,255,0.85);
          font-size: 0.95rem;
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
        }}
        
        .topbar .chip {{
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 7px 13px;
          border-radius: 999px;
          background: rgba(255,255,255,0.14);
          backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.22);
          color: rgba(255,255,255,0.95);
          font-size: 0.84rem;
          font-weight: 800;
          transition: all 0.2s ease;
        }}
        
        .topbar .chip:hover {{
          background: rgba(255,255,255,0.20);
          transform: translateY(-1px);
        }}

        /* Info card (Port header) */
        .info-card {{
          background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%);
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius-lg);
          padding: 24px 26px;
          box-shadow: var(--shadow-md);
          margin-bottom: 20px;
          transition: all 0.3s ease;
        }}
        
        .info-card:hover {{
          box-shadow: var(--shadow-lg);
          transform: translateY(-2px);
        }}
        
        .info-meta {{
          display: flex;
          flex-wrap: wrap;
          gap: 14px;
          align-items: center;
          color: var(--muted);
          font-size: 0.92rem;
          margin-top: 12px;
        }}
        
        .divider-dot {{
          width: 4px;
          height: 4px;
          border-radius: 999px;
          background: rgba(91,102,122,0.50);
          display: inline-block;
        }}

        /* Risk badge */
        .risk-badge {{
          padding: 7px 14px;
          border-radius: 999px;
          font-size: 0.85em;
          font-weight: 900;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 1px solid transparent;
          white-space: nowrap;
          transition: all 0.2s ease;
        }}
        
        .risk-badge:hover {{
          transform: scale(1.05);
        }}
        
        .risk-0 {{ 
          background: rgba(34,197,94,0.14); 
          color: #0F5132; 
          border-color: rgba(34,197,94,0.25); 
        }}
        .risk-1 {{ 
          background: rgba(245,158,11,0.14); 
          color: #7A4B00; 
          border-color: rgba(245,158,11,0.25); 
        }}
        .risk-2 {{ 
          background: rgba(251,146,60,0.14); 
          color: #7A2E00; 
          border-color: rgba(251,146,60,0.25); 
        }}
        .risk-3 {{ 
          background: rgba(230,0,18,0.12); 
          color: #8A0010; 
          border-color: rgba(230,0,18,0.25); 
        }}

        /* Alert list card */
        .port-alert-card {{
          background: #FFFFFF;
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius);
          padding: 18px 20px;
          margin-bottom: 12px;
          box-shadow: var(--shadow-sm);
          transition: all 0.3s ease;
        }}
        
        .port-alert-card:hover {{
          box-shadow: var(--shadow-md);
          transform: translateX(4px);
        }}
        
        .port-alert-card .title {{
          margin: 0 0 8px 0;
          font-weight: 900;
          font-size: 1.05rem;
        }}
        
        .port-alert-card .meta {{
          margin: 0;
          color: var(--muted);
          font-size: 0.90rem;
        }}
        
        .pill {{
          padding: 7px 12px;
          border-radius: 999px;
          font-size: 0.82rem;
          font-weight: 900;
          border: 1px solid rgba(15,23,42,0.16);
          background: rgba(11,46,91,0.06);
          color: var(--navy);
          white-space: nowrap;
          transition: all 0.2s ease;
        }}
        
        .pill:hover {{
          background: rgba(11,46,91,0.10);
          transform: scale(1.05);
        }}

        /* Metrics */
        div[data-testid="stMetric"] {{
          background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%);
          border: 1px solid rgba(15, 23, 42, 0.10);
          padding: 18px 20px;
          border-radius: var(--radius);
          box-shadow: var(--shadow-sm);
          transition: all 0.3s ease;
        }}
        
        div[data-testid="stMetric"]:hover {{
          box-shadow: var(--shadow-md);
          transform: translateY(-2px);
        }}
        
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{
          color: var(--muted) !important;
          font-weight: 800 !important;
          font-size: 0.90rem !important;
        }}
        
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
          color: var(--text) !important;
          font-weight: 900 !important;
          letter-spacing: -0.02em;
          font-size: 2.0rem !important;
        }}

        /* DataFrame */
        .stDataFrame, [data-testid="stDataFrame"] {{
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius);
          overflow: hidden;
          background: #FFFFFF;
          box-shadow: var(--shadow-sm);
        }}

        /* Tabs / Radio */
        [data-testid="stTabs"] button {{
          font-weight: 800 !important;
          color: rgba(91,102,122,0.90) !important;
          transition: all 0.2s ease !important;
        }}
        
        [data-testid="stTabs"] button:hover {{
          color: var(--navy) !important;
        }}
        
        [data-testid="stTabs"] button[aria-selected="true"] {{
          color: var(--navy) !important;
          font-weight: 900 !important;
        }}

        /* Plotly modebar */
        .js-plotly-plot .plotly .modebar {{
          opacity: 0.15;
          transition: opacity 0.2s ease;
        }}
        
        .js-plotly-plot:hover .plotly .modebar {{
          opacity: 1;
        }}

        /* Welcome hero */
        .hero {{
          max-width: 1000px;
          margin: 20px auto 0 auto;
          text-align: center;
          padding: 32px 16px 16px 16px;
        }}
        
        .hero h1 {{
          margin: 0 0 12px 0;
          font-size: 2.4rem;
          background: linear-gradient(135deg, var(--navy) 0%, var(--navy2) 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }}
        
        .hero .sub {{
          margin: 0 auto;
          max-width: 760px;
          color: var(--muted);
          font-size: 1.05rem;
          line-height: 1.7;
        }}
        
        .hero-grid {{
          margin-top: 24px;
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 18px;
        }}
        
        @media (max-width: 920px) {{
          .hero-grid {{ grid-template-columns: 1fr; }}
        }}
        
        .hero-grid .card {{
          text-align: left;
        }}
        
        .hero-grid .card h3 {{
          background: linear-gradient(135deg, var(--navy) 0%, var(--sky) 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }}

        /* Expander */
        .streamlit-expanderHeader {{
          font-weight: 800 !important;
          border-radius: 12px !important;
        }}

        /* Progress bar */
        .stProgress > div > div > div {{
          background: linear-gradient(90deg, var(--navy) 0%, var(--sky) 100%);
        }}

        /* Info/Warning/Error boxes */
        .stAlert {{
          border-radius: var(--radius) !important;
          border-width: 1px !important;
        }}

        </style>
        """,
        unsafe_allow_html=True,
    )


load_css()

# =========================
# Session State
# =========================
if "crawler" not in st.session_state:
    st.session_state.crawler = None
if "analysis_results" not in st.session_state:
    st.session_state.analysis_results = {}
if "last_update" not in st.session_state:
    st.session_state.last_update = None
if "port_options_cache" not in st.session_state:
    st.session_state.port_options_cache = {}
if "crawler_initialized" not in st.session_state:
    st.session_state.crawler_initialized = False
if "aedyn_username" not in st.session_state:
    st.session_state.aedyn_username = ""
if "aedyn_password" not in st.session_state:
    st.session_state.aedyn_password = ""
if "login_configured" not in st.session_state:
    st.session_state.login_configured = False


# =========================
# Risk Analyzer
# =========================
class WeatherRiskAnalyzer:
    THRESHOLDS = {
        "wind_caution": 25,
        "wind_warning": 30,
        "wind_danger": 40,
        "gust_caution": 35,
        "gust_warning": 40,
        "gust_danger": 50,
        "wave_caution": 2.0,
        "wave_warning": 2.5,
        "wave_danger": 4.0,
    }

    @classmethod
    def analyze_record(cls, record: WeatherRecord) -> Dict:
        risks = []
        risk_level = 0

        # wind speed
        if record.wind_speed >= cls.THRESHOLDS["wind_danger"]:
            risks.append(f"⛔ 風速危險: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_speed >= cls.THRESHOLDS["wind_warning"]:
            risks.append(f"⚠️ 風速警告: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_speed >= cls.THRESHOLDS["wind_caution"]:
            risks.append(f"⚡ 風速注意: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 1)

        # gust
        if record.wind_gust >= cls.THRESHOLDS["gust_danger"]:
            risks.append(f"⛔ 陣風危險: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_gust >= cls.THRESHOLDS["gust_warning"]:
            risks.append(f"⚠️ 陣風警告: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_gust >= cls.THRESHOLDS["gust_caution"]:
            risks.append(f"⚡ 陣風注意: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 1)

        # wave
        if record.wave_height >= cls.THRESHOLDS["wave_danger"]:
            risks.append(f"⛔ 浪高危險: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 3)
        elif record.wave_height >= cls.THRESHOLDS["wave_warning"]:
            risks.append(f"⚠️ 浪高警告: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 2)
        elif record.wave_height >= cls.THRESHOLDS["wave_caution"]:
            risks.append(f"⚡ 浪高注意: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 1)

        return {
            "risk_level": risk_level,
            "risks": risks,
            "time": record.time,
            "wind_speed": record.wind_speed,
            "wind_gust": record.wind_gust,
            "wave_height": record.wave_height,
            "wind_direction": record.wind_direction,
            "wave_direction": record.wave_direction,
        }

    @classmethod
    def get_risk_label(cls, risk_level: int) -> str:
        return {0: "安全 Safe", 1: "注意 Caution", 2: "警告 Warning", 3: "危險 Danger"}.get(risk_level, "未知 Unknown")

    @classmethod
    def get_risk_color(cls, risk_level: int) -> str:
        return {0: "#16A34A", 1: "#D97706", 2: "#EA580C", 3: BRAND["RED"]}.get(risk_level, "#64748B")

    @classmethod
    def get_risk_badge(cls, risk_level: int) -> str:
        return f'<span class="risk-badge risk-{risk_level}">{cls.get_risk_label(risk_level)}</span>'


# =========================
# Functions
# =========================
def init_crawler(username: str, password: str):
    """初始化爬蟲，首次登入會顯示等待訊息"""
    try:
        import weather_crawler as wc
        
        status_container = st.empty()
        progress_bar = st.progress(0)
        
        status_container.info("🔍 正在檢查登入狀態...")
        progress_bar.progress(10)
        
        original_username = getattr(wc, "AEDYN_USERNAME", None)
        original_password = getattr(wc, "AEDYN_PASSWORD", None)

        if original_username is not None:
            wc.AEDYN_USERNAME = username
        if original_password is not None:
            wc.AEDYN_PASSWORD = password

        progress_bar.progress(20)
        status_container.info("⚙️ 正在初始化系統...")
        
        crawler = PortWeatherCrawler(auto_login=False)

        if original_username is not None:
            wc.AEDYN_USERNAME = original_username
        if original_password is not None:
            wc.AEDYN_PASSWORD = original_password

        progress_bar.progress(40)
        
        if hasattr(crawler, "login_manager"):
            crawler.login_manager.username = username
            crawler.login_manager.password = password
            
            status_container.info("🔐 正在驗證登入憑證...")
            progress_bar.progress(60)
            
            if hasattr(crawler.login_manager, "verify_cookies") and not crawler.login_manager.verify_cookies():
                status_container.warning("⚠️ Cookie 已過期或首次登入，正在重新登入...")
                status_container.info("🌐 正在啟動瀏覽器進行登入（首次登入約需 10-30 秒）...")
                progress_bar.progress(70)
                
                if hasattr(crawler, "refresh_cookies"):
                    with st.spinner("正在執行以下步驟：\n1. 啟動瀏覽器\n2. 連接 WNI 登入頁面\n3. 輸入帳號密碼\n4. 取得認證 Cookie\n5. 儲存登入狀態"):
                        success = crawler.refresh_cookies(headless=True)
                        
                    if success:
                        progress_bar.progress(90)
                        status_container.success("✅ 登入成功！Cookie 已儲存，下次將自動使用")
                    else:
                        progress_bar.progress(0)
                        status_container.error("❌ 登入失敗，請檢查帳號密碼")
                        return None
            else:
                progress_bar.progress(80)
                status_container.success("✅ 使用已儲存的登入狀態")
        
        progress_bar.progress(100)
        status_container.success("🎉 系統初始化完成！")
        
        time.sleep(1)
        status_container.empty()
        progress_bar.empty()
        
        return crawler
        
    except Exception as e:
        st.error(f"❌ 初始化失敗：{e}")
        st.info("💡 提示：首次登入需要較長時間，請耐心等候")
        return None


def get_port_display_options(crawler: PortWeatherCrawler) -> Dict[str, str]:
    if st.session_state.port_options_cache:
        return st.session_state.port_options_cache

    options = {}
    if not crawler or not hasattr(crawler, "port_list"):
        return options

    for port_code in crawler.port_list:
        try:
            port_info = crawler.get_port_info(port_code)
            if port_info:
                display_name = f"{port_code} - {port_info['port_name']} ({port_info['country']})"
                options[display_name] = port_code
            else:
                options[port_code] = port_code
        except Exception:
            options[port_code] = port_code

    st.session_state.port_options_cache = options
    return options


def fetch_and_analyze_ports(crawler: PortWeatherCrawler, port_codes: List[str]) -> Dict:
    results = {}
    parser = WeatherParser()
    analyzer = WeatherRiskAnalyzer()

    cookie_status = st.empty()
    
    if hasattr(crawler, "login_manager") and hasattr(crawler.login_manager, "verify_cookies"):
        cookie_status.info("🔍 正在驗證登入狀態...")
        
        if not crawler.login_manager.verify_cookies():
            cookie_status.warning("⚠️ Cookie 已過期，正在重新登入...")
            
            with st.spinner("🌐 正在重新取得登入憑證（約需 10-30 秒）..."):
                if hasattr(crawler, "refresh_cookies"):
                    success = crawler.refresh_cookies(headless=True)
                    
                    if not success:
                        cookie_status.error("❌ 無法更新 Cookie，請重新初始化系統")
                        return results
                    else:
                        cookie_status.success("✅ 登入憑證已更新")
                        time.sleep(1)
        else:
            cookie_status.success("✅ 登入狀態正常")
            time.sleep(0.5)
    
    cookie_status.empty()

    progress = st.progress(0)
    status = st.empty()

    for i, port_code in enumerate(port_codes):
        status.write(f"正在處理 **{port_code}**（{i+1}/{len(port_codes)}）")

        # 🔧 修正：先下載資料（這會確保資料庫有最新資料）
        success, message = crawler.fetch_port_data(port_code)
        
        if success or "已是最新" in message:
            # 🔧 修正：使用正確的 port_code 從資料庫讀取
            db_data = crawler.get_data_from_db(port_code)
            
            if db_data:
                content, issued_time, port_name = db_data
                
                # 🔧 新增：顯示除錯資訊
                print(f"✅ {port_code}: 成功讀取資料")
                print(f"   - 港口名稱: {port_name}")
                print(f"   - 發布時間: {issued_time}")
                print(f"   - 內容長度: {len(content)} 字元")
                print(f"   - 內容預覽: {content[:100]}...")
                
                try:
                    _, records, warnings = parser.parse_content(content)

                    risk_records = []
                    all_analyzed = []
                    max_level = 0

                    for r in records:
                        a = analyzer.analyze_record(r)
                        all_analyzed.append(a)
                        if a["risks"]:
                            risk_records.append(a)
                            max_level = max(max_level, a["risk_level"])

                    results[port_code] = {
                        "port_name": port_name,
                        "issued_time": issued_time,
                        "total_records": len(records),
                        "risk_records": risk_records,
                        "all_analyzed": all_analyzed,
                        "max_risk_level": max_level,
                        "all_records": records,
                        "warnings": warnings,
                        "status": "success",
                        "raw_content": content,  # 🔧 這裡應該是正確的內容
                    }
                    
                    print(f"   ✅ 解析成功：{len(records)} 筆記錄")
                    
                except Exception as e:
                    print(f"   ❌ 解析失敗: {e}")
                    results[port_code] = {
                        "status": "parse_error", 
                        "error": str(e),
                        "raw_content": content  # 🔧 即使解析失敗也保留原始內容
                    }
            else:
                print(f"❌ {port_code}: 資料庫無資料")
                results[port_code] = {"status": "no_data", "message": "無資料"}
        else:
            print(f"❌ {port_code}: 下載失敗 - {message}")
            results[port_code] = {"status": "fetch_error", "message": message}

        progress.progress((i + 1) / len(port_codes))

    status.empty()
    progress.empty()
    return results


def display_weather_table(records: List[WeatherRecord]):
    if not records:
        st.warning("無氣象資料")
        return

    analyzer = WeatherRiskAnalyzer()
    rows = []
    for r in records:
        a = analyzer.analyze_record(r)
        rows.append(
            {
                "時間": r.time.strftime("%m/%d %H:%M"),
                "風向": r.wind_direction,
                "風速 (kts)": f"{r.wind_speed:.1f}",
                "陣風 (kts)": f"{r.wind_gust:.1f}",
                "浪向": r.wave_direction,
                "浪高 (m)": f"{r.wave_height:.1f}",
                "週期 (s)": f"{r.wave_period:.1f}",
                "風險等級": WeatherRiskAnalyzer.get_risk_label(a["risk_level"]),
            }
        )

    df = pd.DataFrame(rows)

    def highlight(row):
        label = row["風險等級"]
        if "危險" in label:
            return ["background-color: rgba(230,0,18,0.08); font-weight: 650;"] * len(row)
        if "警告" in label:
            return ["background-color: rgba(251,146,60,0.10);"] * len(row)
        if "注意" in label:
            return ["background-color: rgba(245,158,11,0.10);"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(highlight, axis=1), use_container_width=True, height=420, hide_index=True)


def plot_port_trends(records: List[WeatherRecord], port_code: str = ""):
    """繪製港口趨勢圖，加入 port_code 作為唯一識別"""
    if not records:
        st.info("無資料可繪圖")
        return

    df = pd.DataFrame(
        [
            {
                "time": r.time,
                "wind_speed": r.wind_speed,
                "wind_gust": r.wind_gust,
                "wave_height": r.wave_height,
            }
            for r in records
        ]
    )

    common = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FFFFFF",
        height=360,
        margin=dict(l=10, r=10, t=56, b=10),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(color=BRAND["MUTED"])),
        yaxis=dict(showgrid=True, gridcolor="rgba(15,23,42,0.08)", zeroline=False, tickfont=dict(color=BRAND["MUTED"])),
        legend=dict(font=dict(color=BRAND["MUTED"])),
        hovermode="x unified",
    )

    # Wind
    fig_w = go.Figure()
    fig_w.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["wind_speed"],
            mode="lines",
            name="風速",
            line=dict(color=BRAND["NAVY"], width=2.4),
        )
    )
    fig_w.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["wind_gust"],
            mode="lines",
            name="陣風",
            line=dict(color=BRAND["RED"], width=2.0, dash="dot"),
        )
    )
    fig_w.add_hline(y=25, line_width=1, line_color="rgba(217,119,6,0.75)", annotation_text="注意 25", annotation_font_color="rgba(217,119,6,0.95)")
    fig_w.add_hline(y=30, line_width=1, line_color="rgba(234,88,12,0.75)", annotation_text="警告 30", annotation_font_color="rgba(234,88,12,0.95)")
    fig_w.update_layout(title=dict(text="風速趨勢（knots）", font=dict(color=BRAND["TEXT"], size=16, family="Inter")), **common)
    
    # 加入唯一的 key
    st.plotly_chart(fig_w, use_container_width=True, key=f"wind_chart_{port_code}")

    # Wave
    fig_s = go.Figure()
    fig_s.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["wave_height"],
            mode="lines",
            name="浪高",
            line=dict(color=BRAND["SKY"], width=2.4),
        )
    )
    fig_s.add_hline(y=2.0, line_width=1, line_color="rgba(217,119,6,0.75)", annotation_text="注意 2.0", annotation_font_color="rgba(217,119,6,0.95)")
    fig_s.add_hline(y=2.5, line_width=1, line_color="rgba(234,88,12,0.75)", annotation_text="警告 2.5", annotation_font_color="rgba(234,88,12,0.95)")
    fig_s.update_layout(title=dict(text="浪高趨勢（meter）", font=dict(color=BRAND["TEXT"], size=16, family="Inter")), **common)
    
    # 加入唯一的 key
    st.plotly_chart(fig_s, use_container_width=True, key=f"wave_chart_{port_code}")


def display_port_detail(port_code: str, data: Dict):
    st.markdown(
        f"""
        <div class="info-card">
          <h2 style="margin:0 0 8px 0;">⚓ {port_code} - {data['port_name']}</h2>
          <div class="info-meta">
            <span>📅 發布：{data['issued_time']}</span>
            <span class="divider-dot"></span>
            <span>📊 記錄：{data['total_records']} 筆</span>
            <span class="divider-dot"></span>
            {WeatherRiskAnalyzer.get_risk_badge(data['max_risk_level'])}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    view = st.radio(
        "view",
        ["📈 趨勢圖表", "📋 完整資料表", "⚠️ 警戒時段", "📄 原始資料"],
        horizontal=True,
        label_visibility="collapsed",
        key=f"view_{port_code}",
    )

    st.markdown("---")

    if view == "📈 趨勢圖表":
        # 傳入 port_code 作為唯一識別
        plot_port_trends(data["all_records"], port_code)

    elif view == "📋 完整資料表":
        display_weather_table(data["all_records"])

    elif view == "⚠️ 警戒時段":
        st.subheader("警戒時段詳情")
        if data["risk_records"]:
            for i, r in enumerate(data["risk_records"], 1):
                time_str = r["time"].strftime("%Y-%m-%d %H:%M")
                with st.expander(f"{time_str}｜{r['risks'][0]}", expanded=(i <= 3)):
                    st.markdown("**觸發條件：**")
                    for item in r["risks"]:
                        st.markdown(f"- {item}")
                    c1, c2 = st.columns(2)
                    with c1:
                        st.metric("風速", f"{r['wind_speed']:.1f} kts")
                        st.metric("陣風", f"{r['wind_gust']:.1f} kts")
                    with c2:
                        st.metric("浪高", f"{r['wave_height']:.1f} m")
                        st.metric("浪向", f"{r['wave_direction']}")
        else:
            st.markdown(
                """
                <div class="card pad" style="border-left: 4px solid #16A34A;">
                  <div style="font-weight:900; margin-bottom:6px;">✅ 此港口無警戒時段</div>
                  <div style="color: var(--muted);">目前預報區間未偵測到注意等級以上風險。</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    else:
        st.text_area("WNI 原始資料", value=data["raw_content"], height=520, key=f"raw_data_{port_code}")


def display_risk_summary(results: Dict):
    analyzer = WeatherRiskAnalyzer()
    risk_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    total_ports = 0
    high_risk = []

    for code, data in results.items():
        if data.get("status") == "success":
            total_ports += 1
            lvl = data.get("max_risk_level", 0)
            risk_counts[lvl] += 1
            if lvl >= 2:
                high_risk.append((code, data))

    st.markdown(
        f"""
        <div class="topbar">
          <div class="topbar-content">
            <div class="h">⚓ 港口氣象監控總覽</div>
            <div class="p">
              <span class="chip">📊 監控港口：{total_ports} Ports</span>
              <span class="chip">🕒 Last update: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M') if st.session_state.last_update else '—'}</span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("🔴 危險 Danger", risk_counts[3])
    with c2:
        st.metric("🟠 警告 Warning", risk_counts[2])
    with c3:
        st.metric("🟡 注意 Caution", risk_counts[1])
    with c4:
        st.metric("🟢 安全 Safe", risk_counts[0])

    st.markdown("### 🎯 重點關注（Warning / Danger）")
    if high_risk:
        high_risk.sort(key=lambda x: x[1]["max_risk_level"], reverse=True)
        for code, data in high_risk:
            color = analyzer.get_risk_color(data["max_risk_level"])
            label = analyzer.get_risk_label(data["max_risk_level"])
            cnt = len(data["risk_records"])
            st.markdown(
                f"""
                <div class="port-alert-card" style="border-left: 5px solid {color};">
                  <div style="display:flex; justify-content:space-between; gap:12px; align-items:center;">
                    <h4 class="title">⚓ {code} - {data['port_name']}</h4>
                    <span class="pill" style="border-color: {color}; color:{color}; background: rgba(230,0,18,0.04);">
                      {label}
                    </span>
                  </div>
                  <p class="meta">🔴 高風險時段：<b>{cnt}</b> ｜ 📅 發布：{data['issued_time']}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            """
            <div class="card pad" style="border-left: 4px solid #16A34A;">
              <div style="font-weight:900; margin-bottom:6px;">✅ 目前無 Warning/Danger 港口</div>
              <div style="color: var(--muted);">整體風險落在安全或注意等級。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================
# Main
# =========================
def main():
    # Sidebar
    with st.sidebar:
        st.markdown(
            f"""
            <div class="sidebar-brand">
              <div class="sidebar-brand-content">
                <div class="logo-container">
                  <div class="logo-wrapper">
                    <img src="{LOGO_URL}" alt="Wan Hai Lines Logo" />
                  </div>
                  <div class="text-content">
                    <div class="title">Wan Hai Lines</div>
                    <div class="sub">Marine Technology Division<br/>風險管理課</div>
                  </div>
                </div>
                <div class="badge">⚓ Corporate Dashboard</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("⚙️ 系統設定")

        with st.expander("🔐 帳號設定", expanded=not st.session_state.login_configured):
            username = st.text_input(
                "帳號",
                value=st.session_state.aedyn_username,
                placeholder="請輸入公司個人信箱（例如：name@wanhai.com）",
                key="username"
            )

            password = st.text_input(
                "密碼",
                value=st.session_state.aedyn_password,
                type="password",
                placeholder="預設為 wanhai888",
                key="password"
            )

            st.caption("帳號請填公司個人信箱；密碼預設為 **wanhai888**（如已變更請輸入新密碼）。")
            
            st.info("💡 **首次登入說明**\n\n首次登入或 Cookie 過期時，系統需要約 10-30 秒進行以下步驟：\n\n"
                   "1. 啟動瀏覽器\n"
                   "2. 連接 WNI 登入頁面\n"
                   "3. 自動輸入帳密\n"
                   "4. 取得並儲存 Cookie\n\n"
                   "完成後，Cookie 將保存 24 小時，期間無需重新登入。")

            if st.button("儲存並登入", use_container_width=True):
                if username and password:
                    st.session_state.aedyn_username = username
                    st.session_state.aedyn_password = password
                    st.session_state.login_configured = True
                    st.success("✅ 已儲存帳號設定")
                else:
                    st.error("❌ 請輸入完整帳號密碼")

        if st.session_state.login_configured:
            if not st.session_state.crawler:
                if st.button("🚀 初始化系統", type="primary", use_container_width=True):
                    crawler = init_crawler(st.session_state.aedyn_username, st.session_state.aedyn_password)
                    if crawler:
                        st.session_state.crawler = crawler
                        st.session_state.crawler_initialized = True
                        st.success("✅ 系統已就緒")
                        time.sleep(1)
                        st.rerun()
            else:
                if hasattr(st.session_state.crawler, 'login_manager'):
                    cookie_age = None
                    if st.session_state.crawler.login_manager.cookie_timestamp:
                        cookie_age = datetime.now() - st.session_state.crawler.login_manager.cookie_timestamp
                        hours = int(cookie_age.total_seconds() / 3600)
                        
                        if hours < 24:
                            st.success(f"🔐 登入狀態：正常（已使用 {hours} 小時）")
                        else:
                            st.warning(f"⚠️ Cookie 已過期（{hours} 小時），下次抓取時將自動更新")
                
                if st.button("🔄 手動更新登入狀態", use_container_width=True):
                    with st.spinner("正在更新登入憑證..."):
                        if st.session_state.crawler.refresh_cookies(headless=True):
                            st.success("✅ 登入憑證已更新")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ 更新失敗")

            st.markdown("---")
            st.subheader("📡 資料抓取")

            mode = st.radio("範圍", ["全部港口", "指定港口"], horizontal=True)

            port_codes = []
            if st.session_state.crawler:
                if mode == "全部港口":
                    port_codes = st.session_state.crawler.port_list
                    st.caption(f"共 {len(port_codes)} 個港口")
                else:
                    opts = get_port_display_options(st.session_state.crawler)
                    sel = st.multiselect("選擇港口", list(opts.keys()))
                    port_codes = [opts[k] for k in sel]

                if port_codes and st.button("▶️ 開始更新資料", type="primary", use_container_width=True):
                    with st.spinner("抓取並分析中..."):
                        res = fetch_and_analyze_ports(st.session_state.crawler, port_codes)
                        st.session_state.analysis_results = res
                        st.session_state.last_update = datetime.now()
                        st.rerun()
            if st.button("🔍 檢查資料庫"):
                conn = sqlite3.connect('WNI_port_weather.db')
                df = pd.read_sql_query("SELECT whl_port_code, port_name, station_id, issued_time, LENGTH(content) as content_length FROM weather_data ORDER BY download_time DESC LIMIT 10", conn)
                st.dataframe(df)
                conn.close()
            if st.session_state.last_update:
                st.caption(f"🕒 最後更新：{st.session_state.last_update.strftime('%Y-%m-%d %H:%M')}")

    # Main content
    if not st.session_state.analysis_results:
        st.markdown(
            """
            <div class="hero">
              <h1>⚓ 海技部-港口氣象監控系統</h1>
              <div class="sub">
                以 WNI 氣象資訊為基礎，針對未來 48 小時港口風力進行監控，顯示整體風險等級、趨勢圖與警戒時段，協助船長提早進行風險評估。
                請先於左側輸入 WNI 登入資訊並初始化系統。
              </div>

              <div class="hero-grid">
                <div class="card pad">
                  <h3 style="margin:0 0 8px 0;">🌐 全船隊監控</h3>
                  <div style="color: var(--muted); line-height:1.6;">
                    快速掌握所有港口風險分布與重點關注名單
                  </div>
                </div>
                <div class="card pad">
                  <h3 style="margin:0 0 8px 0;">⚡ 即時風險預警</h3>
                  <div style="color: var(--muted); line-height:1.6;">
                    以注意/警告/危險等級呈現，降低判讀成本
                  </div>
                </div>
                <div class="card pad">
                  <h3 style="margin:0 0 8px 0;">📊 視覺化圖表</h3>
                  <div style="color: var(--muted); line-height:1.6;">
                    風速、陣風、浪高趨勢一眼看懂，決策更快
                  </div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    results = st.session_state.analysis_results

    # Overview
    display_risk_summary(results)
    st.markdown("")

    # Details
    st.markdown("## 📋 詳細分析")

    colA, colB = st.columns([1, 2])
    with colA:
        filter_mode = st.selectbox("顯示模式", ["全部港口", "僅警戒港口（≥ 注意）", "僅 Warning/Danger", "單一港口"])

    success_ports = {k: v for k, v in results.items() if v.get("status") == "success"}

    if not success_ports:
        st.error("本次沒有成功解析的港口資料")
        return

    if filter_mode == "單一港口":
        opts = {f"{k} - {v['port_name']}": k for k, v in success_ports.items()}
        with colB:
            picked = st.selectbox("選擇港口", list(opts.keys()))
        code = opts[picked]
        display_port_detail(code, success_ports[code])

    elif filter_mode == "僅 Warning/Danger":
        subset = {k: v for k, v in success_ports.items() if v.get("max_risk_level", 0) >= 2}
        if not subset:
            st.info("目前無 Warning/Danger 港口")
            return
        items = sorted(subset.items(), key=lambda x: x[1]["max_risk_level"], reverse=True)
        tabs = st.tabs([f"{k}｜{WeatherRiskAnalyzer.get_risk_label(v['max_risk_level'])}" for k, v in items])
        for tab, (code, data) in zip(tabs, items):
            with tab:
                display_port_detail(code, data)

    elif filter_mode == "僅警戒港口（≥ 注意）":
        subset = {k: v for k, v in success_ports.items() if v.get("max_risk_level", 0) >= 1}
        if not subset:
            st.info("目前無警戒港口")
            return
        items = sorted(subset.items(), key=lambda x: x[1]["max_risk_level"], reverse=True)
        tabs = st.tabs([f"{k}｜{WeatherRiskAnalyzer.get_risk_label(v['max_risk_level'])}" for k, v in items])
        for tab, (code, data) in zip(tabs, items):
            with tab:
                display_port_detail(code, data)

    else:
        items = list(success_ports.items())
        tabs = st.tabs([k for k, _ in items])
        for tab, (code, data) in zip(tabs, items):
            with tab:
                display_port_detail(code, data)
    

if __name__ == "__main__":
    main()