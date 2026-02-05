# n8n_weather_monitor.py
import os
import sys
import json
import traceback
import smtplib
import io
import base64
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field

# 第三方套件
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 非互動模式
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 載入環境變數
load_dotenv()

# ================= 自定義模組導入檢查 =================
try:
    from wni_crawler import PortWeatherCrawler, WeatherDatabase
    from weather_parser import WeatherParser, WeatherRecord
except ImportError as e:
    print(f"❌ 錯誤: 找不到必要的模組 ({e})。請確認 wni_crawler.py 與 weather_parser.py 是否在同一目錄下。")
    sys.exit(1)

# ================= 設定區 =================

# 1. WNI 氣象網站爬蟲帳密
AEDYN_USERNAME = os.getenv('AEDYN_USERNAME', 'harry_chung@wanhai.com')
AEDYN_PASSWORD = os.getenv('AEDYN_PASSWORD', 'wanhai888')

# 2. Gmail 接力發信用
MAIL_USER = os.getenv('MAIL_USER')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')

# 3. 接力信件的目標與暗號
TARGET_EMAIL = os.getenv('TARGET_EMAIL', 'harry_chung@wanhai.com')
TRIGGER_SUBJECT = "GITHUB_TRIGGER_WEATHER_REPORT"
TRIGGER_SUBJECT_TEMP = "GITHUB_TRIGGER_TEMPERATURE_ALERT"  # ✅ 新增：低溫警報主旨

# 4. Teams Webhook
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL', '')

# 5. 檔案路徑
EXCEL_FILE_PATH = os.getenv('EXCEL_FILE_PATH', 'WHL_all_ports_list.xlsx')
CHART_OUTPUT_DIR = 'charts'

# 6. 風險閾值
RISK_THRESHOLDS = {
    'wind_caution': 22,
    'wind_warning': 28,
    'wind_danger': 34,
    'gust_caution': 28,
    'gust_warning': 34,
    'gust_danger': 41,
    'wave_caution': 2.5,
    'wave_warning': 3.5,
    'wave_danger': 4.0,
    
    # ✅ 天氣狀況閾值
    'temp_freezing': 0,          # 氣溫 < 0°C
    'pressure_low': 1000,        # 氣壓 < 1000 hPa
    'visibility_poor': 5000,     # 能見度 < 5km (5000m)
}

@dataclass
class RiskAssessment:
    """風險評估結果資料結構"""
    # 必填欄位
    port_code: str
    port_name: str
    country: str
    risk_level: int
    risk_factors: List[str]
    max_wind_kts: float
    max_wind_bft: int
    max_gust_kts: float
    max_gust_bft: int
    max_wave: float
    
    max_wind_time_utc: str
    max_wind_time_lct: str
    max_gust_time_utc: str
    max_gust_time_lct: str
    max_wave_time_utc: str
    max_wave_time_lct: str
    
    risk_periods: List[Dict[str, Any]]
    issued_time: str
    latitude: float
    longitude: float
    
    # ✅ 選填欄位（有預設值）
    min_temperature: float = 999.0
    min_pressure: float = 9999.0
    min_visibility: float = 99999.0
    min_temp_time_utc: str = ""
    min_temp_time_lct: str = ""
    min_pressure_time_utc: str = ""
    min_pressure_time_lct: str = ""
    
    # ✅ 新增：能見度不良時段列表
    poor_visibility_periods: List[Dict[str, Any]] = field(default_factory=list)
    
    raw_records: Optional[List[WeatherRecord]] = None
    weather_records: Optional[List] = None
    chart_base64_list: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ['raw_records', 'weather_records', 'chart_base64_list']:
            d.pop(key, None)
        return d

# ================= 繪圖模組 =================

