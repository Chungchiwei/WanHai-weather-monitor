# n8n_weather_monitor.py
import os
import sys
import json
import traceback
import smtplib
import io  # 新增
import base64 # 新增
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
}

@dataclass
class RiskAssessment:
    """風險評估結果資料結構"""
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
    
    raw_records: Optional[List[WeatherRecord]] = None
    chart_base64_list: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ['raw_records', 'chart_base64_list']:
            d.pop(key, None)
        return d
# ================= 繪圖模組 (修改版) =================

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
            
            # 🎨 使用更專業的樣式
            plt.style.use('default')
            
            # 🔥 設定圖表尺寸和 DPI
            fig, ax = plt.subplots(figsize=(16, 7), dpi=120)
            
            # 設定背景顏色（漸層效果的替代方案）
            fig.patch.set_facecolor('#FFFFFF')
            ax.set_facecolor('#F8FAFC')
            
            # ==================== 繪製風險區域背景 ====================
            # 危險區域（紅色）
            ax.axhspan(RISK_THRESHOLDS['wind_danger'], ax.get_ylim()[1] if len(df) > 0 else 60, 
                    facecolor='#FEE2E2', alpha=0.3, zorder=0)
            # 警告區域（橙色）
            ax.axhspan(RISK_THRESHOLDS['wind_warning'], RISK_THRESHOLDS['wind_danger'], 
                    facecolor='#FEF3C7', alpha=0.3, zorder=0)
            # 注意區域（黃色）
            ax.axhspan(RISK_THRESHOLDS['wind_caution'], RISK_THRESHOLDS['wind_warning'], 
                    facecolor='#FEF9C3', alpha=0.3, zorder=0)
            
            # ==================== 繪製主要數據線 ====================
            # 風速線（藍色，粗實線）
            line1 = ax.plot(df['time'], df['wind_speed'], 
                            color='#1E40AF', 
                            linewidth=3.5, 
                            marker='o', 
                            markersize=7,
                            markerfacecolor='#3B82F6',
                            markeredgecolor='#1E40AF',
                            markeredgewidth=1.5,
                            label='Wind Speed',
                            zorder=5,
                            alpha=0.9)
            
            # 陣風線（紅色，虛線）
            line2 = ax.plot(df['time'], df['wind_gust'], 
                            color='#DC2626', 
                            linewidth=3, 
                            linestyle='--',
                            marker='s', 
                            markersize=6,
                            markerfacecolor='#EF4444',
                            markeredgecolor='#DC2626',
                            markeredgewidth=1.5,
                            label='Wind Gust',
                            zorder=5,
                            alpha=0.9)
            
            # ==================== 填充區域 ====================
            # 風速曲線下方填充（淡藍色）
            ax.fill_between(df['time'], df['wind_speed'], 
                            alpha=0.2, 
                            color='#3B82F6', 
                            zorder=2)
            
            # 高風險時段特別標註（橙色填充）
            high_risk_mask = df['wind_speed'] >= RISK_THRESHOLDS['wind_caution']
            if high_risk_mask.any():
                ax.fill_between(df['time'], 
                            df['wind_speed'], 
                            where=high_risk_mask,
                            interpolate=True,
                            color='#F59E0B',
                            alpha=0.35,
                            label='High Risk Period',
                            zorder=3)
            
            # ==================== 繪製閾值線 ====================
            # 危險線
            ax.axhline(RISK_THRESHOLDS['wind_danger'], 
                    color="#DC2626", 
                    linestyle='-', 
                    linewidth=2.5, 
                    label=f'🔴 Danger Threshold ({RISK_THRESHOLDS["wind_danger"]} kts)', 
                    zorder=4,
                    alpha=0.8)
            
            # 警告線
            ax.axhline(RISK_THRESHOLDS['wind_warning'], 
                    color="#F59E0B", 
                    linestyle='--', 
                    linewidth=2.5, 
                    label=f'🟠 Warning Threshold ({RISK_THRESHOLDS["wind_warning"]} kts)', 
                    zorder=4,
                    alpha=0.8)
            
            # 注意線
            ax.axhline(RISK_THRESHOLDS['wind_caution'], 
                    color="#EAB308", 
                    linestyle=':', 
                    linewidth=2.2, 
                    label=f'🟡 Caution Threshold ({RISK_THRESHOLDS["wind_caution"]} kts)', 
                    zorder=4,
                    alpha=0.7)
            
            # ==================== 標註最大值 ====================
            max_wind_idx = df['wind_speed'].idxmax()
            max_gust_idx = df['wind_gust'].idxmax()
            
            # 標註最大風速
            ax.annotate(f'Max: {df.loc[max_wind_idx, "wind_speed"]:.1f} kts',
                    xy=(df.loc[max_wind_idx, 'time'], df.loc[max_wind_idx, 'wind_speed']),
                    xytext=(10, 15),
                    textcoords='offset points',
                    fontsize=11,
                    fontweight='bold',
                    color='#1E40AF',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='#EFF6FF', edgecolor='#3B82F6', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#1E40AF', lw=2))
            
            # 標註最大陣風
            ax.annotate(f'Max: {df.loc[max_gust_idx, "wind_gust"]:.1f} kts',
                    xy=(df.loc[max_gust_idx, 'time'], df.loc[max_gust_idx, 'wind_gust']),
                    xytext=(10, -20),
                    textcoords='offset points',
                    fontsize=11,
                    fontweight='bold',
                    color='#DC2626',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='#FEF2F2', edgecolor='#EF4444', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#DC2626', lw=2))
            
            # ==================== 標題與標籤 ====================
            # 主標題
            ax.set_title(f"🌪️ Wind Speed & Gust Forecast - {assessment.port_name} ({assessment.port_code})", 
                        fontsize=22, 
                        fontweight='bold', 
                        pad=20, 
                        color='#1F2937',
                        fontfamily='sans-serif')
            
            # 副標題
            fig.text(0.5, 0.94, '48-Hour Weather Monitoring | Data Source: WNI', 
                    ha='center', 
                    fontsize=12, 
                    color='#6B7280',
                    style='italic')
            
            # Y軸標籤
            ax.set_ylabel('Wind Speed (knots)', 
                        fontsize=15, 
                        fontweight='600', 
                        color='#374151',
                        labelpad=10)
            
            # X軸標籤
            ax.set_xlabel('Date / Time (UTC)', 
                        fontsize=15, 
                        fontweight='600', 
                        color='#374151',
                        labelpad=10)
            
            # ==================== 圖例設定 ====================
            legend = ax.legend(loc='upper left', 
                            frameon=True, 
                            fontsize=12, 
                            shadow=True, 
                            fancybox=True,
                            framealpha=0.95,
                            edgecolor='#D1D5DB',
                            facecolor='#FFFFFF',
                            ncol=2)
            legend.get_frame().set_linewidth(1.5)
            
            # ==================== 網格設定 ====================
            ax.grid(True, 
                alpha=0.3, 
                linestyle='--', 
                linewidth=0.8, 
                color='#9CA3AF',
                zorder=1)
            ax.set_axisbelow(True)
            
            # ==================== 座標軸格式 ====================
            # X軸日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax.xaxis.set_minor_locator(mdates.HourLocator(interval=3))
            
            # 旋轉X軸標籤
            plt.setp(ax.xaxis.get_majorticklabels(), 
                    rotation=0, 
                    ha='center', 
                    fontsize=11,
                    fontweight='500')
            
            # Y軸刻度
            plt.setp(ax.yaxis.get_majorticklabels(), 
                    fontsize=11,
                    fontweight='500')
            
            # ==================== 邊框美化 ====================
            for spine in ['top', 'right']:
                ax.spines[spine].set_visible(False)
            
            for spine in ['bottom', 'left']:
                ax.spines[spine].set_edgecolor('#9CA3AF')
                ax.spines[spine].set_linewidth(2)
            
            # ==================== 設定Y軸範圍 ====================
            y_max = max(df['wind_gust'].max(), RISK_THRESHOLDS['wind_danger']) * 1.15
            ax.set_ylim(0, y_max)
            
            # ==================== 加入水印 ====================
            fig.text(0.99, 0.01, 'WHL Marine Technology Division', 
                    ha='right', 
                    va='bottom',
                    fontsize=9, 
                    color='#9CA3AF',
                    alpha=0.6,
                    style='italic')
            
            plt.tight_layout(rect=[0, 0.02, 1, 0.96])
            
            # ==================== 儲存與轉換 ====================
            # 1. 存檔（高解析度）
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            fig.savefig(filepath, 
                    dpi=150, 
                    bbox_inches='tight', 
                    facecolor='white',
                    edgecolor='none',
                    pad_inches=0.1)
            print(f"      💾 圖片已存檔: {filepath}")
            
            # 2. 轉 Base64（高解析度）
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

            # 🎨 使用專業樣式
            plt.style.use('default')
            
            # 🔥 設定圖表尺寸和 DPI
            fig, ax = plt.subplots(figsize=(16, 7), dpi=120)
            
            # 設定背景顏色
            fig.patch.set_facecolor('#FFFFFF')
            ax.set_facecolor('#F0FDF4')
            
            # ==================== 繪製風險區域背景 ====================
            # 危險區域（紅色）
            ax.axhspan(RISK_THRESHOLDS['wave_danger'], ax.get_ylim()[1] if len(df) > 0 else 8, 
                    facecolor='#FEE2E2', alpha=0.3, zorder=0)
            # 警告區域（橙色）
            ax.axhspan(RISK_THRESHOLDS['wave_warning'], RISK_THRESHOLDS['wave_danger'], 
                    facecolor='#FEF3C7', alpha=0.3, zorder=0)
            # 注意區域（黃色）
            ax.axhspan(RISK_THRESHOLDS['wave_caution'], RISK_THRESHOLDS['wave_warning'], 
                    facecolor='#FEF9C3', alpha=0.3, zorder=0)
            
            # ==================== 繪製主要數據線 ====================
            # 浪高線（綠色系，粗實線）
            line = ax.plot(df['time'], df['wave_height'], 
                        color='#047857', 
                        linewidth=4, 
                        marker='o', 
                        markersize=7,
                        markerfacecolor='#10B981',
                        markeredgecolor='#047857',
                        markeredgewidth=1.5,
                        label='Significant Wave Height',
                        zorder=5,
                        alpha=0.9)
            
            # ==================== 填充區域 ====================
            # 浪高曲線下方填充（淡綠色）
            ax.fill_between(df['time'], df['wave_height'], 
                            alpha=0.25, 
                            color='#10B981', 
                            zorder=2)
            
            # 高風險時段特別標註（橙色填充）
            high_risk_mask = df['wave_height'] >= RISK_THRESHOLDS['wave_caution']
            if high_risk_mask.any():
                ax.fill_between(df['time'], 
                            df['wave_height'], 
                            where=high_risk_mask,
                            interpolate=True,
                            color='#F59E0B',
                            alpha=0.35,
                            label='High Risk Period',
                            zorder=3)
            
            # ==================== 繪製閾值線 ====================
            # 危險線
            ax.axhline(RISK_THRESHOLDS['wave_danger'], 
                    color="#DC2626", 
                    linestyle='-', 
                    linewidth=2.5, 
                    label=f'🔴 Danger Threshold ({RISK_THRESHOLDS["wave_danger"]} m)', 
                    zorder=4,
                    alpha=0.8)
            
            # 警告線
            ax.axhline(RISK_THRESHOLDS['wave_warning'], 
                    color="#F59E0B", 
                    linestyle='--', 
                    linewidth=2.5, 
                    label=f'🟠 Warning Threshold ({RISK_THRESHOLDS["wave_warning"]} m)', 
                    zorder=4,
                    alpha=0.8)
            
            # 注意線
            ax.axhline(RISK_THRESHOLDS['wave_caution'], 
                    color="#EAB308", 
                    linestyle=':', 
                    linewidth=2.2, 
                    label=f'🟡 Caution Threshold ({RISK_THRESHOLDS["wave_caution"]} m)', 
                    zorder=4,
                    alpha=0.7)
            
            # ==================== 標註最大值 ====================
            max_wave_idx = df['wave_height'].idxmax()
            
            # 標註最大浪高
            ax.annotate(f'Max: {df.loc[max_wave_idx, "wave_height"]:.2f} m',
                    xy=(df.loc[max_wave_idx, 'time'], df.loc[max_wave_idx, 'wave_height']),
                    xytext=(10, 15),
                    textcoords='offset points',
                    fontsize=11,
                    fontweight='bold',
                    color='#047857',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='#D1FAE5', edgecolor='#10B981', linewidth=2),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='#047857', lw=2))
            
            # ==================== 標題與標籤 ====================
            # 主標題
            ax.set_title(f"🌊 Wave Height Forecast - {assessment.port_name} ({assessment.port_code})", 
                        fontsize=22, 
                        fontweight='bold', 
                        pad=20, 
                        color='#1F2937',
                        fontfamily='sans-serif')
            
            # 副標題
            fig.text(0.5, 0.94, '48-Hour Weather Monitoring | Data Source: WNI', 
                    ha='center', 
                    fontsize=12, 
                    color='#6B7280',
                    style='italic')
            
            # Y軸標籤
            ax.set_ylabel('Wave Height (meters)', 
                        fontsize=15, 
                        fontweight='600', 
                        color='#374151',
                        labelpad=10)
            
            # X軸標籤
            ax.set_xlabel('Date / Time (UTC)', 
                        fontsize=15, 
                        fontweight='600', 
                        color='#374151',
                        labelpad=10)
            
            # ==================== 圖例設定 ====================
            legend = ax.legend(loc='upper left', 
                            frameon=True, 
                            fontsize=12, 
                            shadow=True, 
                            fancybox=True,
                            framealpha=0.95,
                            edgecolor='#D1D5DB',
                            facecolor='#FFFFFF',
                            ncol=2)
            legend.get_frame().set_linewidth(1.5)
            
            # ==================== 網格設定 ====================
            ax.grid(True, 
                alpha=0.3, 
                linestyle='--', 
                linewidth=0.8, 
                color='#9CA3AF',
                zorder=1)
            ax.set_axisbelow(True)
            
            # ==================== 座標軸格式 ====================
            # X軸日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax.xaxis.set_minor_locator(mdates.HourLocator(interval=3))
            
            # 旋轉X軸標籤
            plt.setp(ax.xaxis.get_majorticklabels(), 
                    rotation=0, 
                    ha='center', 
                    fontsize=11,
                    fontweight='500')
            
            # Y軸刻度
            plt.setp(ax.yaxis.get_majorticklabels(), 
                    fontsize=11,
                    fontweight='500')
            
            # ==================== 邊框美化 ====================
            for spine in ['top', 'right']:
                ax.spines[spine].set_visible(False)
            
            for spine in ['bottom', 'left']:
                ax.spines[spine].set_edgecolor('#9CA3AF')
                ax.spines[spine].set_linewidth(2)
            
            # ==================== 設定Y軸範圍 ====================
            y_max = max(df['wave_height'].max(), RISK_THRESHOLDS['wave_danger']) * 1.15
            ax.set_ylim(0, y_max)
            
            # ==================== 加入水印 ====================
            fig.text(0.99, 0.01, 'WHL Marine Technology Division', 
                    ha='right', 
                    va='bottom',
                    fontsize=9, 
                    color='#9CA3AF',
                    alpha=0.6,
                    style='italic')
            
            plt.tight_layout(rect=[0, 0.02, 1, 0.96])
            
            # ==================== 儲存與轉換 ====================
            # 1. 存檔（高解析度）
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            fig.savefig(filepath, 
                    dpi=150, 
                    bbox_inches='tight', 
                    facecolor='white',
                    edgecolor='none',
                    pad_inches=0.1)
            print(f"      💾 圖片已存檔: {filepath}")
            
            # 2. 轉 Base64（高解析度）
            base64_str = self._fig_to_base64(fig, dpi=150)
            print(f"      ✅ Base64 轉換成功 (長度: {len(base64_str)} 字元)")
            
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"      ❌ 繪製浪高圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None


