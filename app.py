# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from typing import List, Dict

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

# Logo（請換成你可用的資源）
LOGO_URL = "data:image/jpeg;base64,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"


# =========================
# CSS (Wan Hai-like Corporate Style)
# =========================
def load_css():
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

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

          --shadow-sm: 0 1px 2px rgba(2, 6, 23, 0.06);
          --shadow-md: 0 14px 40px rgba(2, 6, 23, 0.10);
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
            radial-gradient(1000px 520px at 18% 0%, rgba(11,46,91,0.10), transparent 60%),
            radial-gradient(900px 520px at 88% 0%, rgba(230,0,18,0.06), transparent 62%),
            linear-gradient(180deg, #FFFFFF 0%, var(--bg) 40%, var(--bg) 100%);
        }}

        .block-container {{
          max-width: 1240px;
          padding-top: 1.2rem;
          padding-bottom: 2.2rem;
        }}

        h1,h2,h3,h4 {{
          color: var(--text) !important;
          font-weight: 850 !important;
          letter-spacing: -0.02em;
        }}
        p, li, label, span {{ color: var(--text); }}
        .stCaption, [data-testid="stCaptionContainer"] {{
          color: var(--muted) !important;
        }}
        hr {{ border-color: rgba(15, 23, 42, 0.10) !important; }}

        /* =========================
           Sidebar：白底 + 上方品牌條
        ========================== */
        section[data-testid="stSidebar"] {{
          background: #FFFFFF;
          border-right: 1px solid rgba(15, 23, 42, 0.08);
        }}
        section[data-testid="stSidebar"] .block-container {{
          padding-top: 1.0rem;
        }}

        /* Sidebar top brand bar */
        .sidebar-brand {{
          border-radius: var(--radius);
          padding: 14px 14px;
          background: linear-gradient(135deg, rgba(11,46,91,1), rgba(10,35,66,1));
          box-shadow: var(--shadow-md);
          color: #fff;
          margin-bottom: 14px;
        }}
        .sidebar-brand .title {{
          margin: 0;
          font-weight: 900;
          font-size: 1.0rem;
          color: #fff;
        }}
        .sidebar-brand .sub {{
          margin: 6px 0 0 0;
          font-size: 0.82rem;
          color: rgba(255,255,255,0.78);
          line-height: 1.35;
        }}
        .sidebar-brand .badge {{
          display: inline-flex;
          align-items:center;
          gap:8px;
          margin-top: 10px;
          padding: 6px 10px;
          border-radius: 999px;
          background: rgba(255,255,255,0.12);
          border: 1px solid rgba(255,255,255,0.18);
          color: rgba(255,255,255,0.92);
          font-size: 0.80rem;
          font-weight: 800;
        }}

        /* Inputs / Select：明亮官網風 */
        .stTextInput input, .stNumberInput input, .stTextArea textarea {{
          border-radius: 12px !important;
          border: 1px solid rgba(15, 23, 42, 0.14) !important;
          background: #FFFFFF !important;
          color: var(--text) !important;
          box-shadow: 0 1px 0 rgba(2,6,23,0.02) !important;
        }}
        .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {{
          border-color: rgba(31,111,235,0.65) !important;
          box-shadow: 0 0 0 4px rgba(31,111,235,0.12) !important;
          outline: none !important;
        }}
        .stTextInput input::placeholder, .stTextArea textarea::placeholder {{
          color: rgba(91,102,122,0.70) !important;
        }}

        /* Autofill：避免瀏覽器把 input 變成奇怪顏色 */
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
        }}
        [data-baseweb="select"] > div:focus-within {{
          border-color: rgba(31,111,235,0.65) !important;
          box-shadow: 0 0 0 4px rgba(31,111,235,0.12) !important;
        }}

        /* Buttons：官網 CTA（紅）+ 次要（白） */
        .stButton > button {{
          border-radius: 12px;
          border: 1px solid rgba(15, 23, 42, 0.14);
          background: #FFFFFF;
          color: var(--text);
          font-weight: 850;
          padding: 0.62rem 0.95rem;
          transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, background .12s ease;
          box-shadow: var(--shadow-sm);
        }}
        .stButton > button:hover {{
          transform: translateY(-1px);
          border-color: rgba(15, 23, 42, 0.20);
          box-shadow: var(--shadow-md);
        }}
        .stButton > button[kind="primary"] {{
          background: linear-gradient(180deg, var(--red), #C80010);
          border-color: rgba(230,0,18,0.60);
          color: #FFFFFF;
          box-shadow: 0 16px 44px rgba(230,0,18,0.18);
        }}
        .stButton > button[kind="primary"]:hover {{
          box-shadow: 0 20px 60px rgba(230,0,18,0.22);
        }}

        /* Cards / Panels */
        .card {{
          background: var(--card);
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius);
          box-shadow: var(--shadow-sm);
        }}
        .card.pad {{ padding: 16px 18px; }}
        .card:hover {{
          border-color: rgba(15, 23, 42, 0.14);
          box-shadow: var(--shadow-md);
        }}

        .topbar {{
          background: linear-gradient(135deg, rgba(11,46,91,1), rgba(10,35,66,1));
          border-radius: var(--radius);
          box-shadow: var(--shadow-md);
          padding: 16px 18px;
          margin-bottom: 14px;
          color: #fff;
        }}
        .topbar .h {{
          margin: 0;
          font-size: 1.25rem;
          font-weight: 900;
          color: #fff;
        }}
        .topbar .p {{
          margin: 6px 0 0 0;
          color: rgba(255,255,255,0.78);
          font-size: 0.92rem;
        }}
        .topbar .chip {{
          display:inline-flex;
          align-items:center;
          gap:8px;
          padding: 6px 10px;
          border-radius: 999px;
          background: rgba(255,255,255,0.12);
          border: 1px solid rgba(255,255,255,0.18);
          color: rgba(255,255,255,0.92);
          font-size: 0.82rem;
          font-weight: 850;
          margin-left: 10px;
        }}

        /* Info card (Port header) */
        .info-card {{
          background: #FFFFFF;
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius);
          padding: 18px 18px;
          box-shadow: var(--shadow-sm);
          margin-bottom: 14px;
        }}
        .info-meta {{
          display:flex;
          flex-wrap: wrap;
          gap: 12px;
          align-items:center;
          color: var(--muted);
          font-size: 0.92rem;
        }}
        .divider-dot {{
          width: 4px;
          height: 4px;
          border-radius: 999px;
          background: rgba(91,102,122,0.55);
          display:inline-block;
        }}

        /* Risk badge */
        .risk-badge {{
          padding: 6px 12px;
          border-radius: 999px;
          font-size: 0.84em;
          font-weight: 900;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 1px solid transparent;
          white-space: nowrap;
        }}
        .risk-0 {{ background: rgba(34,197,94,0.12); color: #0F5132; border-color: rgba(34,197,94,0.22); }}
        .risk-1 {{ background: rgba(245,158,11,0.12); color: #7A4B00; border-color: rgba(245,158,11,0.22); }}
        .risk-2 {{ background: rgba(251,146,60,0.12); color: #7A2E00; border-color: rgba(251,146,60,0.22); }}
        .risk-3 {{ background: rgba(230,0,18,0.10); color: #8A0010; border-color: rgba(230,0,18,0.22); }}

        /* Alert list card */
        .port-alert-card {{
          background: #FFFFFF;
          border: 1px solid rgba(15, 23, 42, 0.10);
          border-radius: var(--radius-sm);
          padding: 14px 16px;
          margin-bottom: 10px;
          box-shadow: var(--shadow-sm);
        }}
        .port-alert-card .title {{
          margin: 0;
          font-weight: 900;
        }}
        .port-alert-card .meta {{
          margin: 6px 0 0 0;
          color: var(--muted);
          font-size: 0.92rem;
        }}
        .pill {{
          padding: 6px 10px;
          border-radius: 999px;
          font-size: 0.82rem;
          font-weight: 900;
          border: 1px solid rgba(15,23,42,0.14);
          background: rgba(11,46,91,0.04);
          color: var(--navy);
          white-space: nowrap;
        }}

        /* Metrics */
        div[data-testid="stMetric"] {{
          background: #FFFFFF;
          border: 1px solid rgba(15, 23, 42, 0.10);
          padding: 14px 16px;
          border-radius: var(--radius);
          box-shadow: var(--shadow-sm);
        }}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{
          color: var(--muted) !important;
          font-weight: 800 !important;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
          color: var(--text) !important;
          font-weight: 900 !important;
          letter-spacing: -0.01em;
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
          font-weight: 850 !important;
          color: rgba(91,102,122,0.95) !important;
        }}
        [data-testid="stTabs"] button[aria-selected="true"] {{
          color: var(--navy) !important;
        }}

        /* Plotly modebar */
        .js-plotly-plot .plotly .modebar {{
          opacity: 0.20;
          transition: opacity .15s ease;
        }}
        .js-plotly-plot:hover .plotly .modebar {{
          opacity: 0.95;
        }}

        /* Welcome hero */
        .hero {{
          max-width: 980px;
          margin: 14px auto 0 auto;
          text-align: center;
          padding: 24px 10px 10px 10px;
        }}
        .hero h1 {{
          margin: 0 0 8px 0;
          font-size: 2.05rem;
        }}
        .hero .sub {{
          margin: 0 auto;
          max-width: 740px;
          color: var(--muted);
          font-size: 1.02rem;
          line-height: 1.6;
        }}
        .hero-grid {{
          margin-top: 18px;
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 14px;
        }}
        @media (max-width: 920px) {{
          .hero-grid {{ grid-template-columns: 1fr; }}
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
        # 官網風：Danger 用品牌紅，其他用較穩重色
        return {0: "#16A34A", 1: "#D97706", 2: "#EA580C", 3: BRAND["RED"]}.get(risk_level, "#64748B")

    @classmethod
    def get_risk_badge(cls, risk_level: int) -> str:
        return f'<span class="risk-badge risk-{risk_level}">{cls.get_risk_label(risk_level)}</span>'


# =========================
# Functions (保持你原本行為，這裡做相容寫法)
# =========================
def init_crawler(username: str, password: str):
    """
    你的原始實作可能會動態替換模組常數並 refresh cookies。
    這裡用「盡量相容」寫法：若 crawler 有 login_manager/refresh_cookies 就沿用。
    """
    try:
        import weather_crawler as wc  # 你的模組

        original_username = getattr(wc, "AEDYN_USERNAME", None)
        original_password = getattr(wc, "AEDYN_PASSWORD", None)

        if original_username is not None:
            wc.AEDYN_USERNAME = username
        if original_password is not None:
            wc.AEDYN_PASSWORD = password

        crawler = PortWeatherCrawler(auto_login=False)

        # 還原
        if original_username is not None:
            wc.AEDYN_USERNAME = original_username
        if original_password is not None:
            wc.AEDYN_PASSWORD = original_password

        # 若有 login_manager，填入帳密
        if hasattr(crawler, "login_manager"):
            crawler.login_manager.username = username
            crawler.login_manager.password = password
            if hasattr(crawler.login_manager, "verify_cookies") and not crawler.login_manager.verify_cookies():
                st.warning("Cookie 無效，正在重新登入...")
                if hasattr(crawler, "refresh_cookies"):
                    crawler.refresh_cookies(headless=True)

        return crawler
    except Exception as e:
        st.error(f"初始化失敗：{e}")
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

    # verify cookie if available
    if hasattr(crawler, "login_manager") and hasattr(crawler.login_manager, "verify_cookies"):
        if not crawler.login_manager.verify_cookies():
            st.warning("Cookie 已過期，重新登入中...")
            if hasattr(crawler, "refresh_cookies") and not crawler.refresh_cookies(headless=True):
                st.error("無法更新 Cookie")
                return results

    progress = st.progress(0)
    status = st.empty()

    for i, port_code in enumerate(port_codes):
        status.write(f"正在處理 **{port_code}**（{i+1}/{len(port_codes)}）")

        success, message = crawler.fetch_port_data(port_code)
        if success:
            db_data = crawler.get_data_from_db(port_code)
            if db_data:
                content, issued_time, port_name = db_data
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
                        "raw_content": content,
                    }
                except Exception as e:
                    results[port_code] = {"status": "parse_error", "error": str(e)}
            else:
                results[port_code] = {"status": "no_data", "message": "無資料"}
        else:
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

    # 官網風：淡淡上色、不刺眼
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


def plot_port_trends(records: List[WeatherRecord]):
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
    st.plotly_chart(fig_w, use_container_width=True)

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
    st.plotly_chart(fig_s, use_container_width=True)


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
        plot_port_trends(data["all_records"])

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
        st.text_area("WNI 原始資料", value=data["raw_content"], height=520)


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
          <div class="h">港口氣象監控總覽</div>
          <div class="p">
            監控港口：<span class="chip">⚓ {total_ports} Ports</span>
            <span class="chip">🕒 Last update: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M') if st.session_state.last_update else '—'}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("危險 Danger", risk_counts[3])
    with c2:
        st.metric("警告 Warning", risk_counts[2])
    with c3:
        st.metric("注意 Caution", risk_counts[1])
    with c4:
        st.metric("安全 Safe", risk_counts[0])

    st.markdown("### 重點關注（Warning / Danger）")
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
                    <h4 class="title">{code} - {data['port_name']}</h4>
                    <span class="pill" style="border-color: {color}; color:{color}; background: rgba(230,0,18,0.04);">
                      {label}
                    </span>
                  </div>
                  <p class="meta">🔴 高風險時段：<b>{cnt}</b> ｜ 發布：{data['issued_time']}</p>
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
              <div style="display:flex; align-items:center; gap:10px;">
                <img src="{LOGO_URL}" style="width:34px; height:34px; border-radius:10px; background: rgba(255,255,255,0.10); padding:6px;" />
                <div>
                  <div class="title">Wan Hai Marine Technology Division</div>
                  <div class="sub">風險管理課<br/>Fleet Risk Management Department</div>
                </div>
              </div>
              <div class="badge">⚓ Corporate Dashboard</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("系統設定")

        with st.expander("帳號設定", expanded=not st.session_state.login_configured):
            username = st.text_input("帳號", value=st.session_state.aedyn_username, key="公司個人信箱")
            password = st.text_input("密碼", value=st.session_state.aedyn_password, type="password", key="預設為wanhai888")

            if st.button("儲存並登入", use_container_width=True):
                if username and password:
                    st.session_state.aedyn_username = username
                    st.session_state.aedyn_password = password
                    st.session_state.login_configured = True
                    st.success("已儲存")
                else:
                    st.error("請輸入完整帳號密碼")

        if st.session_state.login_configured:
            if not st.session_state.crawler:
                if st.button("初始化系統", type="primary", use_container_width=True):
                    with st.spinner("系統啟動中..."):
                        crawler = init_crawler(st.session_state.aedyn_username, st.session_state.aedyn_password)
                        if crawler:
                            st.session_state.crawler = crawler
                            st.session_state.crawler_initialized = True
                            st.rerun()

            st.markdown("---")
            st.subheader("資料抓取")

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

                if port_codes and st.button("開始更新資料", type="primary", use_container_width=True):
                    with st.spinner("抓取並分析中..."):
                        res = fetch_and_analyze_ports(st.session_state.crawler, port_codes)
                        st.session_state.analysis_results = res
                        st.session_state.last_update = datetime.now()
                        st.rerun()

            if st.session_state.last_update:
                st.caption(f"最後更新：{st.session_state.last_update.strftime('%Y-%m-%d %H:%M')}")

    # Main content
    if not st.session_state.analysis_results:
        st.markdown(
            """
            <div class="hero">
              <h1>⚓ 海技部-港口氣象監控系統</h1>
              <div class="sub">
                以WNI氣象資訊基礎，針對未來48Hrs港口風力監控，顯示整體風險等級、趨勢圖與警戒時段，協助船長提早進行風險評估。
                請先於左側輸入WNI登入資訊並初始化系統。
              </div>

              <div class="hero-grid">
                <div class="card pad">
                  <h3 style="margin:0 0 6px 0; color: var(--navy);">全船隊監控</h3>
                  <div style="color: var(--muted); line-height:1.6;">
                    快速掌握所有港口風險分布與重點關注名單
                  </div>
                </div>
                <div class="card pad">
                  <h3 style="margin:0 0 6px 0; color: var(--navy);">即時風險預警</h3>
                  <div style="color: var(--muted); line-height:1.6;">
                    以注意/警告/危險等級呈現，降低判讀成本
                  </div>
                </div>
                <div class="card pad">
                  <h3 style="margin:0 0 6px 0; color: var(--navy);">視覺化圖表</h3>
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
    st.markdown("## 詳細分析")

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
        # 全部港口
        items = list(success_ports.items())
        tabs = st.tabs([k for k, _ in items])
        for tab, (code, data) in zip(tabs, items):
            with tab:
                display_port_detail(code, data)


if __name__ == "__main__":
    main()