class ChartGenerator:
    """圖表生成器 - 支援 Base64 輸出（高解析度版）"""
    
    def __init__(self, output_dir: str = CHART_OUTPUT_DIR):
        self.output_dir = output_dir
        
        if os.path.exists(self.output_dir):
            for f in os.listdir(self.output_dir):
                if f.endswith('.png'):
                    try:
                        os.remove(os.path.join(self.output_dir, f))
                    except:
                        pass
        
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 設定中文字體
        try:
            plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'Arial Unicode MS', 'DejaVu Sans', 'sans-serif']
            plt.rcParams['axes.unicode_minus'] = False
        except:
            print("⚠️ 無法設定中文字體")

    def _prepare_dataframe(self, records: List[WeatherRecord]) -> pd.DataFrame:
        data = []
        for r in records:
            data.append({
                'time': r.time,
                'wind_speed': r.wind_speed_kts,
                'wind_gust': r.wind_gust_kts,
                'wave_height': r.wave_height
            })
        return pd.DataFrame(data)

    def _fig_to_base64(self, fig, dpi=150) -> str:
        """將 Matplotlib Figure 轉為 Base64 字串（高解析度）"""
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=dpi)
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        return img_str

    def generate_wind_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製風速趨勢圖，回傳 Base64 字串（專業優化版）"""
        if not assessment.raw_records:
            print(f"      ⚠️ {port_code} 沒有原始資料記錄")
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            if df.empty:
                print(f"      ⚠️ {port_code} DataFrame 為空")
                return None
            
            print(f"      📊 準備繪製 {port_code} 的風速圖 (資料點數: {len(df)})")
            
            plt.style.use('default')
            fig, ax = plt.subplots(figsize=(16, 7), dpi=120)
            
            fig.patch.set_facecolor('#FFFFFF')
            ax.set_facecolor('#F8FAFC')
            
            # 繪製風險區域背景
            ax.axhspan(RISK_THRESHOLDS['wind_danger'], ax.get_ylim()[1] if len(df) > 0 else 60, 
                    facecolor='#FEE2E2', alpha=0.3, zorder=0)
            ax.axhspan(RISK_THRESHOLDS['wind_warning'], RISK_THRESHOLDS['wind_danger'], 
                    facecolor='#FEF3C7', alpha=0.3, zorder=0)
            ax.axhspan(RISK_THRESHOLDS['wind_caution'], RISK_THRESHOLDS['wind_warning'], 
                    facecolor='#FEF9C3', alpha=0.3, zorder=0)
            
            # 繪製主要數據線
            line1 = ax.plot(df['time'], df['wind_speed'], 
                            color='#1E40AF', linewidth=3.5, marker='o', markersize=7,
                            markerfacecolor='#3B82F6', markeredgecolor='#1E40AF',
                            markeredgewidth=1.5, label='Wind Speed', zorder=5, alpha=0.9)
            
            line2 = ax.plot(df['time'], df['wind_gust'], 
                            color='#DC2626', linewidth=3, linestyle='--',
                            marker='s', markersize=6, markerfacecolor='#EF4444',
                            markeredgecolor='#DC2626', markeredgewidth=1.5,
                            label='Wind Gust', zorder=5, alpha=0.9)
            
            ax.fill_between(df['time'], df['wind_speed'], alpha=0.2, color='#3B82F6', zorder=2)
            
            high_risk_mask = df['wind_speed'] >= RISK_THRESHOLDS['wind_caution']
            if high_risk_mask.any():
                ax.fill_between(df['time'], df['wind_speed'], where=high_risk_mask,
                            interpolate=True, color='#F59E0B', alpha=0.35,
                            label='High Risk Period', zorder=3)
            
            # 繪製閾值線
            ax.axhline(RISK_THRESHOLDS['wind_danger'], color="#DC2626", linestyle='-', 
                    linewidth=2.5, label=f'🔴 Danger Threshold ({RISK_THRESHOLDS["wind_danger"]} kts)', 
                    zorder=4, alpha=0.8)
            ax.axhline(RISK_THRESHOLDS['wind_warning'], color="#F59E0B", linestyle='--', 
                    linewidth=2.5, label=f'🟠 Warning Threshold ({RISK_THRESHOLDS["wind_warning"]} kts)', 
                    zorder=4, alpha=0.8)
            ax.axhline(RISK_THRESHOLDS['wind_caution'], color="#EAB308", linestyle=':', 
                    linewidth=2.2, label=f'🟡 Caution Threshold ({RISK_THRESHOLDS["wind_caution"]} kts)', 
                    zorder=4, alpha=0.7)
            
            # 標註最大值
            max_wind_idx = df['wind_speed'].idxmax()
            max_gust_idx = df['wind_gust'].idxmax()
            
            ax.annotate(f'Max: {df.loc[max_wind_idx, "wind_speed"]:.1f} kts',
                    xy=(df.loc[max_wind_idx, 'time'], df.loc[max_wind_idx, 'wind_speed']),
                    xytext=(10, 15), textcoords='offset points', fontsize=11, fontweight='bold',
                    color='#1E40AF', bbox=dict(boxstyle='round,pad=0.5', facecolor='#EFF6FF', 
                    edgecolor='#3B82F6', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#1E40AF', lw=2))
            
            ax.annotate(f'Max: {df.loc[max_gust_idx, "wind_gust"]:.1f} kts',
                    xy=(df.loc[max_gust_idx, 'time'], df.loc[max_gust_idx, 'wind_gust']),
                    xytext=(10, -20), textcoords='offset points', fontsize=11, fontweight='bold',
                    color='#DC2626', bbox=dict(boxstyle='round,pad=0.5', facecolor='#FEF2F2', 
                    edgecolor='#EF4444', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#DC2626', lw=2))
            
            # 標題與標籤
            ax.set_title(f"🌪️ Wind Speed & Gust Forecast - {assessment.port_name} ({assessment.port_code})", 
                        fontsize=22, fontweight='bold', pad=20, color='#1F2937', fontfamily='sans-serif')
            
            fig.text(0.5, 0.94, '48-Hour Weather Monitoring | Data Source: WNI', 
                    ha='center', fontsize=12, color='#6B7280', style='italic')
            
            ax.set_ylabel('Wind Speed (knots)', fontsize=15, fontweight='600', color='#374151', labelpad=10)
            ax.set_xlabel('Date / Time (UTC)', fontsize=15, fontweight='600', color='#374151', labelpad=10)
            
            legend = ax.legend(loc='upper left', frameon=True, fontsize=12, shadow=True, fancybox=True,
                            framealpha=0.95, edgecolor='#D1D5DB', facecolor='#FFFFFF', ncol=2)
            legend.get_frame().set_linewidth(1.5)
            
            ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.8, color='#9CA3AF', zorder=1)
            ax.set_axisbelow(True)
            
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax.xaxis.set_minor_locator(mdates.HourLocator(interval=3))
            
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=11, fontweight='500')
            plt.setp(ax.yaxis.get_majorticklabels(), fontsize=11, fontweight='500')
            
            for spine in ['top', 'right']:
                ax.spines[spine].set_visible(False)
            
            for spine in ['bottom', 'left']:
                ax.spines[spine].set_edgecolor('#9CA3AF')
                ax.spines[spine].set_linewidth(2)
            
            y_max = max(df['wind_gust'].max(), RISK_THRESHOLDS['wind_danger']) * 1.15
            ax.set_ylim(0, y_max)
            
            fig.text(0.99, 0.01, 'WHL Marine Technology Division', 
                    ha='right', va='bottom', fontsize=9, color='#9CA3AF', alpha=0.6, style='italic')
            
            plt.tight_layout(rect=[0, 0.02, 1, 0.96])
            
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none', pad_inches=0.1)
            print(f"      💾 圖片已存檔: {filepath}")
            
            base64_str = self._fig_to_base64(fig, dpi=150)
            print(f"      ✅ Base64 轉換成功 (長度: {len(base64_str)} 字元)")
            
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"      ❌ 繪製風速圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None

    def generate_wave_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製浪高趨勢圖，回傳 Base64 字串（專業優化版）"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            if df['wave_height'].max() < 1.0:
                return None

            plt.style.use('default')
            fig, ax = plt.subplots(figsize=(16, 7), dpi=120)
            
            fig.patch.set_facecolor('#FFFFFF')
            ax.set_facecolor('#F0FDF4')
            
            ax.axhspan(RISK_THRESHOLDS['wave_danger'], ax.get_ylim()[1] if len(df) > 0 else 8, 
                    facecolor='#FEE2E2', alpha=0.3, zorder=0)
            ax.axhspan(RISK_THRESHOLDS['wave_warning'], RISK_THRESHOLDS['wave_danger'], 
                    facecolor='#FEF3C7', alpha=0.3, zorder=0)
            ax.axhspan(RISK_THRESHOLDS['wave_caution'], RISK_THRESHOLDS['wave_warning'], 
                    facecolor='#FEF9C3', alpha=0.3, zorder=0)
            
            line = ax.plot(df['time'], df['wave_height'], 
                        color='#047857', linewidth=4, marker='o', markersize=7,
                        markerfacecolor='#10B981', markeredgecolor='#047857',
                        markeredgewidth=1.5, label='Significant Wave Height',
                        zorder=5, alpha=0.9)
            
            ax.fill_between(df['time'], df['wave_height'], alpha=0.25, color='#10B981', zorder=2)
            
            high_risk_mask = df['wave_height'] >= RISK_THRESHOLDS['wave_caution']
            if high_risk_mask.any():
                ax.fill_between(df['time'], df['wave_height'], where=high_risk_mask,
                            interpolate=True, color='#F59E0B', alpha=0.35,
                            label='High Risk Period', zorder=3)
            
            ax.axhline(RISK_THRESHOLDS['wave_danger'], color="#DC2626", linestyle='-', 
                    linewidth=2.5, label=f'🔴 Danger Threshold ({RISK_THRESHOLDS["wave_danger"]} m)', 
                    zorder=4, alpha=0.8)
            ax.axhline(RISK_THRESHOLDS['wave_warning'], color="#F59E0B", linestyle='--', 
                    linewidth=2.5, label=f'🟠 Warning Threshold ({RISK_THRESHOLDS["wave_warning"]} m)', 
                    zorder=4, alpha=0.8)
            ax.axhline(RISK_THRESHOLDS['wave_caution'], color="#EAB308", linestyle=':', 
                    linewidth=2.2, label=f'🟡 Caution Threshold ({RISK_THRESHOLDS["wave_caution"]} m)', 
                    zorder=4, alpha=0.7)
            
            max_wave_idx = df['wave_height'].idxmax()
            ax.annotate(f'Max: {df.loc[max_wave_idx, "wave_height"]:.2f} m',
                    xy=(df.loc[max_wave_idx, 'time'], df.loc[max_wave_idx, 'wave_height']),
                    xytext=(10, 15), textcoords='offset points', fontsize=11, fontweight='bold',
                    color='#047857', bbox=dict(boxstyle='round,pad=0.5', facecolor='#D1FAE5', 
                    edgecolor='#10B981', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#047857', lw=2))
            
            ax.set_title(f"🌊 Wave Height Forecast - {assessment.port_name} ({assessment.port_code})", 
                        fontsize=22, fontweight='bold', pad=20, color='#1F2937', fontfamily='sans-serif')
            
            fig.text(0.5, 0.94, '48-Hour Weather Monitoring | Data Source: WNI', 
                    ha='center', fontsize=12, color='#6B7280', style='italic')
            
            ax.set_ylabel('Wave Height (meters)', fontsize=15, fontweight='600', color='#374151', labelpad=10)
            ax.set_xlabel('Date / Time (UTC)', fontsize=15, fontweight='600', color='#374151', labelpad=10)
            
            legend = ax.legend(loc='upper left', frameon=True, fontsize=12, shadow=True, fancybox=True,
                            framealpha=0.95, edgecolor='#D1D5DB', facecolor='#FFFFFF', ncol=2)
            legend.get_frame().set_linewidth(1.5)
            
            ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.8, color='#9CA3AF', zorder=1)
            ax.set_axisbelow(True)
            
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax.xaxis.set_minor_locator(mdates.HourLocator(interval=3))
            
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=11, fontweight='500')
            plt.setp(ax.yaxis.get_majorticklabels(), fontsize=11, fontweight='500')
            
            for spine in ['top', 'right']:
                ax.spines[spine].set_visible(False)
            
            for spine in ['bottom', 'left']:
                ax.spines[spine].set_edgecolor('#9CA3AF')
                ax.spines[spine].set_linewidth(2)
            
            y_max = max(df['wave_height'].max(), RISK_THRESHOLDS['wave_danger']) * 1.15
            ax.set_ylim(0, y_max)
            
            fig.text(0.99, 0.01, 'WHL Marine Technology Division', 
                    ha='right', va='bottom', fontsize=9, color='#9CA3AF', alpha=0.6, style='italic')
            
            plt.tight_layout(rect=[0, 0.02, 1, 0.96])
            
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none', pad_inches=0.1)
            print(f"      💾 圖片已存檔: {filepath}")
            
            base64_str = self._fig_to_base64(fig, dpi=150)
            print(f"      ✅ Base64 轉換成功 (長度: {len(base64_str)} 字元)")
            
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"      ❌ 繪製浪高圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None

    def generate_temperature_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製溫度趨勢圖（當有低溫警告時）"""
        if not assessment.weather_records:
            return None
        
        try:
            # 準備溫度資料
            temp_data = []
            for wr in assessment.weather_records:
                temp_data.append({
                    'time': wr.time,
                    'temperature': wr.temperature,
                    'precipitation': wr.precipitation
                })
            
            df = pd.DataFrame(temp_data)
            
            if df.empty or df['temperature'].min() >= RISK_THRESHOLDS['temp_freezing']:
                return None
            
            print(f"      📊 準備繪製 {port_code} 的溫度圖 (資料點數: {len(df)})")
            
            plt.style.use('default')
            
            # 設定圖表尺寸（雙Y軸）
            fig, ax1 = plt.subplots(figsize=(16, 7), dpi=120)
            
            fig.patch.set_facecolor('#FFFFFF')
            ax1.set_facecolor('#F0F9FF')
            
            # 繪製冰點警戒線背景
            ax1.axhspan(-50, RISK_THRESHOLDS['temp_freezing'], 
                        facecolor='#DBEAFE', alpha=0.3, zorder=0)
            
            # 主Y軸：溫度
            color_temp = '#DC2626'
            ax1.set_xlabel('Date / Time (UTC)', fontsize=15, fontweight='600', color='#374151', labelpad=10)
            ax1.set_ylabel('Temperature (°C)', fontsize=15, fontweight='600', color=color_temp, labelpad=10)
            
            line1 = ax1.plot(df['time'], df['temperature'], 
                            color=color_temp, linewidth=3.5, marker='o', markersize=7,
                            markerfacecolor='#FCA5A5', markeredgecolor=color_temp,
                            markeredgewidth=1.5, label='Temperature', zorder=5, alpha=0.9)
            
            ax1.tick_params(axis='y', labelcolor=color_temp, labelsize=11)
            
            # 冰點線
            ax1.axhline(RISK_THRESHOLDS['temp_freezing'], 
                        color="#3B82F6", linestyle='--', linewidth=2.5, 
                        label=f'❄️ Freezing Point (0°C)', zorder=4, alpha=0.8)
            
            # 填充低溫區域
            freezing_mask = df['temperature'] < RISK_THRESHOLDS['temp_freezing']
            if freezing_mask.any():
                ax1.fill_between(df['time'], df['temperature'], RISK_THRESHOLDS['temp_freezing'],
                                where=freezing_mask, interpolate=True, color='#DC2626',
                                alpha=0.3, label='Below Freezing', zorder=3)
            
            # 標註最低溫
            min_temp_idx = df['temperature'].idxmin()
            ax1.annotate(f'Min: {df.loc[min_temp_idx, "temperature"]:.1f}°C',
                        xy=(df.loc[min_temp_idx, 'time'], df.loc[min_temp_idx, 'temperature']),
                        xytext=(10, -20), textcoords='offset points', fontsize=11, fontweight='bold',
                        color=color_temp, bbox=dict(boxstyle='round,pad=0.5', facecolor='#FEE2E2', 
                        edgecolor=color_temp, linewidth=2),
                        arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color=color_temp, lw=2))
            
            # 次Y軸：降雨量
            ax2 = ax1.twinx()
            color_precip = '#3B82F6'
            ax2.set_ylabel('Precipitation (mm/h)', fontsize=15, fontweight='600', color=color_precip, labelpad=10)
            
            bars = ax2.bar(df['time'], df['precipitation'], width=0.05, color=color_precip, 
                          alpha=0.4, label='Precipitation', zorder=2)
            
            ax2.tick_params(axis='y', labelcolor=color_precip, labelsize=11)
            
            # 標題
            ax1.set_title(f"❄️ Temperature & Precipitation Forecast - {assessment.port_name} ({assessment.port_code})", 
                        fontsize=22, fontweight='bold', pad=20, color='#1F2937', fontfamily='sans-serif')
            
            fig.text(0.5, 0.94, '48-Hour Weather Monitoring | Data Source: WNI', 
                    ha='center', fontsize=12, color='#6B7280', style='italic')
            
            # 圖例
            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', frameon=True, 
                      fontsize=12, shadow=True, fancybox=True, framealpha=0.95,
                      edgecolor='#D1D5DB', facecolor='#FFFFFF')
            
            # 網格
            ax1.grid(True, alpha=0.3, linestyle='--', linewidth=0.8, color='#9CA3AF', zorder=1)
            ax1.set_axisbelow(True)
            
            # X軸格式
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
            ax1.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax1.xaxis.set_minor_locator(mdates.HourLocator(interval=3))
            
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=11, fontweight='500')
            
            # 邊框美化
            for spine in ['top']:
                ax1.spines[spine].set_visible(False)
                ax2.spines[spine].set_visible(False)
            
            for spine in ['bottom', 'left']:
                ax1.spines[spine].set_edgecolor('#9CA3AF')
                ax1.spines[spine].set_linewidth(2)
            
            ax2.spines['right'].set_edgecolor('#9CA3AF')
            ax2.spines['right'].set_linewidth(2)
            
            # 水印
            fig.text(0.99, 0.01, 'WHL Marine Technology Division', 
                    ha='right', va='bottom', fontsize=9, color='#9CA3AF', alpha=0.6, style='italic')
            
            plt.tight_layout(rect=[0, 0.02, 1, 0.96])
            
            # 儲存與轉換
            filepath = os.path.join(self.output_dir, f"temp_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none', pad_inches=0.1)
            print(f"      💾 溫度圖已存檔: {filepath}")
            
            base64_str = self._fig_to_base64(fig, dpi=150)
            print(f"      ✅ 溫度圖 Base64 轉換成功 (長度: {len(base64_str)} 字元)")
            
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"      ❌ 繪製溫度圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None


# ================= 風險分析模組 =================

class WeatherRiskAnalyzer:
    """氣象風險分析器（含天氣狀況）"""
    
    @staticmethod
    def kts_to_bft(speed_kts: float) -> int:
        if speed_kts < 1: return 0
        if speed_kts < 4: return 1
        if speed_kts < 7: return 2
        if speed_kts < 11: return 3
        if speed_kts < 17: return 4
        if speed_kts < 22: return 5
        if speed_kts < 28: return 6
        if speed_kts < 34: return 7
        if speed_kts < 41: return 8
        if speed_kts < 48: return 9
        if speed_kts < 56: return 10
        if speed_kts < 64: return 11
        return 12

    @classmethod
    def analyze_record(cls, record: WeatherRecord, weather_record=None) -> Dict:
        """分析單筆記錄（含風浪 + 天氣狀況）"""
        risks = []
        risk_level = 0

        # 風速檢查
        if record.wind_speed_kts >= RISK_THRESHOLDS['wind_danger']:
            risks.append(f"⛔ 風速危險: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_warning']:
            risks.append(f"⚠️ 風速警告: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
            risks.append(f"⚡ 風速注意: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 1)

        # 陣風檢查
        if record.wind_gust_kts >= RISK_THRESHOLDS['gust_danger']:
            risks.append(f"⛔ 陣風危險: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_warning']:
            risks.append(f"⚠️ 陣風警告: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
            risks.append(f"⚡ 陣風注意: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 1)

        # 浪高檢查
        if record.wave_height >= RISK_THRESHOLDS['wave_danger']:
            risks.append(f"⛔ 浪高危險: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 3)
        elif record.wave_height >= RISK_THRESHOLDS['wave_warning']:
            risks.append(f"⚠️ 浪高警告: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 2)
        elif record.wave_height >= RISK_THRESHOLDS['wave_caution']:
            risks.append(f"⚡ 浪高注意: {record.wave_height:.1f} m")
            risk_level = max(risk_level, 1)

        # 天氣狀況檢查
        if weather_record:
            # 氣溫檢查（< 0°C）
            if weather_record.temperature < RISK_THRESHOLDS['temp_freezing']:
                risks.append(f"❄️ 低溫警告: {weather_record.temperature:.1f}°C")
                risk_level = max(risk_level, 2)
            
            # 氣壓檢查（< 1000 hPa）
            if weather_record.pressure < RISK_THRESHOLDS['pressure_low']:
                risks.append(f"🌀 低氣壓警告: {weather_record.pressure:.0f} hPa")
                risk_level = max(risk_level, 2)
            
            # 能見度檢查（< 5km）
            vis_m = weather_record.visibility_meters
            if vis_m is not None and vis_m < RISK_THRESHOLDS['visibility_poor']:
                risks.append(f"🌫️ 能見度不良: {vis_m:.0f} m")
                risk_level = max(risk_level, 2)

        return {
            'risk_level': risk_level,
            'risks': risks
        }

    @classmethod
    def get_risk_label(cls, risk_level: int) -> str:
        return {
            0: "安全 Safe",
            1: "注意 Caution",
            2: "警告 Warning",
            3: "危險 Danger"
        }.get(risk_level, "未知 Unknown")

    @classmethod
    def analyze_port_risk(cls, port_code: str, port_info: Dict[str, Any],
                        content: str, issued_time: str) -> Optional[RiskAssessment]:
        """分析港口風險（含天氣狀況）"""
        try:
            parser = WeatherParser()
            
            # 解析風浪 + 天氣狀況
            port_name, wind_records, weather_records, warnings = parser.parse_content(content)
            
            if not wind_records:
                return None
            
            # 建立時間對應的天氣狀況字典
            weather_dict = {}
            if weather_records:
                for wr in weather_records:
                    weather_dict[wr.time] = wr
            
            risk_periods = []
            max_level = 0
            
            # 找出極值記錄
            max_wind_record = max(wind_records, key=lambda r: r.wind_speed_kts)
            max_gust_record = max(wind_records, key=lambda r: r.wind_gust_kts)
            max_wave_record = max(wind_records, key=lambda r: r.wave_height)
            
            # 天氣狀況極值
            min_temp_record = None
            min_pressure_record = None
            poor_visibility_periods = []  # ✅ 改為收集所有低能見度時段
            
            if weather_records:
                min_temp_record = min(weather_records, key=lambda r: r.temperature)
                min_pressure_record = min(weather_records, key=lambda r: r.pressure)
                
                # ✅ 收集所有能見度 < 5km 的時段
                for wr in weather_records:
                    if wr.visibility_meters is not None and wr.visibility_meters < RISK_THRESHOLDS['visibility_poor']:
                        poor_visibility_periods.append({
                            'time_utc': wr.time.strftime('%Y-%m-%d %H:%M'),
                            'time_lct': wr.lct_time.strftime('%Y-%m-%d %H:%M'),
                            'visibility_m': wr.visibility_meters,
                            'visibility_km': wr.visibility_meters / 1000
                        })
            
            # 分析每個時段
            for record in wind_records:
                wx_record = weather_dict.get(record.time)
                analyzed = cls.analyze_record(record, wx_record)
                
                if analyzed['risks']:
                    period_data = {
                        'time': record.time.strftime('%Y-%m-%d %H:%M'),
                        'wind_speed_kts': record.wind_speed_kts,
                        'wind_speed_bft': record.wind_speed_bft,
                        'wind_gust_kts': record.wind_gust_kts,
                        'wind_gust_bft': record.wind_gust_bft,
                        'wave_height': record.wave_height,
                        'risks': analyzed['risks'],
                        'risk_level': analyzed['risk_level']
                    }
                    
                    if wx_record:
                        period_data.update({
                            'temperature': wx_record.temperature,
                            'pressure': wx_record.pressure,
                            'visibility': wx_record.visibility,
                            'weather_code': wx_record.weather_code
                        })
                    
                    risk_periods.append(period_data)
                    max_level = max(max_level, analyzed['risk_level'])
            
            if max_level == 0:
                return None
            
            # 建立風險因素列表
            risk_factors = []
            if max_wind_record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
                risk_factors.append(f"風速 {max_wind_record.wind_speed_kts:.1f} kts")
            if max_gust_record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
                risk_factors.append(f"陣風 {max_gust_record.wind_gust_kts:.1f} kts")
            if max_wave_record.wave_height >= RISK_THRESHOLDS['wave_caution']:
                risk_factors.append(f"浪高 {max_wave_record.wave_height:.1f} m")
            
            # 加入天氣風險因素
            if min_temp_record and min_temp_record.temperature < RISK_THRESHOLDS['temp_freezing']:
                risk_factors.append(f"低溫 {min_temp_record.temperature:.1f}°C")
            if min_pressure_record and min_pressure_record.pressure < RISK_THRESHOLDS['pressure_low']:
                risk_factors.append(f"低氣壓 {min_pressure_record.pressure:.0f} hPa")
            if poor_visibility_periods:
                risk_factors.append(f"低能見度 ({len(poor_visibility_periods)} 時段)")
            
            # 計算 LCT 時區偏移
            lct_offset_hours = int(max_wind_record.lct_time.utcoffset().total_seconds() / 3600)
            
            # 建立 RiskAssessment
            assessment = RiskAssessment(
                port_code=port_code,
                port_name=port_info.get('port_name', port_name),
                country=port_info.get('country', 'N/A'),
                risk_level=max_level,
                risk_factors=risk_factors,
                
                max_wind_kts=max_wind_record.wind_speed_kts,
                max_wind_bft=max_wind_record.wind_speed_bft,
                max_gust_kts=max_gust_record.wind_gust_kts,
                max_gust_bft=max_gust_record.wind_gust_bft,
                max_wave=max_wave_record.wave_height,
                
                max_wind_time_utc=f"{max_wind_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_gust_time_utc=f"{max_gust_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_wave_time_utc=f"{max_wave_record.time.strftime('%m/%d %H:%M')} (UTC)",
                
                max_wind_time_lct=f"{max_wind_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                max_gust_time_lct=f"{max_gust_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                max_wave_time_lct=f"{max_wave_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                
                min_temperature=min_temp_record.temperature if min_temp_record else 999,
                min_pressure=min_pressure_record.pressure if min_pressure_record else 9999,
                min_visibility=min(p['visibility_m'] for p in poor_visibility_periods) if poor_visibility_periods else 99999,
                
                min_temp_time_utc=f"{min_temp_record.time.strftime('%m/%d %H:%M')} (UTC)" if min_temp_record else "",
                min_temp_time_lct=f"{min_temp_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)" if min_temp_record else "",
                
                min_pressure_time_utc=f"{min_pressure_record.time.strftime('%m/%d %H:%M')} (UTC)" if min_pressure_record else "",
                min_pressure_time_lct=f"{min_pressure_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)" if min_pressure_record else "",
                
                poor_visibility_periods=poor_visibility_periods,
                
                risk_periods=risk_periods,
                issued_time=issued_time,
                latitude=port_info.get('latitude', 0.0),
                longitude=port_info.get('longitude', 0.0),
                raw_records=wind_records,
                weather_records=weather_records
            )
            
            return assessment
            
        except Exception as e:
            print(f"❌ 分析港口 {port_code} 時發生錯誤: {e}")
            traceback.print_exc()
            return None


# ================= Teams 通知器 =================

class TeamsNotifier:
    """Teams 通知發送器"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_risk_alert(self, risk_assessments: List[RiskAssessment]) -> bool:
        if not self.webhook_url:
            print("⚠️ 未設定 Teams Webhook URL")
            return False
        
        if not risk_assessments:
            return self._send_all_safe_notification()
        
        try:
            card = self._create_adaptive_card(risk_assessments)
            response = requests.post(
                self.webhook_url, 
                json=card, 
                headers={'Content-Type': 'application/json'}, 
                timeout=30
            )
            
            if response.status_code == 200:
                print("✅ Teams 通知發送成功")
                return True
            else:
                print(f"❌ Teams 通知發送失敗: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ 發送 Teams 通知時發生錯誤: {e}")
            traceback.print_exc()
            return False
    
    def _send_all_safe_notification(self) -> bool:
        try:
            card = {
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "✅ WHL 港口氣象監控: 所有港口安全",
                                "weight": "Bolder",
                                "size": "Large",
                                "color": "Good"
                            },
                            {
                                "type": "TextBlock",
                                "text": f"檢查時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                "isSubtle": True,
                                "spacing": "Small"
                            }
                        ]
                    }
                }]
            }
            response = requests.post(self.webhook_url, json=card, headers={'Content-Type': 'application/json'})
            return response.status_code == 200
        except:
            return False
    
    def _create_adaptive_card(self, risk_assessments: List[RiskAssessment]) -> Dict[str, Any]:
        """建立 Adaptive Card"""
        
        danger_ports = [a for a in risk_assessments if a.risk_level == 3]
        warning_ports = [a for a in risk_assessments if a.risk_level == 2]
        caution_ports = [a for a in risk_assessments if a.risk_level == 1]
        
        body = [
            {
                "type": "TextBlock",
                "text": "⚠️ WHL 港口氣象風險警報",
                "weight": "Bolder",
                "size": "Large",
                "color": "Attention"
            },
            {
                "type": "TextBlock",
                "text": f"發現 {len(risk_assessments)} 個高風險港口",
                "isSubtle": True,
                "spacing": "Small"
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "🔴 高度風險 (HEIGHT RISK)", "value": str(len(danger_ports))},
                    {"title": "🟠 中度風險 (MEDIUM RISK)", "value": str(len(warning_ports))},
                    {"title": "🟡 低度風險 (LOW RISK)", "value": str(len(caution_ports))},
                    {"title": "📅 更新時間", "value": datetime.now().strftime('%Y-%m-%d %H:%M')}
                ],  
                "spacing": "Medium"
            }
        ]
        
        top_risks = sorted(risk_assessments, key=lambda x: x.risk_level, reverse=True)[:5]
        
        for port in top_risks:
            risk_color = {3: "Attention", 2: "Warning", 1: "Good"}.get(port.risk_level, "Default")
            risk_emoji = {3: "🔴", 2: "🟠", 1: "🟡"}.get(port.risk_level, "⚪")
            
            body.append({
                "type": "Container",
                "style": "emphasis",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": f"{risk_emoji} {port.port_code} - {port.port_name}",
                        "weight": "Bolder",
                        "color": risk_color
                    },
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "風速", "value": f"{port.max_wind_kts:.0f} kts (BF{port.max_wind_bft})"},
                            {"title": "陣風", "value": f"{port.max_gust_kts:.0f} kts (BF{port.max_gust_bft})"},
                            {"title": "浪高", "value": f"{port.max_wave:.1f} m"},
                            {"title": "國家", "value": port.country}
                        ]
                    }
                ],
                "spacing": "Medium"
            })
        
        if len(risk_assessments) > 5:
            body.append({
                "type": "TextBlock",
                "text": f"... 及其他 {len(risk_assessments) - 5} 個港口 (詳見郵件報告)",
                "isSubtle": True,
                "spacing": "Small"
            })
        
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body
                }
            }]
        }


# ================= Gmail 通知器 =================

class GmailRelayNotifier:
    """Gmail 接力發信器"""
    
    def __init__(self):
        self.user = MAIL_USER
        self.password = MAIL_PASSWORD
        self.target = TARGET_EMAIL
        self.subject_trigger = TRIGGER_SUBJECT
        self.subject_temp = TRIGGER_SUBJECT_TEMP

    def send_trigger_email(self, report_data: dict, report_html: str, 
                           images: Dict[str, str] = None) -> bool:
        """發送主要氣象風險報告"""
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密 (MAIL_USER / MAIL_PASSWORD)")
            return False

        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_trigger
        
        json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
        msg.attach(MIMEText(json_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(report_html, 'html', 'utf-8'))

        try:
            print(f"📧 正在透過 Gmail 發送主要氣象報表給 {self.target}...")
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
            print("   🔑 正在登入...")
            server.login(self.user, self.password)
            
            print("   📨 正在傳送...")
            server.sendmail(self.user, self.target, msg.as_string())
            server.quit()
            
            print(f"✅ 主要氣象報告發送成功！")
            return True
            
        except smtplib.SMTPAuthenticationError:
            print("❌ Gmail 認證失敗！請檢查:")
            print("   1. MAIL_USER 是否正確")
            print("   2. MAIL_PASSWORD 是否為「應用程式密碼」(非一般密碼)")
            print("   3. Google 帳戶是否已啟用「兩步驟驗證」")
            return False
            
        except Exception as e:
            print(f"❌ Gmail 發送失敗: {e}")
            traceback.print_exc()
            return False

    def send_temperature_alert(self, temp_report_data: dict, temp_report_html: str) -> bool:
        """✅ 發送低溫警報專用報告"""
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密 (MAIL_USER / MAIL_PASSWORD)")
            return False

        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_temp
        
        json_text = json.dumps(temp_report_data, ensure_ascii=False, indent=2)
        msg.attach(MIMEText(json_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(temp_report_html, 'html', 'utf-8'))

        try:
            print(f"❄️ 正在透過 Gmail 發送低溫警報給 {self.target}...")
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
            print("   🔑 正在登入...")
            server.login(self.user, self.password)
            
            print("   📨 正在傳送...")
            server.sendmail(self.user, self.target, msg.as_string())
            server.quit()
            
            print(f"✅ 低溫警報發送成功！")
            return True
            
        except Exception as e:
            print(f"❌ 低溫警報發送失敗: {e}")
            traceback.print_exc()
            return False


# ================= 主服務類別 =================

class WeatherMonitorService:
    """氣象監控服務"""
    
    def __init__(self, username: str, password: str,
                 teams_webhook_url: str = '',
                 excel_path: str = EXCEL_FILE_PATH):
        
        print("🔧 正在初始化氣象監控服務...")
        self.crawler = PortWeatherCrawler(username, password, excel_path, auto_login=False)
        self.analyzer = WeatherRiskAnalyzer()
        self.notifier = TeamsNotifier(teams_webhook_url)
        self.db = WeatherDatabase()
        self.email_notifier = GmailRelayNotifier()
        self.chart_generator = ChartGenerator()
        
        print(f"✅ 系統初始化完成，共載入 {len(self.crawler.port_list)} 個港口")
    
    def run_daily_monitoring(self) -> Dict[str, Any]:
        """執行每日監控"""
        print("=" * 80)
        print(f"🚀 開始執行每日氣象監控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        # 1. 下載資料
        print("\n📡 步驟 1: 下載所有港口氣象資料...")
        download_stats = self.crawler.fetch_all_ports()
        
        # 2. 分析風險
        print("\n🔍 步驟 2: 分析港口風險...")
        risk_assessments = self._analyze_all_ports()
        
        # 3. 生成圖表
        print(f"\n📈 步驟 3: 生成氣象趨勢圖...")
        self._generate_charts(risk_assessments)
        charts_generated = sum(1 for r in risk_assessments if r.chart_base64_list)
        print(f"   ✅ 成功為 {charts_generated}/{len(risk_assessments)} 個港口生成圖表")
        
        # 4. 發送 Teams 通知
        teams_sent = False
        if self.notifier.webhook_url:
            print("\n📢 步驟 4: 發送 Teams 通知...")
            teams_sent = self.notifier.send_risk_alert(risk_assessments)
        else:
            print("\n⚠️ 步驟 4: 跳過 Teams 通知 (未設定 Webhook)")
        
        # 5. 生成報告
        print("\n📊 步驟 5: 生成數據報告...")
        report_data = self._generate_data_report(download_stats, risk_assessments, teams_sent)
        
        # 6. 發送主要氣象報告 Email
        print("\n📧 步驟 6: 發送主要氣象報告 Email...")
        report_html = self._generate_html_report(risk_assessments)
        
        email_sent = False
        try:
            email_sent = self.email_notifier.send_trigger_email(
                report_data, report_html, None
            )
        except Exception as e:
            print(f"⚠️ 主要報告發信過程發生異常: {e}")
            traceback.print_exc()
        
        # ✅ 7. 發送低溫警報 Email（獨立郵件）
        print("\n❄️ 步驟 7: 檢查是否需要發送低溫警報...")
        temp_assessments = [a for a in risk_assessments if a.min_temperature < RISK_THRESHOLDS['temp_freezing']]
        
        temp_email_sent = False
        if temp_assessments:
            print(f"   🔍 發現 {len(temp_assessments)} 個港口有低溫警告，準備發送專用報告...")
            temp_report_data = self._generate_temperature_report_data(temp_assessments)
            temp_report_html = self._generate_temperature_html_report(temp_assessments)
            
            try:
                temp_email_sent = self.email_notifier.send_temperature_alert(
                    temp_report_data, temp_report_html
                )
            except Exception as e:
                print(f"⚠️ 低溫警報發信過程發生異常: {e}")
                traceback.print_exc()
        else:
            print("   ✅ 無低溫警告港口，跳過低溫警報發送")
        
        report_data['email_sent'] = email_sent
        report_data['teams_sent'] = teams_sent
        report_data['temp_email_sent'] = temp_email_sent
        report_data['temp_ports_count'] = len(temp_assessments)
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print(f"   - 風險港口: {len(risk_assessments)}")
        print(f"   - 低溫港口: {len(temp_assessments)}")
        print(f"   - Teams 通知: {'✅' if teams_sent else '❌'}")
        print(f"   - 主要報告 Email: {'✅' if email_sent else '❌'}")
        print(f"   - 低溫警報 Email: {'✅' if temp_email_sent else '❌'}")
        print("=" * 80)
        
        return report_data
    
    def _analyze_all_ports(self) -> List[RiskAssessment]:
        """分析所有港口"""
        assessments = []
        total = len(self.crawler.port_list)
        
        for i, port_code in enumerate(self.crawler.port_list, 1):
            try:
                data = self.db.get_latest_content(port_code)
                if not data:
                    continue
                
                content, issued, name = data
                info = self.crawler.get_port_info(port_code)
                if not info:
                    continue
                
                res = self.analyzer.analyze_port_risk(port_code, info, content, issued)
                
                if res:
                    assessments.append(res)
                    print(f"   [{i}/{total}] ⚠️ {port_code}: {self.analyzer.get_risk_label(res.risk_level)}")
                else:
                    print(f"   [{i}/{total}] ✅ {port_code}: 安全")
                    
            except Exception as e:
                print(f"   [{i}/{total}] ❌ {port_code}: {e}")
        
        assessments.sort(key=lambda x: x.risk_level, reverse=True)
        return assessments
    
    def _generate_charts(self, assessments: List[RiskAssessment]):
        """生成圖表並將 Base64 存入 assessment"""
        
        if not assessments:
            print("   ⚠️ 沒有風險港口需要生成圖表")
            return
        
        chart_targets = assessments[:20]
        
        print(f"   📊 準備為 {len(chart_targets)} 個港口生成圖表...")
        
        success_count = 0
        for i, assessment in enumerate(chart_targets, 1):
            print(f"   [{i}/{len(chart_targets)}] 正在處理 {assessment.port_code}...")
            
            # 1. 風速圖
            b64_wind = self.chart_generator.generate_wind_chart(
                assessment, assessment.port_code
            )
            if b64_wind:
                assessment.chart_base64_list.append(b64_wind)
                success_count += 1
                print(f"      ✅ 風速圖已生成")
            
            # 2. 浪高圖
            if assessment.max_wave >= RISK_THRESHOLDS['wave_caution']:
                b64_wave = self.chart_generator.generate_wave_chart(
                    assessment, assessment.port_code
                )
                if b64_wave:
                    assessment.chart_base64_list.append(b64_wave)
                    print(f"      ✅ 浪高圖已生成")
            
            # ✅ 3. 溫度圖（當有低溫警告時）
            if assessment.min_temperature < RISK_THRESHOLDS['temp_freezing']:
                b64_temp = self.chart_generator.generate_temperature_chart(
                    assessment, assessment.port_code
                )
                if b64_temp:
                    assessment.chart_base64_list.append(b64_temp)
                    print(f"      ✅ 溫度圖已生成")
        
        print(f"   ✅ 圖表生成完成：{success_count}/{len(chart_targets)} 個港口成功")
        
    def _generate_data_report(self, stats, assessments, teams_sent):
        """生成 JSON 報告"""
        return {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_ports_checked": stats.get('total', 0),
                "risk_ports_found": len(assessments),
                "danger_count": len([a for a in assessments if a.risk_level == 3]),
                "warning_count": len([a for a in assessments if a.risk_level == 2]),
                "caution_count": len([a for a in assessments if a.risk_level == 1]),
            },
            "download_stats": stats,
            "risk_assessments": [a.to_dict() for a in assessments],
            "notifications": {
                "teams_sent": teams_sent
            }
        }
    
    def _generate_temperature_report_data(self, temp_assessments: List[RiskAssessment]) -> dict:
        """✅ 生成低溫警報專用 JSON 報告"""
        return {
            "timestamp": datetime.now().isoformat(),
            "alert_type": "LOW_TEMPERATURE",
            "summary": {
                "total_ports_with_freezing": len(temp_assessments),
                "min_temperature": min(a.min_temperature for a in temp_assessments),
            },
            "freezing_ports": [
                {
                    "port_code": a.port_code,
                    "port_name": a.port_name,
                    "country": a.country,
                    "min_temperature": a.min_temperature,
                    "min_temp_time_utc": a.min_temp_time_utc,
                    "min_temp_time_lct": a.min_temp_time_lct,
                } for a in temp_assessments
            ]
        }
        
    def _generate_html_report(self, assessments: List[RiskAssessment]) -> str:
        """生成主要氣象風險 HTML 報告"""
        
        def format_time_display(time_str):
            if not time_str:
                return "N/A"
            try:
                if '(' in time_str:
                    return time_str.split('(')[0].strip()
                return time_str
            except:
                return time_str
        
        font_style = "font-family: 'Noto Sans TC', 'Microsoft JhengHei UI', 'Microsoft YaHei UI', 'Segoe UI', Arial, sans-serif;"
        
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        now_str_UTC = f"{utc_now.strftime('%Y-%m-%d %H:%M')} (UTC)"

        if not assessments:
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="margin: 0; padding: 20px; background-color: #F0F4F8; {font_style}">
                <div style="max-width: 900px; margin: 0 auto; background-color: #E8F5E9; padding: 40px; border-left: 8px solid #4CAF50; border-radius: 4px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 15px;">✅</div>
                    <h2 style="margin: 0 0 10px 0; font-size: 28px; color: #2E7D32;">
                        所有港口安全 All Ports Safe
                    </h2>
                    <p style="margin: 0; font-size: 18px; color: #1B5E20; line-height: 1.8;">
                        未來 48 小時內所有靠泊港口均處於安全範圍<br>
                        All ports are within safe limits for the next 48 hours.
                    </p>
                    <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #A5D6A7; font-size: 13px; color: #558B2F;">
                        📅 最後更新時間 Last Updated: {now_str_TPE} / {now_str_UTC}
                    </div>
                </div>
            </body>
            </html>
            """
            
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        summary_styles = {
            3: {
                'emoji': '🔴', 
                'label': 'HIGH RISK', 
                'label_zh': '高度風險', 
                'color': '#DC2626', 
                'bg': '#FEF2F2', 
                'border': '#FCA5A5',
                'criteria': '風速 Wind > 34 kts / 陣風 Gust > 41 kts / 浪高 Wave > 4.0 m'
            },
            2: {
                'emoji': '🟠', 
                'label': 'MEDIUM RISK', 
                'label_zh': '中度風險', 
                'color': '#F59E0B', 
                'bg': '#FFFBEB', 
                'border': '#FCD34D',
                'criteria': '風速 Wind > 28 kts / 陣風 Gust > 34 kts / 浪高 Wave > 3.5 m / 氣溫 < 0°C / 氣壓 < 1000 hPa / 能見度 < 5km'
            },
            1: {
                'emoji': '🟡', 
                'label': 'LOW RISK', 
                'label_zh': '低度風險', 
                'color': '#0EA5E9', 
                'bg': '#F0F9FF', 
                'border': '#7DD3FC',
                'criteria': '風速 Wind > 22 kts / 陣風 Gust > 28 kts / 浪高 Wave > 2.5 m'
            }
        }

        html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body bgcolor="#F0F4F8" style="margin: 0; padding: 0; {font_style}">
                    <center>
                    <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#ffffff" style="max-width: 900px; margin: 20px auto;">
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td bgcolor="#7F1D1D" style="padding: 8px 20px;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td align="left" style="font-size: 13px; color: #FEE2E2; font-weight: bold;">
                                                    📅 最後更新時間 Last Updated:
                                                </td>
                                                <td align="right" style="font-size: 13px; color: #ffffff; font-weight: bold;">
                                                    {now_str_TPE} | {now_str_UTC}
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 25px 25px 0 25px;">
                            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td bgcolor="#1E3A8A" style="padding: 20px 25px; border-radius: 8px 8px 0 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                        <h2 style="margin: 0; font-size: 24px; font-weight: 700; color: #ffffff; line-height: 1.4; letter-spacing: 0.3px;">
                                            WHL Port Weather Risk Monitor
                                        </h2>
                                        <p style="margin: 8px 0 0 0; font-size: 16px; font-weight: 500; color: #E0E7FF; line-height: 1.3;">
                                            Weather Warning for Next 48 Hours
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 3px solid #1E3A8A; border-top: none;">
                """
        
        for level in [3, 2, 1]:
            ports = risk_groups[level]
            style = summary_styles[level]
            
            if ports:
                port_codes = ', '.join([f"<strong style='font-size: 17px; color: {style['color']};'>{p.port_code}</strong>" for p in ports])
                html += f"""
                                <tr>
                                    <td style="padding: 18px 20px; border-bottom: 2px solid {style['border']}; background-color: {style['bg']};">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td width="240" valign="middle">
                                                    <div style="font-size: 22px; font-weight: bold; color: {style['color']}; line-height: 1.2;">
                                                        {style['emoji']} {style['label_zh']}
                                                    </div>
                                                    <div style="font-size: 16px; color: {style['color']}; margin-top: 2px; font-weight: 600;">
                                                        {style['label']}
                                                    </div>
                                                </td>
                                                <td width="120" valign="middle" align="center">
                                                    <div style="background-color: {style['color']}; color: #ffffff; font-size: 32px; font-weight: bold; padding: 8px 16px; border-radius: 8px; display: inline-block; min-width: 60px;">
                                                        {len(ports)}
                                                    </div>
                                                </td>
                                                <td style="padding-left: 20px;" valign="middle">
                                                    <div style="font-size: 17px; color: #1F2937; line-height: 1.8; margin-bottom: 8px;">
                                                        {port_codes}
                                                    </div>
                                                    <div style="font-size: 13px; color: #6B7280; line-height: 1.5; font-style: italic;">
                                                        條件 Criteria: {style['criteria']}
                                                    </div>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                """
        
        html += f"""
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px 20px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#F3F4F6">
                                <tr>
                                    <td style="padding: 15px 20px; font-size: 13px; color: #6B7280; text-align: center; border: 1px solid #D1D5DB; border-top: none; border-radius: 0 0 8px 8px;">
                                        <strong style="color: #374151;">資料來源: Weathernews Inc. (WNI)</strong><br>
                                        <span style="color: #9CA3AF;">Data Source: Weathernews Inc. (WNI)</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px 25px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#FFFBEB">
                                <tr>
                                    <td style="padding: 22px 25px; border-left: 5px solid #F59E0B; border-radius: 4px;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding-bottom: 18px; border-bottom: 2px solid #FCD34D;">
                                                    <strong style="font-size: 16px; color: #78350F;">📋 船隊風險應對措施 Fleet Risk Response Actions</strong>
                                                </td>
                                            </tr>
                                            
                                            <tr>
                                                <td style="padding-top: 15px; padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">請立即確認貴輪靠泊港口是否在風險名單中,並評估可能影響</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Immediately verify if your vessel's port of call is on the alert list and assess potential impacts.</span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">根據風險等級制定應對策略,如:拋錨候泊改為安全水域備車漂航、提前申請額外拖船協助、加強繫泊纜繩、或調整靠離泊計畫等</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Formulate response strategies based on risk levels, including Drifting instant anchor, strengthening mooring lines, arranging extra tug assistance in advance, or adjusting berthing/unberthing schedules.</span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td>
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">與船管PIC、當地代理保持密切聯繫,及時報告船舶狀態和決策</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Maintain close contact with the PIC and local agents; promptly report vessel status and decisions.</span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding: 0 25px 25px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding-top: 20px; padding-bottom: 20px; border-top: 3px dashed #D1D5DB; text-align: center;">
                                        <strong style="font-size: 16px; color: #374151;">⬇️ 以下為各港口詳細氣象風險資料 ⬇️</strong>
                                        <br>
                                        <span style="font-size: 12px; color: #9CA3AF; letter-spacing: 0.5px;">DETAILED WEATHER RISK DATA FOR EACH PORT</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                """

        styles_detail = {
            3: {
                'color': '#DC2626', 
                'bg': '#FEF2F2', 
                'title_zh': '🔴 危險等級港口', 
                'title_en': 'HIGH RISK LEVEL PORTS',
                'border': '#DC2626', 
                'header_bg': '#FEE2E2', 
                'desc': '條件 Criteria: 風速 Wind > 34 kts / 陣風 Gust > 41 kts / 浪高 Wave > 4.0 m'
            },
            2: {
                'color': '#F59E0B', 
                'bg': '#FFFBEB', 
                'title_zh': '🟠 警告等級港口', 
                'title_en': 'MEDIUM RISK LEVEL PORTS',
                'border': '#F59E0B', 
                'header_bg': '#FEF3C7', 
                'desc': '條件 Criteria: 風速 Wind > 28 kts / 陣風 Gust > 34 kts / 浪高 Wave > 3.5 m / 氣溫 < 0°C / 氣壓 < 1000 hPa / 能見度 < 5km'
            },
            1: {
                'color': '#0EA5E9', 
                'bg': '#F0F9FF', 
                'title_zh': '🟡 注意等級港口', 
                'title_en': 'LOW RISK LEVEL PORTS',
                'border': '#0EA5E9', 
                'header_bg': '#E0F2FE', 
                'desc': '條件 Criteria: 風速 Wind > 22 kts / 陣風 Gust > 28 kts / 浪高 Wave > 2.5 m'
            }
        }

        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports:
                continue
            
            style = styles_detail[level]
            
            html += f"""
                        <tr>
                            <td style="padding: 0 25px;">
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 10px;">
                                    <tr>
                                        <td style="background-color: {style['color']}; color: white; padding: 10px 15px; font-weight: bold; font-size: 15px;">
                                            {style['title_zh']} {style['title_en']}
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="font-size: 11px; color: #666; padding: 5px 0 8px 0;">
                                            {style['desc']}
                                        </td>
                                    </tr>
                                </table>
                                
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 1px solid #E5E7EB; margin-bottom: 30px;">
                                    <tr style="background-color: {style['header_bg']}; font-size: 12px; color: #666;">
                                        <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 18%; font-weight: 600;">港口資訊<br>Port Info</th>
                                        <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 25%; font-weight: 600;">未來 48 Hrs 氣象數據<br>48-Hr Weather Data</th>
                                        <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 57%; font-weight: 600;">高風險時段<br>High Risk Period</th>
                                    </tr>
            """
            
            for index, p in enumerate(ports):
                row_bg = "#FFFFFF" if index % 2 == 0 else "#FAFBFC"
                
                wind_style = "color: #DC2626; font-weight: bold;" if p.max_wind_kts >= 28 else "color: #333;"
                gust_style = "color: #DC2626; font-weight: bold;" if p.max_gust_kts >= 34 else "color: #333;"
                wave_style = "color: #DC2626; font-weight: bold;" if p.max_wave >= 3.5 else "color: #333;"
                
                if p.risk_level == 3:
                    risk_level_bg = "#FEF2F2"
                    risk_level_color = "#DC2626"
                    risk_level_text = "高風險 HIGH RISK"
                    risk_level_icon = "🔴"
                elif p.risk_level == 2:
                    risk_level_bg = "#FFFBEB"
                    risk_level_color = "#F59E0B"
                    risk_level_text = "中風險 MEDIUM RISK"
                    risk_level_icon = "🟠"
                else:
                    risk_level_bg = "#F0F9FF"
                    risk_level_color = "#0EA5E9"
                    risk_level_text = "低風險 LOW RISK"
                    risk_level_icon = "🟡"

                if p.max_wind_kts >= 34:
                    wind_level_text = "強風"
                    wind_level_color = "#DC2626"
                elif p.max_wind_kts >= 28:
                    wind_level_text = "中強風"
                    wind_level_color = "#F59E0B"
                elif p.max_wind_kts >= 22:
                    wind_level_text = "微風"
                    wind_level_color = "#0EA5E9"
                else:
                    wind_level_text = ""
                    wind_level_color = "#333"

                if p.max_gust_kts >= 41:
                    gust_level_text = "危險陣風"
                    gust_level_color = "#DC2626"
                elif p.max_gust_kts >= 34:
                    gust_level_text = "強陣風"
                    gust_level_color = "#F59E0B"
                elif p.max_gust_kts >= 28:
                    gust_level_text = "中陣風"
                    gust_level_color = "#0EA5E9"
                else:
                    gust_level_text = ""
                    gust_level_color = "#333"

                if p.max_wave >= 4.0:
                    wave_level_text = "危險浪高"
                    wave_level_color = "#DC2626"
                elif p.max_wave >= 3.5:
                    wave_level_text = "高浪"
                    wave_level_color = "#F59E0B"
                elif p.max_wave >= 2.5:
                    wave_level_text = "中浪"
                    wave_level_color = "#0EA5E9"
                else:
                    wave_level_text = ""
                    wave_level_color = "#333"

                if p.risk_periods:
                    try:
                        first_risk = datetime.strptime(p.risk_periods[0]['time'], '%Y-%m-%d %H:%M')
                        last_risk = datetime.strptime(p.risk_periods[-1]['time'], '%Y-%m-%d %H:%M')
                        duration_hours = int((last_risk - first_risk).total_seconds() / 3600) + 3
                        risk_duration = str(min(duration_hours, 48))
                    except:
                        risk_duration = str(len(p.risk_periods) * 3)
                else:
                    risk_duration = "0"

                w_utc = format_time_display(p.max_wind_time_utc)
                w_lct = format_time_display(p.max_wind_time_lct)
                g_utc = format_time_display(p.max_gust_time_utc)
                g_lct = format_time_display(p.max_gust_time_lct)
                v_utc = format_time_display(p.max_wave_time_utc)
                v_lct = format_time_display(p.max_wave_time_lct)
                
                pres_utc = format_time_display(p.min_pressure_time_utc) if p.min_pressure_time_utc else "N/A"
                pres_lct = format_time_display(p.min_pressure_time_lct) if p.min_pressure_time_lct else "N/A"

                show_pressure_warning = p.min_pressure < RISK_THRESHOLDS['pressure_low']
                show_vis_warning = len(p.poor_visibility_periods) > 0
                
                html += f"""
                            <tr style="background-color: {row_bg}; border-bottom: 1px solid #E5E7EB;">
                            <td valign="top" style="padding: 15px; width: 25%;">
                                <div style="font-size: 20px; font-weight: 800; color: #1E3A8A; margin-bottom: 4px; line-height: 1;">
                                    {p.port_code}
                                </div>
                                <div style="font-size: 13px; color: #4B5563; font-weight: 600; margin-bottom: 4px;">
                                    {p.port_name}
                                </div>
                                <div style="font-size: 12px; color: #6B7280; margin-bottom: 8px;">
                                    📍 {p.country}
                                </div>
                                <div>
                                    <span style="background-color: {risk_level_bg}; color: {risk_level_color}; font-size: 11px; font-weight: 700; padding: 3px 6px; border-radius: 3px; display: inline-block;">
                                        {risk_level_icon} {risk_level_text}
                                    </span>
                                </div>
                            </td>

                            <td valign="top" style="padding: 15px; width: 30%;">
                                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                    <tr>
                                        <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">💨</td>
                                        <td valign="top">
                                            <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">風速 Wind</span>
                                            <span style="{wind_style} font-size: 16px; font-weight: 700;">
                                                {p.max_wind_kts:.0f} <span style="font-size: 12px; font-weight: 500;">kts</span>
                                            </span>
                                            <span style="font-size: 11px; color: {wind_level_color}; margin-left: 6px; font-weight: 600;">
                                                {wind_level_text}
                                            </span>
                                        </td>
                                    </tr>
                                </table>
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                    <tr>
                                        <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">🌪️</td>
                                        <td valign="top">
                                            <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">陣風 Gust</span>
                                            <span style="{gust_style} font-size: 16px; font-weight: 700;">
                                                {p.max_gust_kts:.0f} <span style="font-size: 12px; font-weight: 500;">kts</span>
                                            </span>
                                            <span style="font-size: 11px; color: {gust_level_color}; margin-left: 6px; font-weight: 600;">
                                                {gust_level_text}
                                            </span>
                                        </td>
                                    </tr>
                                </table>
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                    <tr>
                                        <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">🌊</td>
                                        <td valign="top">
                                            <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">浪高 Wave</span>
                                            <span style="{wave_style} font-size: 16px; font-weight: 700;">
                                                {p.max_wave:.1f} <span style="font-size: 12px; font-weight: 500;">m</span>
                                            </span>
                                            <span style="font-size: 11px; color: {wave_level_color}; margin-left: 6px; font-weight: 600;">
                                                {wave_level_text}
                                            </span>
                                        </td>
                                    </tr>
                                </table>
                """
                
                if show_pressure_warning:
                    html += f"""
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                    <tr>
                                        <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">🌀</td>
                                        <td valign="top">
                                            <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">氣壓 Pressure</span>
                                            <span style="color: #DC2626; font-size: 16px; font-weight: 700;">
                                                {p.min_pressure:.0f} <span style="font-size: 12px; font-weight: 500;">hPa</span>
                                            </span>
                                            <span style="font-size: 11px; color: #DC2626; margin-left: 6px; font-weight: 600;">
                                                低氣壓
                                            </span>
                                        </td>
                                    </tr>
                                </table>
                    """
                
                if show_vis_warning:
                    vis_periods_html = "<br>".join([
                        f"• {period['time_lct'].split()[1]}: {period['visibility_km']:.1f} km"
                        for period in p.poor_visibility_periods[:3]
                    ])
                    
                    if len(p.poor_visibility_periods) > 3:
                        vis_periods_html += f"<br>... 及其他 {len(p.poor_visibility_periods) - 3} 個時段"
                    
                    html += f"""
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                    <tr>
                                        <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">🌫️</td>
                                        <td valign="top">
                                            <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">能見度不良時段</span>
                                            <span style="color: #DC2626; font-size: 11px; font-weight: 600; line-height: 1.6;">
                                                {vis_periods_html}
                                            </span>
                                        </td>
                                    </tr>
                                </table>
                    """
                
                html += f"""
                            </td>

                            <td valign="top" style="padding: 15px; width: 45%;">
                                <div style="margin-bottom: 12px;">
                                    <span style="background-color: #FEF2F2; color: #B91C1C; border: 1px solid #FCA5A5; font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px; display: inline-block; line-height: 1.4;">
                                        ⚠️ 風險因素 Risk Factors: {', '.join(p.risk_factors[:3])}
                                    </span>
                                </div>
                                
                                <table border="0" cellpadding="2" cellspacing="0" width="100%" style="font-size: 12px; border-collapse: collapse;">
                                    <tr>
                                        <td valign="top" style="color: #6B7280; width: 85px; padding-bottom: 8px; line-height: 1.3;">
                                            最大風速<br><span style="font-size: 10px;">Max Wind:</span>
                                        </td>
                                        <td valign="top" style="padding-bottom: 8px;">
                                            <div style="color: #111827; font-weight: 600;">{w_utc} <span style="color: #9CA3AF; font-size: 10px; font-weight: normal;">UTC</span></div>
                                            <div style="color: #4B5563;">{w_lct} <span style="color: #9CA3AF; font-size: 10px;">LT</span></div>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td valign="top" style="color: #6B7280; width: 85px; padding-bottom: 8px; line-height: 1.3;">
                                            最大陣風<br><span style="font-size: 10px;">Max Gust:</span>
                                        </td>
                                        <td valign="top" style="padding-bottom: 8px;">
                                            <div style="color: #111827; font-weight: 600;">{g_utc} <span style="color: #9CA3AF; font-size: 10px; font-weight: normal;">UTC</span></div>
                                            <div style="color: #4B5563;">{g_lct} <span style="color: #9CA3AF; font-size: 10px;">LT</span></div>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td valign="top" style="color: #6B7280; width: 85px; padding-bottom: 8px; line-height: 1.3;">
                                            最大浪高<br><span style="font-size: 10px;">Max Wave:</span>
                                        </td>
                                        <td valign="top" style="padding-bottom: 8px;">
                                            <div style="color: #111827; font-weight: 600;">{v_utc} <span style="color: #9CA3AF; font-size: 10px; font-weight: normal;">UTC</span></div>
                                            <div style="color: #4B5563;">{v_lct} <span style="color: #9CA3AF; font-size: 10px;">LT</span></div>
                                        </td>
                                    </tr>
                """
                
                if show_pressure_warning:
                    html += f"""
                                    <tr>
                                        <td valign="top" style="color: #DC2626; width: 85px; padding-bottom: 8px; line-height: 1.3; font-weight: 600;">
                                            最低氣壓<br><span style="font-size: 10px;">Min Pressure:</span>
                                        </td>
                                        <td valign="top" style="padding-bottom: 8px;">
                                            <div style="color: #DC2626; font-weight: 600;">{pres_utc} <span style="color: #9CA3AF; font-size: 10px; font-weight: normal;">UTC</span></div>
                                            <div style="color: #DC2626;">{pres_lct} <span style="color: #9CA3AF; font-size: 10px;">LT</span></div>
                                        </td>
                                    </tr>
                    """
                
                html += f"""
                                    <tr>
                                        <td valign="top" style="color: #991B1B; width: 85px; padding-top: 8px; border-top: 1px dashed #E5E7EB; font-weight: 600; line-height: 1.3;">
                                            風險持續<br><span style="font-size: 10px;">Duration:</span>
                                        </td>
                                        <td valign="top" style="padding-top: 8px; border-top: 1px dashed #E5E7EB;">
                                            <div style="color: #991B1B; font-weight: 700; font-size: 13px;">
                                                {risk_duration} <span style="font-size: 11px; font-weight: 600;">小時 Hrs</span>
                                            </div>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                """
                
                if hasattr(p, 'chart_base64_list') and p.chart_base64_list:
                    chart_imgs = ""
                    for idx, b64 in enumerate(p.chart_base64_list):
                        b64_clean = b64.replace('\n', '').replace('\r', '').replace(' ', '')
                        chart_imgs += f"""
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                <tr>
                                    <td align="center">
                                        <img src="data:image/png;base64,{b64_clean}" 
                                            width="750" 
                                            style="display:block; max-width: 100%; height: auto; border: 1px solid #ddd;" 
                                            alt="Chart {idx+1}">
                                    </td>
                                </tr>
                            </table>
                        """
                    
                    html += f"""
                            <tr>
                                <td colspan="3" style="padding: 15px; background-color: {row_bg}; border-bottom: 1px solid #eee;">
                                    <div style="font-size: 13px; color: #666; margin-bottom: 8px; font-weight: 600;">
                                        📈 氣象趨勢圖表 Weather Trend Chart:
                                    </div>
                                    {chart_imgs}
                                </td>
                            </tr>
                    """
            
            html += """
                                </table>
                            </td>
                        </tr>
            """

        html += f"""
                        <tr>
                            <td bgcolor="#F8F9FA" align="center" style="padding: 40px 25px; border-top: 3px solid #D1D5DB;">
                                <table border="0" cellpadding="0" cellspacing="0" width="600">
                                    <tr>
                                        <td align="center" style="padding-bottom: 8px;">
                                            <font size="5" color="#1F2937" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                <strong>萬海航運股份有限公司</strong>
                                            </font>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td align="center" style="padding-bottom: 20px;">
                                            <font size="3" color="#4B5563" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                <strong>WAN HAI LINES LTD.</strong>
                                            </font>
                                        </td>
                                    </tr>
                                    
                                    <tr>
                                        <td align="center" style="padding-bottom: 20px;">
                                            <table border="0" cellpadding="0" cellspacing="0" width="120">
                                                <tr>
                                                    <td style="border-top: 2px solid #9CA3AF;"></td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                    
                                    <tr>
                                        <td align="center" style="padding-bottom: 25px;">
                                            <font size="2" color="#374151" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                <strong>Marine Technology Division | Fleet Risk Management Dept.</strong>
                                            </font>
                                        </td>
                                    </tr>
                                    
                                    <tr>
                                        <td>
                                            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#FEF3C7">
                                                <tr>
                                                    <td style="padding: 18px 20px; border-left: 4px solid #F59E0B; border-radius: 4px;">
                                                        <table border="0" cellpadding="0" cellspacing="0">
                                                            <tr>
                                                                <td style="padding-bottom: 8px;">
                                                                    <font size="2" color="#78350F" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                                        <strong>⚠️ 免責聲明 Disclaimer</strong>
                                                                    </font>
                                                                </td>
                                                            </tr>
                                                            <tr>
                                                                <td>
                                                                    <font size="2" color="#92400E" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                                        本信件內容僅供參考,船長仍應依據實際天候狀況與專業判斷採取適當措施。
                                                                        <br>
                                                                        <span style="color: #B45309;">This report is for reference only. Captains should take appropriate actions based on actual weather conditions.</span>
                                                                    </font>
                                                                </td>
                                                            </tr>
                                                        </table>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                    
                                    <tr>
                                        <td align="center" style="padding-top: 25px;">
                                            <font size="1" color="#9CA3AF" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                &copy; {now_str_TPE[:4]} Wan Hai Lines Ltd. All Rights Reserved.
                                            </font>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </center>
            </body>
            </html>
                """
        
        return html

    def _generate_temperature_html_report(self, temp_assessments: List[RiskAssessment]) -> str:
        """✅ 生成低溫警報專用 HTML 報告（含公司通告內容）"""
        
        def format_time_display(time_str):
            if not time_str:
                return "N/A"
            try:
                if '(' in time_str:
                    return time_str.split('(')[0].strip()
                return time_str
            except:
                return time_str
        
        font_style = "font-family: 'Noto Sans TC', 'Microsoft JhengHei UI', 'Microsoft YaHei UI', 'Segoe UI', Arial, sans-serif;"
        
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        now_str_UTC = f"{utc_now.strftime('%Y-%m-%d %H:%M')} (UTC)"

        html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body bgcolor="#F0F4F8" style="margin: 0; padding: 0; {font_style}">
                    <center>
                    <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#ffffff" style="max-width: 900px; margin: 20px auto;">
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td bgcolor="#1E3A8A" style="padding: 8px 20px;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td align="left" style="font-size: 13px; color: #DBEAFE; font-weight: bold;">
                                                    📅 最後更新時間 Last Updated:
                                                </td>
                                                <td align="right" style="font-size: 13px; color: #ffffff; font-weight: bold;">
                                                    {now_str_TPE} | {now_str_UTC}
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 25px 25px 0 25px;">
                            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td bgcolor="#DC2626" style="padding: 20px 25px; border-radius: 8px 8px 0 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                        <h2 style="margin: 0; font-size: 24px; font-weight: 700; color: #ffffff; line-height: 1.4; letter-spacing: 0.3px;">
                                            ❄️ WHL Port Low Temperature Alert
                                        </h2>
                                        <p style="margin: 8px 0 0 0; font-size: 16px; font-weight: 500; color: #FEE2E2; line-height: 1.3;">
                                            低溫警報 - 未來 48 小時氣溫低於冰點港口
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 3px solid #DC2626; border-top: none;">
                                <tr>
                                    <td style="padding: 18px 20px; border-bottom: 2px solid #FCA5A5; background-color: #FEE2E2;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td width="240" valign="middle">
                                                    <div style="font-size: 22px; font-weight: bold; color: #DC2626; line-height: 1.2;">
                                                        ❄️ 低溫警告港口
                                                    </div>
                                                    <div style="font-size: 16px; color: #DC2626; margin-top: 2px; font-weight: 600;">
                                                        FREEZING TEMPERATURE ALERT
                                                    </div>
                                                </td>
                                                <td width="120" valign="middle" align="center">
                                                    <div style="background-color: #DC2626; color: #ffffff; font-size: 32px; font-weight: bold; padding: 8px 16px; border-radius: 8px; display: inline-block; min-width: 60px;">
                                                        {len(temp_assessments)}
                                                    </div>
                                                </td>
                                                <td style="padding-left: 20px;" valign="middle">
                                                    <div style="font-size: 17px; color: #1F2937; line-height: 1.8; margin-bottom: 8px;">
                                                        {', '.join([f"<strong style='font-size: 17px; color: #DC2626;'>{p.port_code}</strong>" for p in temp_assessments])}
                                                    </div>
                                                    <div style="font-size: 13px; color: #6B7280; line-height: 1.5; font-style: italic;">
                                                        條件 Criteria: 氣溫 Temperature < 0°C (32°F)
                                                    </div>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px 20px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#F3F4F6">
                                <tr>
                                    <td style="padding: 15px 20px; font-size: 13px; color: #6B7280; text-align: center; border: 1px solid #D1D5DB; border-top: none; border-radius: 0 0 8px 8px;">
                                        <strong style="color: #374151;">資料來源: Weathernews Inc. (WNI)</strong><br>
                                        <span style="color: #9CA3AF;">Data Source: Weathernews Inc. (WNI)</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- ==================== ✅ 擴充：低溫應對措施（基於公司通告）==================== -->
                    <tr>
                        <td style="padding: 0 25px 25px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#FEE2E2">
                                <tr>
                                    <td style="padding: 22px 25px; border-left: 5px solid #DC2626; border-radius: 4px;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding-bottom: 18px; border-bottom: 2px solid #FCA5A5;">
                                                    <strong style="font-size: 18px; color: #7F1D1D;">❄️ 低溫應對措施 Low Temperature Response Actions</strong>
                                                    <br>
                                                    <span style="font-size: 12px; color: #991B1B; margin-top: 5px; display: block;">
                                                        參考：海技通告 WRK-00-2412-379-2-000-T-CIR | Reference: Maritech Circular
                                                    </span>
                                                </td>
                                            </tr>
                                            
                                            <!-- 第一部分：管路防護 -->
                                            <tr>
                                                <td style="padding-top: 15px; padding-bottom: 5px;">
                                                    <strong style="font-size: 15px; color: #7F1D1D;">🔧 一、管路與設備防護 Piping & Equipment Protection</strong>
                                                </td>
                                            </tr>
                                            
                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">預先排空兩舷甲板淡水管路（Drain Pipes）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Drain all fresh water pipes on both sides of passage way in advance to prevent freezing and bursting.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">2️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">排空其他易凍結設備（救生艇淡水櫃、駕駛台洗窗用水等）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Drain lifeboat fresh water tanks, bridge window washing water, and other vulnerable equipment.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">3️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">檢查並保護暴露在外的管路、閥門及設備</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Inspect and protect exposed pipes, valves, and equipment to prevent freezing damage.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">4️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">對暴露的可活動部件塗抹油脂與防凍劑混合物</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Apply mixture of grease and anti-freeze to exposed movable parts (winches, valves, hinges, etc.).
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                            
                                            <!-- 第二部分：甲板安全 -->
                                            <tr>
                                                <td style="padding-top: 10px; padding-bottom: 5px; border-top: 1px dashed #FCA5A5;">
                                                    <strong style="font-size: 15px; color: #7F1D1D;">🧊 二、甲板防滑與除冰 Deck Anti-Slip & De-Icing</strong>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">5️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">定期剷除甲板冰雪，並在走道撒鹽防止結冰</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Regularly shovel ice/snow from open decks and apply salt on walkways to reduce ice formation.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">6️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">備妥除冰設備（鏟子、撬棍、噴燈等）並置於易取得位置</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Keep de-icing equipment (shovels, crowbars, blow torch, etc.) ready in accessible sheltered areas.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">7️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">確保全體船員配發防寒衣物並加強防滑措施</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Ensure all crew are provided with winter wear and enhance anti-slip measures for crew safety.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                            
                                            <!-- 第三部分：機械設備 -->
                                            <tr>
                                                <td style="padding-top: 10px; padding-bottom: 5px; border-top: 1px dashed #FCA5A5;">
                                                    <strong style="font-size: 15px; color: #7F1D1D;">⚙️ 三、機械設備保護 Machinery Protection</strong>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">8️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">提前啟動並保持機械運轉（舷梯、吊車、起錨機、繫泊絞機等）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Start machinery well in advance and keep running (gangways, cranes, windlass, mooring winches, etc.).
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">9️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">遮蓋暴露的氣動/電動馬達，未使用的存放在溫暖區域</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Cover exposed air/electric motors. Store unused motors in warm areas.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">🔟</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">確保加熱系統正常運作（貨艙、壓載艙、生活區域）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Ensure heating systems are functioning properly in cargo holds, ballast tanks, and accommodation areas.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                            
                                            <!-- 第四部分：救生設備 -->
                                            <tr>
                                                <td style="padding-top: 10px; padding-bottom: 5px; border-top: 1px dashed #FCA5A5;">
                                                    <strong style="font-size: 15px; color: #7F1D1D;">🚤 四、救生設備準備 Life-Saving Equipment</strong>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣1️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">救生艇引擎加熱器保持開啟，燃油櫃液位降低（預留膨脹空間）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Keep lifeboat engine heaters 'ON'. Lower fuel storage tank levels to allow for expansion.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣2️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">救助艇完全遮蓋並固定，引水梯存放在遮蔽處</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Rescue boats fully covered and secured. Pilot ladder stowed in sheltered place.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                            
                                            <!-- 第五部分：航行安全 -->
                                            <tr>
                                                <td style="padding-top: 10px; padding-bottom: 5px; border-top: 1px dashed #FCA5A5;">
                                                    <strong style="font-size: 15px; color: #7F1D1D;">⚓ 五、航行與靠泊安全 Navigation & Berthing Safety</strong>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣3️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">向船長報告並遵守平/停/靠離泊守則（寒風強、水流強）</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Report to Master and obey navigation/berthing/unberthing procedures (strong wind & current).
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td style="padding-bottom: 12px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣4️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">確認船舶穩度足以應對結冰造成的 GM 損失</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Ensure ship has stability sufficient to counter loss of GM due to ice accretion (refer to Stability Manual).
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>

                                            <tr>
                                                <td>
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                                        <tr>
                                                            <td width="20" valign="top" style="font-size: 14px;">1️⃣5️⃣</td>
                                                            <td>
                                                                <strong style="font-size: 14px; color: #7F1D1D; line-height: 1.5;">與船管 PIC、當地代理保持密切聯繫，及時報告船舶狀態</strong>
                                                                <br>
                                                                <span style="font-size: 13px; color: #991B1B; line-height: 1.4;">
                                                                    Maintain close contact with PIC and local agents; promptly report vessel status and decisions.
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                            
                                            <!-- 警告訊息 -->
                                            <tr>
                                                <td style="padding-top: 15px;">
                                                    <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#7F1D1D" style="border-radius: 4px;">
                                                        <tr>
                                                            <td style="padding: 12px 15px;">
                                                                <strong style="font-size: 13px; color: #FEE2E2; line-height: 1.6;">
                                                                    ⚠️ 警告：寒潮可能造成船舶沉沒、擱淺、主輔機冷卻吸口冰堵、錨泊設備損壞等嚴重後果，請務必提高警覺！
                                                                </strong>
                                                                <br>
                                                                <span style="font-size: 12px; color: #FECACA; line-height: 1.5;">
                                                                    WARNING: Cold fronts may cause serious consequences such as ship sinking, stranding, ice blockage on cooling suction, anchoring equipment damage, etc. Stay alert!
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding: 0 25px 25px 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding-top: 20px; padding-bottom: 20px; border-top: 3px dashed #D1D5DB; text-align: center;">
                                        <strong style="font-size: 16px; color: #374151;">⬇️ 以下為各港口詳細低溫資料 ⬇️</strong>
                                        <br>
                                        <span style="font-size: 12px; color: #9CA3AF; letter-spacing: 0.5px;">DETAILED LOW TEMPERATURE DATA FOR EACH PORT</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 1px solid #E5E7EB; margin-bottom: 30px;">
                                <tr style="background-color: #FEE2E2; font-size: 12px; color: #666;">
                                    <th align="left" style="padding: 10px; border-bottom: 2px solid #DC2626; width: 18%; font-weight: 600;">港口資訊<br>Port Info</th>
                                    <th align="left" style="padding: 10px; border-bottom: 2px solid #DC2626; width: 25%; font-weight: 600;">溫度資訊<br>Temperature Data</th>
                                    <th align="left" style="padding: 10px; border-bottom: 2px solid #DC2626; width: 57%; font-weight: 600;">最低溫時間<br>Minimum Temperature Time</th>
                                </tr>
        """
        
        for index, p in enumerate(temp_assessments):
            row_bg = "#FFFFFF" if index % 2 == 0 else "#FAFBFC"
            
            temp_utc = format_time_display(p.min_temp_time_utc) if p.min_temp_time_utc else "N/A"
            temp_lct = format_time_display(p.min_temp_time_lct) if p.min_temp_time_lct else "N/A"
            
            html += f"""
                                <tr style="background-color: {row_bg}; border-bottom: 1px solid #E5E7EB;">
                                <td valign="top" style="padding: 15px; width: 25%;">
                                    <div style="font-size: 20px; font-weight: 800; color: #DC2626; margin-bottom: 4px; line-height: 1;">
                                        {p.port_code}
                                    </div>
                                    <div style="font-size: 13px; color: #4B5563; font-weight: 600; margin-bottom: 4px;">
                                        {p.port_name}
                                    </div>
                                    <div style="font-size: 12px; color: #6B7280; margin-bottom: 8px;">
                                        📍 {p.country}
                                    </div>
                                    <div>
                                        <span style="background-color: #FEE2E2; color: #DC2626; font-size: 11px; font-weight: 700; padding: 3px 6px; border-radius: 3px; display: inline-block;">
                                            ❄️ 低溫警告 FREEZING
                                        </span>
                                    </div>
                                </td>

                                <td valign="top" style="padding: 15px; width: 30%;">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="24" valign="top" style="font-size: 16px; padding-top: 2px;">❄️</td>
                                            <td valign="top">
                                                <span style="font-size: 11px; color: #6B7280; text-transform: uppercase; display: block; line-height: 1; margin-bottom: 2px;">最低氣溫 Min Temp</span>
                                                <span style="color: #DC2626; font-size: 20px; font-weight: 700;">
                                                    {p.min_temperature:.1f} <span style="font-size: 14px; font-weight: 500;">°C</span>
                                                </span>
                                                <br>
                                                <span style="color: #DC2626; font-size: 16px; font-weight: 600;">
                                                    ({p.min_temperature * 9/5 + 32:.1f} °F)
                                                </span>
                                            </td>
                                        </tr>
                                    </table>
                                </td>

                                <td valign="top" style="padding: 15px; width: 45%;">
                                    <table border="0" cellpadding="2" cellspacing="0" width="100%" style="font-size: 12px; border-collapse: collapse;">
                                        <tr>
                                            <td valign="top" style="color: #DC2626; width: 85px; padding-bottom: 8px; line-height: 1.3; font-weight: 600;">
                                                最低溫時間<br><span style="font-size: 10px;">Min Temp Time:</span>
                                            </td>
                                            <td valign="top" style="padding-bottom: 8px;">
                                                <div style="color: #DC2626; font-weight: 600;">{temp_utc} <span style="color: #9CA3AF; font-size: 10px; font-weight: normal;">UTC</span></div>
                                                <div style="color: #DC2626;">{temp_lct} <span style="color: #9CA3AF; font-size: 10px;">LT</span></div>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
            """
            
            # 溫度圖表
            if hasattr(p, 'chart_base64_list') and p.chart_base64_list:
                temp_chart = None
                for b64 in p.chart_base64_list:
                    if len(b64) > 0:
                        temp_chart = b64
                
                if temp_chart:
                    b64_clean = temp_chart.replace('\n', '').replace('\r', '').replace(' ', '')
                    html += f"""
                            <tr>
                                <td colspan="3" style="padding: 15px; background-color: {row_bg}; border-bottom: 1px solid #eee;">
                                    <div style="font-size: 13px; color: #666; margin-bottom: 8px; font-weight: 600;">
                                        📈 溫度趨勢圖表 Temperature Trend Chart:
                                    </div>
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td align="center">
                                                <img src="data:image/png;base64,{b64_clean}" 
                                                    width="750" 
                                                    style="display:block; max-width: 100%; height: auto; border: 1px solid #ddd;" 
                                                    alt="Temperature Chart">
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                    """
        
        html += f"""
                            </table>
                        </td>
                    </tr>
                    
                    <tr>
                        <td bgcolor="#F8F9FA" align="center" style="padding: 40px 25px; border-top: 3px solid #D1D5DB;">
                            <table border="0" cellpadding="0" cellspacing="0" width="600">
                                <tr>
                                    <td align="center" style="padding-bottom: 8px;">
                                        <font size="5" color="#1F2937" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                            <strong>萬海航運股份有限公司</strong>
                                        </font>
                                    </td>
                                </tr>
                                <tr>
                                    <td align="center" style="padding-bottom: 20px;">
                                        <font size="3" color="#4B5563" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                            <strong>WAN HAI LINES LTD.</strong>
                                        </font>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td align="center" style="padding-bottom: 20px;">
                                        <table border="0" cellpadding="0" cellspacing="0" width="120">
                                            <tr>
                                                <td style="border-top: 2px solid #9CA3AF;"></td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td align="center" style="padding-bottom: 25px;">
                                        <font size="2" color="#374151" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                            <strong>Marine Technology Division | Fleet Risk Management Dept.</strong>
                                        </font>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td>
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#FEF3C7">
                                            <tr>
                                                <td style="padding: 18px 20px; border-left: 4px solid #F59E0B; border-radius: 4px;">
                                                    <table border="0" cellpadding="0" cellspacing="0">
                                                        <tr>
                                                            <td style="padding-bottom: 8px;">
                                                                <font size="2" color="#78350F" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                                    <strong>⚠️ 免責聲明 Disclaimer</strong>
                                                                </font>
                                                            </td>
                                                        </tr>
                                                        <tr>
                                                            <td>
                                                                <font size="2" color="#92400E" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                                    本信件內容僅供參考,船長仍應依據實際天候狀況與專業判斷採取適當措施。
                                                                    <br>
                                                                    <span style="color: #B45309;">This report is for reference only. Captains should take appropriate actions based on actual weather conditions.</span>
                                                                </font>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td align="center" style="padding-top: 25px;">
                                        <font size="1" color="#9CA3AF" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                            &copy; {now_str_TPE[:4]} Wan Hai Lines Ltd. All Rights Reserved.
                                        </font>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </center>
        </body>
        </html>
        """
        
        return html

    
    def save_report_to_file(self, report, output_dir='reports'):
        """儲存報告到檔案"""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(output_dir, f"report_{timestamp}.json")
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"📄 報告已儲存: {path}")
        return path


# ================= 主程式 =================

def main():
    """主程式進入點"""
    
    # 檢查必要環境變數
    if not AEDYN_USERNAME or not AEDYN_PASSWORD:
        print("❌ 錯誤: 未設定 AEDYN_USERNAME 或 AEDYN_PASSWORD")
        sys.exit(1)
    
    if not MAIL_USER or not MAIL_PASSWORD:
        print("⚠️ 警告: 未設定 MAIL_USER 或 MAIL_PASSWORD，將無法發送 Email")
    
    try:
        # 初始化服務
        service = WeatherMonitorService(
            username=AEDYN_USERNAME,
            password=AEDYN_PASSWORD,
            teams_webhook_url=TEAMS_WEBHOOK_URL
        )
        
        # 執行監控
        report = service.run_daily_monitoring()
        
        # 儲存報告
        service.save_report_to_file(report)
        
        # 輸出 JSON (供 GitHub Actions 使用)
        print("\n" + "="*80)
        print("📤 JSON OUTPUT (for GitHub Actions):")
        print("="*80)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        
        # 根據結果設定退出碼
        if report.get('email_sent', False):
            sys.exit(0)  # 成功
        else:
            sys.exit(1)  # 失敗
        
    except KeyboardInterrupt:
        print("\n⚠️ 使用者中斷執行")
        sys.exit(130)
        
    except Exception as e:
        print(f"\n❌ 執行過程發生嚴重錯誤: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
                                                