# ================= 風險分析模組 (修正版) =================

class WeatherRiskAnalyzer:
    """氣象風險分析器"""
    
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
    def analyze_record(cls, record: WeatherRecord) -> Dict:
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
        try:
            parser = WeatherParser()
            port_name, records, warnings = parser.parse_content(content)
            
            if not records:
                return None
            
            risk_periods = []
            max_level = 0
            
            # 找出風速最大的那一筆記錄
            max_wind_record = max(records, key=lambda r: r.wind_speed_kts)
            # 找出陣風最大的那一筆記錄
            max_gust_record = max(records, key=lambda r: r.wind_gust_kts)
            # 浪高最大的記錄
            max_wave_record = max(records, key=lambda r: r.wave_height)
            
            for record in records:
                analyzed = cls.analyze_record(record)
                if analyzed['risks']:
                    risk_periods.append({
                        'time': record.time.strftime('%Y-%m-%d %H:%M'),
                        'wind_speed_kts': record.wind_speed_kts,
                        'wind_speed_bft': record.wind_speed_bft,
                        'wind_gust_kts': record.wind_gust_kts,
                        'wind_gust_bft': record.wind_gust_bft,
                        'wave_height': record.wave_height,
                        'risks': analyzed['risks'],
                        'risk_level': analyzed['risk_level']
                    })
                    max_level = max(max_level, analyzed['risk_level'])
            
            if max_level == 0:
                return None
            
            risk_factors = []
            if max_wind_record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
                risk_factors.append(f"風速 {max_wind_record.wind_speed_kts:.1f} kts")
            if max_gust_record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
                risk_factors.append(f"陣風 {max_gust_record.wind_gust_kts:.1f} kts")
            if max_wave_record.wave_height >= RISK_THRESHOLDS['wave_caution']:
                risk_factors.append(f"浪高 {max_wave_record.wave_height:.1f} m")
            
            # ✅ 計算 LCT 時區偏移（用於顯示）
            lct_offset_hours = int(max_wind_record.lct_time.utcoffset().total_seconds() / 3600)
            lct_offset_str = f"UTC{lct_offset_hours:+d}"
            
            return RiskAssessment(
                port_code=port_code,
                port_name=port_info.get('port_name', port_name),
                country=port_info.get('country', 'N/A'),
                risk_level=max_level,
                risk_factors=risk_factors,
                max_wind_kts=max_wind_record.wind_speed_kts,
                max_wind_bft=max_wind_record.wind_speed_bft,
                max_gust_kts=max_wind_record.wind_gust_kts,
                max_gust_bft=max_wind_record.wind_gust_bft,
                max_wave=max_wave_record.wave_height,
                
                # ✅ 格式：MM/DD 08:00 (UTC)
                max_wind_time_utc=f"{max_wind_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_gust_time_utc=f"{max_gust_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_wave_time_utc=f"{max_wave_record.time.strftime('%m/%d %H:%M')} (UTC)",
                
                # ✅ 格式：08:00 (LT) 或 08:00 (UTC+8)
                max_wind_time_lct=f"{max_wind_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                max_gust_time_lct=f"{max_gust_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                max_wave_time_lct=f"{max_wave_record.lct_time.strftime('%Y-%m-%d %H:%M')} (LT)",
                
                risk_periods=risk_periods,
                issued_time=issued_time,
                latitude=port_info.get('latitude', 0.0),
                longitude=port_info.get('longitude', 0.0),
                raw_records=records
            )
            
        except Exception as e:
            print(f"❌ 分析港口 {port_code} 時發生錯誤: {e}")
            traceback.print_exc()
            return None


# ================= Teams 通知器 (無變動) =================

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
        
        # 風險分組
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
        
        # 只顯示前 5 個最高風險港口
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


# ================= Gmail 通知器 (無變動) =================

class GmailRelayNotifier:
    """Gmail 接力發信器"""
    
    def __init__(self):
        self.user = MAIL_USER
        self.password = MAIL_PASSWORD
        self.target = TARGET_EMAIL
        self.subject_trigger = TRIGGER_SUBJECT

    def send_trigger_email(self, report_data: dict, report_html: str, 
                       images: Dict[str, str] = None) -> bool:
        """發送觸發信件"""
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密 (MAIL_USER / MAIL_PASSWORD)")
            return False
    
        # ✅ 新增:檢查密碼格式
        print(f"🔍 Gmail 設定檢查:")
        print(f"   帳號: {self.user}")
        print(f"   密碼長度: {len(self.password)}")
        print(f"   密碼格式: {'✅ 正確 (16字元)' if len(self.password) == 16 else '❌ 錯誤'}")
        print(f"   密碼包含空格: {'❌ 是' if ' ' in self.password else '✅ 否'}")
    
        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_trigger
        
        json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
        msg.attach(MIMEText(json_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(report_html, 'html', 'utf-8'))
    
        try:
            print(f"📧 正在透過 Gmail 發送報表給 {self.target}...")
            
            # ✅ 新增:更詳細的連線過程
            print("   🔌 正在連線到 smtp.gmail.com:587...")
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            print("   ✅ 連線成功")
            
            print("   🤝 正在發送 EHLO...")
            server.ehlo()
            print("   ✅ EHLO 成功")
            
            print("   🔒 正在啟動 TLS 加密...")
            server.starttls()
            print("   ✅ TLS 啟動成功")
            
            print("   🤝 正在重新發送 EHLO...")
            server.ehlo()
            print("   ✅ EHLO 成功")
            
            print(f"   🔑 正在登入 {self.user}...")
            server.login(self.user, self.password)
            print("   ✅ 登入成功")
            
            print("   📨 正在傳送郵件...")
            server.sendmail(self.user, self.target, msg.as_string())
            print("   ✅ 郵件傳送成功")
            
            server.quit()
            print(f"✅ Email 發送成功!")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            print(f"❌ Gmail 認證失敗: {e}")
            print(f"   錯誤代碼: {e.smtp_code}")
            print(f"   錯誤訊息: {e.smtp_error}")
            print("\n   可能原因:")
            print("   1. 應用程式密碼錯誤或已過期")
            print("   2. 帳號被 Google 標記為可疑")
            print("   3. 帳號曾被盜用,目前受限")
            print("\n   解決方法:")
            print("   → 前往: https://accounts.google.com/DisplayUnlockCaptcha")
            print("   → 完成驗證後重新產生應用程式密碼")
            return False
        
        except smtplib.SMTPException as e:
            print(f"❌ SMTP 錯誤: {e}")
            return False
            
        except Exception as e:
            print(f"❌ Gmail 發送失敗: {e}")
            print(f"   錯誤類型: {type(e).__name__}")
            traceback.print_exc()
            return False


# ================= 主服務類別 (修改版) =================

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
        print(f"\n📈 步驟 3: 生成氣象趨勢圖 (針對 {len([r for r in risk_assessments if r.risk_level >= 2])} 個高風險港口)...")
        # 修改：不再回傳 dict，而是直接更新 assessment 物件內部
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
        
        # 6. 發送 Email
        print("\n📧 步驟 6: 發送 Email 通知...")
        report_html = self._generate_html_report(risk_assessments)
        
        email_sent = False
        try:
            email_sent = self.email_notifier.send_trigger_email(
                report_data, report_html, None
            )
        except Exception as e:
            print(f"⚠️ 發信過程發生異常: {e}")
            traceback.print_exc()
        
        report_data['email_sent'] = email_sent
        report_data['teams_sent'] = teams_sent
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print(f"   - 風險港口: {len(risk_assessments)}")
        print(f"   - Teams 通知: {'✅' if teams_sent else '❌'}")
        print(f"   - Email 發送: {'✅' if email_sent else '❌'}")
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
        
        # 依風險等級排序
        assessments.sort(key=lambda x: x.risk_level, reverse=True)
        return assessments
    
    def _generate_charts(self, assessments: List[RiskAssessment]):
        """生成圖表並將 Base64 存入 assessment"""
        
        if not assessments:
            print("   ⚠️ 沒有風險港口需要生成圖表")
            return
        
        chart_targets = assessments[:20]  # 最多生成 20 個港口的圖表（避免郵件過大）
        
        print(f"   📊 準備為 {len(chart_targets)} 個港口生成圖表...")
        
        success_count = 0
        for i, assessment in enumerate(chart_targets, 1):
            print(f"   [{i}/{len(chart_targets)}] 正在處理 {assessment.port_code}...")
            
            # 風速圖
            b64_wind = self.chart_generator.generate_wind_chart(
                assessment, assessment.port_code
            )
            if b64_wind:
                assessment.chart_base64_list.append(b64_wind)
                success_count += 1
                print(f"      ✅ 風速圖已生成 (Base64 長度: {len(b64_wind)} 字元)")
            else:
                print(f"      ❌ 風速圖生成失敗")
            
            # 浪高圖 (只在有高浪風險時生成)
            if assessment.max_wave >= RISK_THRESHOLDS['wave_caution']:
                b64_wave = self.chart_generator.generate_wave_chart(
                    assessment, assessment.port_code
                )
                if b64_wave:
                    assessment.chart_base64_list.append(b64_wave)
                    print(f"      ✅ 浪高圖已生成 (Base64 長度: {len(b64_wave)} 字元)")
                else:
                    print(f"      ⚠️ 浪高圖生成失敗")
        
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
        
    def _generate_html_report(self, assessments: List[RiskAssessment]) -> str:
        """生成 HTML 格式的精美報告 (通知屬輪版本)"""
        
        # ==================== 輔助函數定義區 ====================
        def format_time_display(time_str):
            """格式化時間顯示：移除時區標記但保留完整日期時間"""
            if not time_str:
                return "N/A"
            try:
                # 移除 (UTC) 或 (LT) 標記
                if '(' in time_str:
                    return time_str.split('(')[0].strip()
                return time_str
            except:
                return time_str
        
        # ==================== 初始化設定 ====================
        # 定義字型 - 更改為更現代的字體組合
        font_style = "font-family: 'Noto Sans TC', 'Microsoft JhengHei UI', 'Microsoft YaHei UI', 'Segoe UI', Arial, sans-serif;"
        
        # ✅ 時間計算（使用正確的時區處理）
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        now_str_UTC = f"{utc_now.strftime('%Y-%m-%d %H:%M')} (UTC)"

        # ==================== 無風險情況 ====================
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
            
        # ==================== 風險分組 ====================
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        # ==================== 風險港口樣式定義 ====================
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
                'criteria': '風速 Wind > 28 kts / 陣風 Gust > 34 kts / 浪高 Wave > 3.5 m'
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

        # ==================== HTML 開始 ====================
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
                """
        
        # ==================== 1. 時間戳記 ====================
        html += f"""  
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
                    
                   <!-- ==================== 2. 港口清單總表標題 ==================== -->
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
                    
                    <!-- ==================== 港口清單內容 ==================== -->
                    <tr>
                        <td style="padding: 0 25px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 3px solid #1E3A8A; border-top: none;">
                """
        
        # ==================== 風險港口列表 ====================
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
        
        # ==================== 快速統計列 ====================
        html += f"""
                            </table>
                        </td>
                    </tr>
                    
                    <!-- ==================== 3. 資料來源說明 ==================== -->
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
                      <!-- ==================== 4. 應對措施 ==================== -->
                    <tr>
    <td style="padding: 0 25px 25px 25px;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#FFFBEB">
            <tr>
                <td style="padding: 22px 25px; border-left: 5px solid #F59E0B; border-radius: 4px;">
                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                        <!-- 標題 -->
                        <tr>
                            <td style="padding-bottom: 18px; border-bottom: 2px solid #FCD34D;">
                                <strong style="font-size: 16px; color: #78350F;">📋 船隊風險應對措施 Fleet Risk Response Actions</strong>
                            </td>
                        </tr>
                        
                        <!-- 措施 1: 增加與代理核實氣象 -->
                        <tr>
                            <td style="padding-top: 15px; padding-bottom: 12px;">
                                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                    <tr>
                                        <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                        <td>
                                            <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">請立即確認貴輪靠泊港口是否在風險名單中。除參照氣象預報外，亦務必與當地代理核實港口現場天候，以綜合評估潛在影響。</strong>
                                            <br>
                                            <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Immediately verify if your vessel's port of call is on the alert list. In addition to weather forecasts, cross-check local weather conditions with the local agent to assess potential impacts.</span>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- 措施 2: 修正漂航英文術語 -->
                        <tr>
                            <td style="padding-bottom: 12px;">
                                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                    <tr>
                                        <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                        <td>
                                            <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">根據風險等級制定應對策略，如：改至安全水域備車漂航以替代拋錨、提前申請額外拖船協助、加強繫泊纜繩、或調整靠離泊計畫等。</strong>
                                            <br>
                                            <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Formulate response strategies based on risk levels, such as drifting in safe waters with engines on standby instead of anchoring, arranging extra tug assistance in advance, reinforcing mooring arrangements, or adjusting berthing/unberthing schedules.</span>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- 措施 3: 優化溝通決策用語 -->
                        <tr>
                            <td>
                                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                    <tr>
                                        <td width="20" valign="top" style="font-size: 14px;">✅</td>
                                        <td>
                                            <strong style="font-size: 14px; color: #451A03; line-height: 1.5;">與船管PIC、當地代理保持密切聯繫，及時報告船舶狀態和決策。</strong>
                                            <br>
                                            <span style="font-size: 13px; color: #92400E; line-height: 1.4;">Maintain close contact with the PIC and local agents; promptly report vessel status and operational decisions.</span>
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

                    <!-- ==================== 6. 分隔線與提示 ==================== -->
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

        # ==================== 6. 詳細港口資料區 ====================
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
                        'desc': '條件 Criteria: 風速 Wind > 28 kts / 陣風 Gust > 34 kts / 浪高 Wave > 3.5 m'
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

        # 遍歷每個風險等級
        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports:
                continue
            
            style = styles_detail[level]
            
            # 該等級的標題區塊
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
            
            # 遍歷該等級的每個港口
            for index, p in enumerate(ports):
                # 1. 樣式與背景邏輯
                row_bg = "#FFFFFF" if index % 2 == 0 else "#FAFBFC"
                
                # 2. 數值強調樣式 (閾值判斷)
                wind_style = "color: #DC2626; font-weight: bold;" if p.max_wind_kts >= 28 else "color: #333;"
                gust_style = "color: #DC2626; font-weight: bold;" if p.max_gust_kts >= 34 else "color: #333;"
                wave_style = "color: #DC2626; font-weight: bold;" if p.max_wave >= 3.5 else "color: #333;"
                
                # 3. 風險等級 (顏色、文字、圖示)
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

                # 4. 風速等級 (文字、顏色)
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

                # 5. 陣風等級 (文字、顏色)
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

                # 6. 浪高等級 (文字、顏色)
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

                # 7. 風險持續時間
                if p.risk_periods:
                    try:
                        first_risk = datetime.strptime(p.risk_periods[0]['time'], '%Y-%m-%d %H:%M')
                        last_risk = datetime.strptime(p.risk_periods[-1]['time'], '%Y-%m-%d %H:%M')
                        duration_hours = int((last_risk - first_risk).total_seconds() / 3600) + 3
                        
                        # 限制最大 48 小時
                        risk_duration = str(min(duration_hours, 48))
                        
                        # 如果超過 48 小時，記錄警告
                        if duration_hours > 48:
                            print(f"   ⚠️ {p.port_code} 風險持續時間異常: {duration_hours} 小時 (已限制為 48)")
                    except Exception as e:
                        print(f"   ❌ {p.port_code} 計算持續時間失敗: {e}")
                        risk_duration = str(len(p.risk_periods) * 3)
                else:
                    risk_duration = "0"

                # 8. 時間格式化
                w_utc = format_time_display(p.max_wind_time_utc)
                w_lct = format_time_display(p.max_wind_time_lct)
                g_utc = format_time_display(p.max_gust_time_utc)
                g_lct = format_time_display(p.max_gust_time_lct)
                v_utc = format_time_display(p.max_wave_time_utc)
                v_lct = format_time_display(p.max_wave_time_lct)
                
                # 主要資料列
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
                            </td>

                            <td valign="top" style="padding: 15px; width: 45%;">
                                <div style="margin-bottom: 12px;">
                                    <span style="background-color: #FEF2F2; color: #B91C1C; border: 1px solid #FCA5A5; font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px; display: inline-block; line-height: 1.4;">
                                        ⚠️ 風險因素 Risk Factors: {', '.join(p.risk_factors[:2])}
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
                
                # 圖表列處理
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
                                        📈 風速趨勢圖表 Wind Trend Chart:
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

        # ==================== 7. 頁尾 ====================
        html += f"""
                     <!-- ==================== Footer 頁尾區塊 ==================== -->
                        <tr>
                            <td bgcolor="#F8F9FA" align="center" style="padding: 40px 25px; border-top: 3px solid #D1D5DB;">
                                <table border="0" cellpadding="0" cellspacing="0" width="600">
                                    <!-- 公司名稱 -->
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
                                    
                                    <!-- 分隔線 -->
                                    <tr>
                                        <td align="center" style="padding-bottom: 20px;">
                                            <table border="0" cellpadding="0" cellspacing="0" width="120">
                                                <tr>
                                                    <td style="border-top: 2px solid #9CA3AF;"></td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                    
                                    <!-- 部門名稱 -->
                                    <tr>
                                        <td align="center" style="padding-bottom: 25px;">
                                            <font size="2" color="#374151" face="Arial, Noto Sans TC, Microsoft JhengHei UI, sans-serif">
                                                <strong>Marine Technology Division | Fleet Risk Management Dept.</strong>
                                            </font>
                                        </td>
                                    </tr>
                                    
                                    <!-- 免責聲明 -->
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
                                    
                                    <!-- 版權聲明 -->
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




