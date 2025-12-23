# app.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
from typing import List, Dict, Tuple
import re
import os
import numpy as np

# 導入你的模組
from weather_crawler import PortWeatherCrawler, WeatherDatabase, AedynLoginManager
from weather_parser import WeatherParser, WeatherRecord

# ================= 設定 =================
st.set_page_config(
    page_title="港口氣象監控系統",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= CSS 美化工程 =================
def load_css():
    st.markdown("""
        <style>
        /* 全局字體設定 */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        
        html, body, [class*="css"]  {
            font-family: 'Inter', 'Microsoft JhengHei', sans-serif;
        }
        
        /* 背景色調整 - 讓主區域呈現淡淡的灰色，突顯白色卡片 */
        .stApp {
            background-color: #f8f9fa;
        }
        
        /* 側邊欄美化 */
        section[data-testid="stSidebar"] {
            background-color: #ffffff;
            border-right: 1px solid #e9ecef;
        }
        
        /* 標題樣式 */
        h1, h2, h3 {
            color: #2c3e50;
            font-weight: 700;
        }
        
        /* Metrics 卡片化 - 這是質感提升的關鍵 */
        div[data-testid="stMetric"] {
            background-color: #ffffff;
            border: 1px solid #e9ecef;
            padding: 15px 20px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.04);
            transition: transform 0.2s ease;
        }
        div[data-testid="stMetric"]:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0, 0, 0, 0.08);
        }
        
        /* 自定義風險 Badge */
        .risk-badge {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
            display: inline-block;
        }
        .risk-0 { background-color: #d4edda; color: #155724; }
        .risk-1 { background-color: #fff3cd; color: #856404; }
        .risk-2 { background-color: #ffeeba; color: #856404; border: 1px solid #ffdf7e;}
        .risk-3 { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb;}
        
        /* 資訊卡片容器 */
        .info-card {
            background-color: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            margin-bottom: 20px;
        }
        
        /* 調整表格樣式 */
        .stDataFrame {
            border: 1px solid #e9ecef;
            border-radius: 8px;
            overflow: hidden;
        }
        
        /* 調整 Plotly 圖表容器 */
        .js-plotly-plot .plotly .modebar {
            opacity: 0.5;
        }
        </style>
    """, unsafe_allow_html=True)

load_css()

# ================= 風險評估類別 =================
class WeatherRiskAnalyzer:
    """氣象風險分析器"""
    
    THRESHOLDS = {
        'wind_caution': 25, 'wind_warning': 30, 'wind_danger': 40,
        'gust_caution': 35, 'gust_warning': 40, 'gust_danger': 50,
        'wave_caution': 2.0, 'wave_warning': 2.5, 'wave_danger': 4.0,
    }
    
    @classmethod
    def analyze_record(cls, record: WeatherRecord) -> Dict:
        """分析單筆氣象記錄"""
        risks = []
        risk_level = 0
        
        # 風速判斷
        if record.wind_speed >= cls.THRESHOLDS['wind_danger']:
            risks.append(f"⛔ 風速危險: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_speed >= cls.THRESHOLDS['wind_warning']:
            risks.append(f"⚠️ 風速警告: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_speed >= cls.THRESHOLDS['wind_caution']:
            risks.append(f"⚡ 風速注意: {record.wind_speed:.1f} kts")
            risk_level = max(risk_level, 1)
        
        # 陣風判斷
        if record.wind_gust >= cls.THRESHOLDS['gust_danger']:
            risks.append(f"⛔ 陣風危險: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_gust >= cls.THRESHOLDS['gust_warning']:
            risks.append(f"⚠️ 陣風警告: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_gust >= cls.THRESHOLDS['gust_caution']:
            risks.append(f"⚡ 陣風注意: {record.wind_gust:.1f} kts")
            risk_level = max(risk_level, 1)
        
        # 浪高判斷
        if record.wave_height >= cls.THRESHOLDS['wave_danger']:
            risks.append(f"⛔ 浪高危險: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 3)
        elif record.wave_height >= cls.THRESHOLDS['wave_warning']:
            risks.append(f"⚠️ 浪高警告: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 2)
        elif record.wave_height >= cls.THRESHOLDS['wave_caution']:
            risks.append(f"⚡ 浪高注意: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 1)
        
        return {
            'risk_level': risk_level,
            'risks': risks,
            'time': record.time,
            'wind_speed': record.wind_speed,
            'wind_gust': record.wind_gust,
            'wave_height': record.wave_height,
            'wind_direction': record.wind_direction,
            'wave_direction': record.wave_direction
        }
    
    @classmethod
    def get_risk_color(cls, risk_level: int) -> str:
        colors = {0: '#28a745', 1: '#ffc107', 2: '#fd7e14', 3: '#dc3545'}
        return colors.get(risk_level, '#6c757d')
    
    @classmethod
    def get_risk_label(cls, risk_level: int) -> str:
        labels = {0: '安全 Safe', 1: '注意 Caution', 2: '警告 Warning', 3: '危險 Danger'}
        return labels.get(risk_level, '未知 Unknown')
        
    @classmethod
    def get_risk_badge(cls, risk_level: int) -> str:
        """回傳 HTML Badge"""
        label = cls.get_risk_label(risk_level)
        return f'<span class="risk-badge risk-{risk_level}">{label}</span>'


# ================= 初始化 Session State (保持不變) =================
if 'crawler' not in st.session_state: st.session_state.crawler = None
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = {}
if 'last_update' not in st.session_state: st.session_state.last_update = None
if 'selected_ports' not in st.session_state: st.session_state.selected_ports = []
if 'port_options_cache' not in st.session_state: st.session_state.port_options_cache = {}
if 'crawler_initialized' not in st.session_state: st.session_state.crawler_initialized = False
if 'aedyn_username' not in st.session_state: st.session_state.aedyn_username = ""
if 'aedyn_password' not in st.session_state: st.session_state.aedyn_password = ""
if 'login_configured' not in st.session_state: st.session_state.login_configured = False


# ================= 主要功能函數 (邏輯部分保持不變) =================
def init_crawler(username: str, password: str):
    try:
        from weather_crawler import PortWeatherCrawler
        import weather_crawler
        
        original_username = weather_crawler.AEDYN_USERNAME
        original_password = weather_crawler.AEDYN_PASSWORD
        weather_crawler.AEDYN_USERNAME = username
        weather_crawler.AEDYN_PASSWORD = password
        
        crawler = PortWeatherCrawler(auto_login=False)
        weather_crawler.AEDYN_USERNAME = original_username
        weather_crawler.AEDYN_PASSWORD = original_password
        
        crawler.login_manager.username = username
        crawler.login_manager.password = password
        
        if not crawler.login_manager.verify_cookies():
            st.warning("⚠️ Cookie 無效，正在重新登入...")
            crawler.refresh_cookies(headless=True)
        return crawler
    except Exception as e:
        st.error(f"❌ 初始化失敗: {e}")
        import traceback
        st.code(traceback.format_exc())
        return None

def get_port_display_options(crawler: PortWeatherCrawler) -> Dict[str, str]:
    if st.session_state.port_options_cache: return st.session_state.port_options_cache
    options = {}
    if not crawler or not hasattr(crawler, 'port_list'): return options
    for port_code in crawler.port_list:
        try:
            port_info = crawler.get_port_info(port_code)
            if port_info:
                display_name = f"{port_code} - {port_info['port_name']} ({port_info['country']})"
                options[display_name] = port_code
        except Exception as e:
            options[port_code] = port_code
            continue
    st.session_state.port_options_cache = options
    return options

def fetch_and_analyze_ports(crawler: PortWeatherCrawler, port_codes: List[str]) -> Dict:
    results = {}
    parser = WeatherParser()
    analyzer = WeatherRiskAnalyzer()
    
    if not crawler.login_manager.verify_cookies():
        st.warning("⚠️ Cookie 已過期，重新登入中...")
        if not crawler.refresh_cookies(headless=True):
            st.error("❌ 無法更新 Cookie")
            return results
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, port_code in enumerate(port_codes):
        status_text.text(f"正在處理 {port_code} ({i+1}/{len(port_codes)})...")
        success, message = crawler.fetch_port_data(port_code)
        
        if success:
            db_data = crawler.get_data_from_db(port_code)
            if db_data:
                content, issued_time, port_name = db_data
                try:
                    _, records, warnings = parser.parse_content(content)
                    risk_records = []
                    all_analyzed = []
                    max_risk_level = 0
                    
                    for record in records:
                        analysis = analyzer.analyze_record(record)
                        all_analyzed.append(analysis)
                        if analysis['risks']:
                            risk_records.append(analysis)
                            max_risk_level = max(max_risk_level, analysis['risk_level'])
                    
                    results[port_code] = {
                        'port_name': port_name, 'issued_time': issued_time,
                        'total_records': len(records), 'risk_records': risk_records,
                        'all_analyzed': all_analyzed, 'max_risk_level': max_risk_level,
                        'all_records': records, 'warnings': warnings,
                        'status': 'success', 'raw_content': content
                    }
                except Exception as e:
                    results[port_code] = {'status': 'parse_error', 'error': str(e)}
            else:
                results[port_code] = {'status': 'no_data', 'message': '無資料'}
        else:
            results[port_code] = {'status': 'fetch_error', 'message': message}
        progress_bar.progress((i + 1) / len(port_codes))
    
    status_text.empty()
    progress_bar.empty()
    return results

def display_weather_table(records: List[WeatherRecord], show_all: bool = True):
    if not records:
        st.warning("無氣象資料")
        return
    
    data = []
    analyzer = WeatherRiskAnalyzer()
    
    for record in records:
        analysis = analyzer.analyze_record(record)
        data.append({
            '時間': record.time.strftime('%m/%d %H:%M'),
            '風向': record.wind_direction,
            '風速 (kts)': f"{record.wind_speed:.1f}",
            '陣風 (kts)': f"{record.wind_gust:.1f}",
            '浪向': record.wave_direction,
            '浪高 (m)': f"{record.wave_height:.1f}",
            '週期 (s)': f"{record.wave_period:.1f}",
            '風險等級': WeatherRiskAnalyzer.get_risk_label(analysis['risk_level'])
        })
    
    df = pd.DataFrame(data)
    
    # 優化表格配色
    def highlight_risk(row):
        label = row['風險等級']
        if '危險' in label: return ['background-color: rgba(220, 53, 69, 0.15); color: #721c24; font-weight: bold;'] * len(row)
        elif '警告' in label: return ['background-color: rgba(253, 126, 20, 0.15); color: #856404;'] * len(row)
        elif '注意' in label: return ['background-color: rgba(255, 193, 7, 0.15); color: #856404;'] * len(row)
        else: return [''] * len(row)
    
    st.dataframe(
        df.style.apply(highlight_risk, axis=1),
        use_container_width=True,
        height=400,
        hide_index=True
    )

def display_port_detail(port_code: str, data: Dict):
    """顯示單一港口詳細資訊 - 視覺優化版"""
    
    # 頂部資訊卡
    st.markdown(f"""
    <div class="info-card">
        <h2 style="margin-top:0;">⚓ {port_code} - {data['port_name']}</h2>
        <div style="display: flex; gap: 20px; align-items: center; color: #666;">
            <span>📅 發布: {data['issued_time']}</span>
            <span>📊 記錄: {data['total_records']} 筆</span>
            {WeatherRiskAnalyzer.get_risk_badge(data['max_risk_level'])}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # 內容切換
    view_mode = st.radio(
        "",  # 隱藏標籤
        ["📈 趨勢圖表", "📋 完整資料表", "⚠️ 警戒時段", "📄 原始資料"],
        horizontal=True,
        key=f"view_{port_code}",
        label_visibility="collapsed"
    )
    
    st.markdown("---")

    if view_mode == "📋 完整資料表":
        st.caption("📋 完整氣象預報資料")
        display_weather_table(data['all_records'], show_all=True)
        
    elif view_mode == "⚠️ 警戒時段":
        st.subheader("⚠️ 警戒時段詳情")
        if data['risk_records']:
            for i, risk in enumerate(data['risk_records'], 1):
                time_str = risk['time'].strftime('%Y-%m-%d %H:%M')
                badge = WeatherRiskAnalyzer.get_risk_badge(risk['risk_level'])
                
                with st.expander(f"🔴 {time_str} - {risk['risks'][0]}", expanded=(i <= 3)):
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.markdown("**觸發警戒條件:**")
                        for r in risk['risks']:
                            st.markdown(f"- {r}")
                    with col2:
                        st.metric("風速", f"{risk['wind_speed']:.1f} kts", f"陣風 {risk['wind_gust']:.1f}")
                        st.metric("浪高", f"{risk['wave_height']:.1f} m", f"{risk['wave_direction']}")
        else:
            st.success("✅ 此港口無警戒時段，天氣狀況良好！")
    
    elif view_mode == "📈 趨勢圖表":
        records = data['all_records']
        if records:
            df = pd.DataFrame([{
                'time': r.time, 'wind_speed': r.wind_speed,
                'wind_gust': r.wind_gust, 'wave_height': r.wave_height,
                'wave_max': r.wave_max
            } for r in records])
            
            # 共用圖表佈局設定 (讓圖表更美觀)
            common_layout = dict(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                hovermode='x unified',
                height=350,
                xaxis=dict(showgrid=False, zeroline=False),
                yaxis=dict(showgrid=True, gridcolor='#eee', zeroline=False),
                margin=dict(l=0, r=0, t=30, b=0)
            )
            
            # 風速圖
            fig_wind = go.Figure()
            fig_wind.add_trace(go.Scatter(
                x=df['time'], y=df['wind_speed'], mode='lines',
                name='風速', line=dict(color='#007bff', width=2),
                fill='tozeroy', fillcolor='rgba(0, 123, 255, 0.1)'
            ))
            fig_wind.add_trace(go.Scatter(
                x=df['time'], y=df['wind_gust'], mode='lines',
                name='陣風', line=dict(color='#dc3545', width=1, dash='dot')
            ))
            
            # 加入警戒線
            fig_wind.add_hline(y=25, line_width=1, line_color="#ffc107", annotation_text="注意 (25)")
            fig_wind.add_hline(y=30, line_width=1, line_color="#fd7e14", annotation_text="警告 (30)")
            
            fig_wind.update_layout(title_text="🌬️ 風速趨勢 (Knots)", **common_layout)
            st.plotly_chart(fig_wind, use_container_width=True)
            
            # 浪高圖
            fig_wave = go.Figure()
            fig_wave.add_trace(go.Scatter(
                x=df['time'], y=df['wave_height'], mode='lines',
                name='顯著浪高', line=dict(color='#20c997', width=2),
                fill='tozeroy', fillcolor='rgba(32, 201, 151, 0.1)'
            ))
            fig_wave.add_hline(y=2.0, line_width=1, line_color="#ffc107", annotation_text="注意 (2.0)")
            fig_wave.add_hline(y=2.5, line_width=1, line_color="#fd7e14", annotation_text="警告 (2.5)")
            
            fig_wave.update_layout(title_text="🌊 浪高趨勢 (Meter)", **common_layout)
            st.plotly_chart(fig_wave, use_container_width=True)
    
    elif view_mode == "📄 原始資料":
        st.text_area("WNI 原始資料", value=data['raw_content'], height=500)

def display_risk_summary(results: Dict):
    """顯示風險摘要儀表板"""
    
    risk_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    total_ports = 0
    high_risk_list = []
    
    for port_code, data in results.items():
        if data.get('status') == 'success':
            total_ports += 1
            lvl = data.get('max_risk_level', 0)
            risk_counts[lvl] += 1
            if lvl >= 2:
                high_risk_list.append((port_code, data))
    
    # 頂部標題
    st.markdown(f"## 🚨 監控總覽 (已監控 {total_ports} 個港口)")
    
    # 使用我們 CSS 美化過的 Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric("⛔ 風險 (Danger)", risk_counts[3], delta="需立即處置" if risk_counts[3]>0 else None, delta_color="inverse")
    with col2: st.metric("⚠️ 警告 (Warning)", risk_counts[2], delta="密切注意" if risk_counts[2]>0 else None, delta_color="inverse")
    with col3: st.metric("⚡ 注意 (Caution)", risk_counts[1], delta=None)
    with col4: st.metric("✅ 安全 (Safe)", risk_counts[0], delta="狀況良好")
    
    # 高風險港口列表
    if high_risk_list:
        st.markdown("### 🔥 重點關注港口")
        high_risk_list.sort(key=lambda x: x[1]['max_risk_level'], reverse=True)
        
        for port_code, data in high_risk_list:
            risk_color = WeatherRiskAnalyzer.get_risk_color(data['max_risk_level'])
            risk_label = WeatherRiskAnalyzer.get_risk_label(data['max_risk_level'])
            risk_cnt = len(data['risk_records'])
            
            st.markdown(f"""
            <div style="background-color: white; border-left: 5px solid {risk_color}; padding: 15px; border-radius: 5px; margin-bottom: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0; color: #333;">{port_code} - {data['port_name']}</h4>
                    <span style="background:{risk_color}; color:white; padding:4px 10px; border-radius:15px; font-size:0.8rem;">{risk_label}</span>
                </div>
                <p style="margin: 5px 0 0 0; color: #666; font-size: 0.9rem;">
                    🔴 共發現 <b>{risk_cnt}</b> 個高風險時段 ｜ 發布時間: {data['issued_time']}
                </p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="padding: 20px; background-color: #d4edda; color: #155724; border-radius: 8px; text-align: center; margin-top: 20px;">
            <h4>✅ 目前所有港口狀況良好</h4>
            <p>無檢測到警告等級以上之風險。</p>
        </div>
        """, unsafe_allow_html=True)

# ================= 主程式 =================
def main():
    # 側邊欄
    with st.sidebar:
        st.image("https://cdn-icons-png.flaticon.com/512/2942/2942544.png", width=50)
        st.title("WNI氣象數據監控中心")
        st.caption("Wan Hai Marine Technology Division")
        st.caption("Fleet Risk Management Department")
        st.markdown("---")
        
        st.subheader("⚙️ 系統設定")
        
        with st.expander("🔐 帳號設定", expanded=not st.session_state.login_configured):
            username = st.text_input("帳號", value=st.session_state.aedyn_username, key="user")
            password = st.text_input("密碼", value=st.session_state.aedyn_password, type="password", key="pass")
            
            if st.button("儲存並登入", use_container_width=True):
                if username and password:
                    st.session_state.aedyn_username = username
                    st.session_state.aedyn_password = password
                    st.session_state.login_configured = True
                    st.success("已儲存")
                else:
                    st.error("請輸入完整資訊")
        
        if st.session_state.login_configured:
            if not st.session_state.crawler:
                if st.button("🚀 初始化系統", type="primary", use_container_width=True):
                    with st.spinner("系統啟動中..."):
                        c = init_crawler(st.session_state.aedyn_username, st.session_state.aedyn_password)
                        if c:
                            st.session_state.crawler = c
                            st.session_state.crawler_initialized = True
                            st.rerun()

            st.markdown("---")
            st.subheader("📡 資料抓取")
            
            crawl_mode = st.radio("範圍", ["🌐 全部港口", "📝 指定港口"], label_visibility="collapsed")
            
            port_codes = []
            if crawl_mode == "🌐 全部港口":
                if st.session_state.crawler:
                    port_codes = st.session_state.crawler.port_list
                    st.info(f"全系統共 {len(port_codes)} 個港口")
            else:
                if st.session_state.crawler:
                    opts = get_port_display_options(st.session_state.crawler)
                    sel = st.multiselect("選擇港口", list(opts.keys()))
                    port_codes = [opts[k] for k in sel]
            
            if st.session_state.crawler and port_codes:
                if st.button("🔄 開始更新資料", type="primary", use_container_width=True):
                    with st.spinner("正在分析氣象數據..."):
                        res = fetch_and_analyze_ports(st.session_state.crawler, port_codes)
                        st.session_state.analysis_results = res
                        st.session_state.last_update = datetime.now()
                        st.rerun()
            
            if st.session_state.last_update:
                st.caption(f"最後更新: {st.session_state.last_update.strftime('%H:%M')}")

    # 主畫面邏輯
    if not st.session_state.analysis_results:
        # 空狀態 (Empty State) 美化
        st.markdown("""
        <div style="text-align: center; padding: 50px; color: #666;">
            <h1>👋 歡迎使用氣象監控系統</h1>
            <p style="font-size: 1.2rem;">請從左側側邊欄進行登入並初始化系統以開始監控。</p>
            <div style="margin-top: 30px; display: flex; justify-content: center; gap: 20px;">
                <div style="background:white; padding:20px; border-radius:10px; box-shadow:0 2px 5px rgba(0,0,0,0.05); width: 200px;">
                    <h3>🌐</h3>
                    <p>全船隊監控</p>
                </div>
                <div style="background:white; padding:20px; border-radius:10px; box-shadow:0 2px 5px rgba(0,0,0,0.05); width: 200px;">
                    <h3>⚡</h3>
                    <p>即時風險預警</p>
                </div>
                <div style="background:white; padding:20px; border-radius:10px; box-shadow:0 2px 5px rgba(0,0,0,0.05); width: 200px;">
                    <h3>📊</h3>
                    <p>視覺化圖表</p>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        results = st.session_state.analysis_results
        
        # 1. 儀表板
        display_risk_summary(results)
        st.markdown("<br>", unsafe_allow_html=True)
        
        # 2. 詳細資訊區
        st.markdown("### 📊 詳細分析")
        
        # 篩選器
        col_f1, col_f2 = st.columns([1, 3])
        with col_f1:
            filter_mode = st.selectbox("顯示模式", ["🌐 全部港口", "⚠️ 僅警戒港口", "🔍 單一港口搜尋"])
        
        if filter_mode == "🔍 單一港口搜尋":
            opts = {f"{k} - {v['port_name']}": k for k, v in results.items() if v.get('status')=='success'}
            selected = st.selectbox("搜尋港口", list(opts.keys()))
            if selected:
                display_port_detail(opts[selected], results[opts[selected]])
        
        elif filter_mode == "⚠️ 僅警戒港口":
            alert_ports = {k: v for k, v in results.items() if v.get('status')=='success' and v.get('max_risk_level', 0) >= 1}
            if alert_ports:
                sorted_ports = sorted(alert_ports.items(), key=lambda x: x[1]['max_risk_level'], reverse=True)
                tabs = st.tabs([f"{k} {WeatherRiskAnalyzer.get_risk_label(v['max_risk_level'])}" for k, v in sorted_ports])
                for tab, (code, data) in zip(tabs, sorted_ports):
                    with tab: display_port_detail(code, data)
            else:
                st.info("目前無警戒港口")
                
        else:
            success_ports = {k: v for k, v in results.items() if v.get('status')=='success'}
            if success_ports:
                tabs = st.tabs([f"{k}" for k in success_ports.keys()])
                for tab, (code, data) in zip(tabs, success_ports.items()):
                    with tab: display_port_detail(code, data)

if __name__ == "__main__":
    main()