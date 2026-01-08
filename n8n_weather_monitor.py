# n8n_weather_monitor.py
"""
N8N 自動化氣象監控腳本 (含圖表生成功能) - Base64 嵌入版
用途：每天自動抓取港口天氣，分析高風險港口，生成趨勢圖，並發送到 Teams 與 Email
修改重點：圖片改為 Base64 編碼直接嵌入 HTML，解決 Power Automate 轉寄掉圖問題。
"""

import os
import sys
import json
import traceback
import sqlite3
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
# from email.mime.image import MIMEImage # 已不需要

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
DB_FILE_PATH = os.getenv('DB_FILE_PATH', 'WNI_port_weather.db')
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
    # chart_cids 已移除，改用 Base64 列表
    chart_base64_list: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 移除不適合轉 JSON 的欄位
        for key in ['raw_records', 'chart_base64_list']:
            d.pop(key, None)
        return d


# ================= 繪圖模組 (修改版) =================

class ChartGenerator:
    """圖表生成器 - 支援 Base64 輸出"""
    
    def __init__(self, output_dir: str = CHART_OUTPUT_DIR):
        self.output_dir = output_dir
        
        # 清空舊圖表 (仍保留存檔功能以便除錯)
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

    def _fig_to_base64(self, fig) -> str:
        """將 Matplotlib Figure 轉為 Base64 字串"""
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=120)
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        return img_str

    def generate_wind_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製風速趨勢圖，回傳 Base64 字串"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            plt.style.use('seaborn-v0_8-darkgrid')
            fig, ax = plt.subplots(figsize=(12, 5.5))
            
            # 繪製曲線 - 加粗並使用更鮮明的顏色
            ax.plot(df['time'], df['wind_speed'], color='#2563EB', 
                   label='Wind Speed (kts)', linewidth=2.5, marker='o', markersize=4, zorder=3)
            ax.plot(df['time'], df['wind_gust'], color='#DC2626', 
                   linestyle='--', label='Gust (kts)', linewidth=2, marker='s', markersize=3.5, zorder=3)
            
            # 填充
            ax.fill_between(df['time'], df['wind_speed'], alpha=0.15, color='#2563EB', zorder=1)
            ax.fill_between(
                df['time'], 
                df['wind_speed'], 
                y2=0,
                where=(df['wind_speed'] >= RISK_THRESHOLDS['wind_caution']),
                interpolate=True,
                color='#F59E0B',
                alpha=0.25,
                label='High Risk Period',
                zorder=2
            )                    
            
            # 閾值線 - 調整顏色和粗細
            ax.axhline(RISK_THRESHOLDS['wind_danger'], color="#DC2626", 
                      linestyle=':', linewidth=2, label=f'Danger ({RISK_THRESHOLDS["wind_danger"]} kts)', zorder=2)   
            ax.axhline(RISK_THRESHOLDS['wind_warning'], color="#F59E0B", 
                      linestyle='--', linewidth=2, label=f'Warning ({RISK_THRESHOLDS["wind_warning"]} kts)', zorder=2)        
            ax.axhline(RISK_THRESHOLDS['wind_caution'], color="#FCD34D", 
                      linestyle=':', linewidth=1.8, label=f'Caution ({RISK_THRESHOLDS["wind_caution"]} kts)', zorder=2)
            
            # 標題與標籤 - 加大字體
            ax.set_title(f"{assessment.port_name} ({assessment.port_code}) - Wind Speed & Gust Trend (48 Hrs)", 
                        fontsize=16, fontweight='bold', pad=20, color='#1F2937')
            ax.set_ylabel('Speed (knots)', fontsize=13, fontweight='600', color='#374151')
            ax.set_xlabel('Date / Time (UTC)', fontsize=13, fontweight='600', color='#374151')
            ax.legend(loc='upper left', frameon=True, fontsize=10, shadow=True, fancybox=True)
            ax.grid(True, alpha=0.4, linestyle='--', linewidth=0.8)
            
            # 設定背景顏色
            ax.set_facecolor('#F9FAFB')
            fig.patch.set_facecolor('white')
            
            # 日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=30, ha='right', fontsize=10)
            plt.yticks(fontsize=10)
            
            # 加入邊框
            for spine in ax.spines.values():
                spine.set_edgecolor('#D1D5DB')
                spine.set_linewidth(1.5)
            
            plt.tight_layout()
            
            # 1. 存檔 (保留做為紀錄)
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            plt.savefig(filepath, dpi=120, bbox_inches='tight', facecolor='white')
            
            # 2. 轉 Base64 (用於 Email)
            base64_str = self._fig_to_base64(fig)
            
            plt.close(fig)
            print(f"   ✅ 風速圖已生成: {filepath}")
            return base64_str
            
        except Exception as e:
            print(f"   ❌ 繪製風速圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None

    def generate_wave_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製浪高趨勢圖，回傳 Base64 字串"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            if df['wave_height'].max() < 1.0:
                return None

            plt.style.use('seaborn-v0_8-darkgrid')
            fig, ax = plt.subplots(figsize=(12, 5.5))
            
            # 繪製曲線
            ax.plot(df['time'], df['wave_height'], color='#059669', 
                   label='Sig. Wave Height (m)', linewidth=2.5, marker='o', markersize=4, zorder=3)
            ax.fill_between(df['time'], df['wave_height'], alpha=0.15, color='#059669', zorder=1)
            ax.fill_between(
                df['time'], 
                df['wave_height'], 
                y2=0,
                where=(df['wave_height'] > RISK_THRESHOLDS['wave_caution']),
                interpolate=True,
                color='#F59E0B',
                alpha=0.25,
                label='Risk Area',
                zorder=2
            )          
            
            # 閾值線
            ax.axhline(RISK_THRESHOLDS['wave_caution'], color="#FCD34D", 
                      linestyle=':', linewidth=1.8, label=f'Caution ({RISK_THRESHOLDS["wave_caution"]} m)', zorder=2)
            ax.axhline(RISK_THRESHOLDS['wave_warning'], color="#F59E0B", 
                      linestyle='--', linewidth=2, label=f'Warning ({RISK_THRESHOLDS["wave_warning"]} m)', zorder=2)
            ax.axhline(RISK_THRESHOLDS['wave_danger'], color="#DC2626", 
                      linestyle=':', linewidth=2, label=f'Danger ({RISK_THRESHOLDS["wave_danger"]} m)', zorder=2)    
            
            ax.set_title(f"{assessment.port_name} ({assessment.port_code}) - Wave Height Trend (48 Hrs)", 
                        fontsize=16, fontweight='bold', pad=20, color='#1F2937')
            ax.set_ylabel('Height (m)', fontsize=13, fontweight='600', color='#374151')
            ax.set_xlabel('Date / Time (UTC)', fontsize=13, fontweight='600', color='#374151')
            ax.legend(loc='upper left', frameon=True, fontsize=10, shadow=True, fancybox=True)
            ax.grid(True, alpha=0.4, linestyle='--', linewidth=0.8)
            
            # 設定背景顏色
            ax.set_facecolor('#F9FAFB')
            fig.patch.set_facecolor('white')
            
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=30, ha='right', fontsize=10)
            plt.yticks(fontsize=10)
            
            # 加入邊框
            for spine in ax.spines.values():
                spine.set_edgecolor('#D1D5DB')
                spine.set_linewidth(1.5)
            
            plt.tight_layout()
            
            # 1. 存檔
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            plt.savefig(filepath, dpi=120, bbox_inches='tight', facecolor='white')
            
            # 2. 轉 Base64
            base64_str = self._fig_to_base64(fig)
            
            plt.close(fig)
            print(f"   ✅ 浪高圖已生成: {filepath}")
            return base64_str
            
        except Exception as e:
            print(f"   ❌ 繪製浪高圖失敗 {port_code}: {e}")
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
            
            # 🔧 修正：找出風速最大的那一筆記錄（該筆記錄同時包含當時的陣風值）
            max_wind_record = max(records, key=lambda r: r.wind_speed_kts)
            # 🔧 修正：找出陣風最大的那一筆記錄（該筆記錄同時包含當時的風速值）
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
            
            return RiskAssessment(
                port_code=port_code,
                port_name=port_info.get('port_name', port_name),
                country=port_info.get('country', 'N/A'),
                risk_level=max_level,
                risk_factors=risk_factors,
                # 🔧 修正：使用同一筆記錄的風速和陣風
                max_wind_kts=max_wind_record.wind_speed_kts,
                max_wind_bft=max_wind_record.wind_speed_bft,
                max_gust_kts=max_wind_record.wind_gust_kts,  # 使用同一時間的陣風
                max_gust_bft=max_wind_record.wind_gust_bft,  # 使用同一時間的陣風
                max_wave=max_wave_record.wave_height,
                # 時間都使用風速最大的那一筆
                max_wind_time_utc=max_wind_record.time.strftime('%Y-%m-%d %H:%M'),
                max_wind_time_lct=max_wind_record.lct_time.strftime('%Y-%m-%d %H:%M'),
                max_gust_time_utc=max_gust_record.time.strftime('%Y-%m-%d %H:%M'),
                max_gust_time_lct=max_gust_record.lct_time.strftime('%Y-%m-%d %H:%M'),
                max_wave_time_utc=max_wave_record.time.strftime('%Y-%m-%d %H:%M'),
                max_wave_time_lct=max_wave_record.lct_time.strftime('%Y-%m-%d %H:%M'),
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
                    {"title": "🔴 危險 (Danger)", "value": str(len(danger_ports))},
                    {"title": "🟠 警告 (Warning)", "value": str(len(warning_ports))},
                    {"title": "🟡 注意 (Caution)", "value": str(len(caution_ports))},
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
        """
        發送觸發信件
        注意：現在圖片已經內嵌在 report_html 的 Base64 中，不需要再用 attachments 處理。
        """
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密 (MAIL_USER / MAIL_PASSWORD)")
            return False

        # 改用 MIMEMultipart('alternative') 因為不需要 related (附件) 了
        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_trigger
        
        # 1. 純文字 (JSON)
        json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
        msg.attach(MIMEText(json_text, 'plain', 'utf-8'))
        
        # 2. HTML (內含 Base64 圖片)
        msg.attach(MIMEText(report_html, 'html', 'utf-8'))

        try:
            print(f"📧 正在透過 Gmail 發送報表給 {self.target}...")
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
            print("   🔑 正在登入...")
            server.login(self.user, self.password)
            
            print("   📨 正在傳送...")
            server.sendmail(self.user, self.target, msg.as_string())
            server.quit()
            
            print(f"✅ Email 發送成功！")
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
        
        # 優先處理高風險港口
        chart_targets = [r for r in assessments if r.risk_level >= 2]
        
        # 如果高風險港口少，補充部分 Caution 港口
        if len(chart_targets) < 5:
            cautions = [r for r in assessments if r.risk_level == 1]
            chart_targets.extend(cautions[:(10 - len(chart_targets))])
        
        for assessment in chart_targets:
            # 風速圖
            b64_wind = self.chart_generator.generate_wind_chart(
                assessment, assessment.port_code
            )
            if b64_wind:
                assessment.chart_base64_list.append(b64_wind)
            
            # 浪高圖 (只在有高浪風險時生成)
            if assessment.max_wave >= RISK_THRESHOLDS['wave_caution']:
                b64_wave = self.chart_generator.generate_wave_chart(
                    assessment, assessment.port_code
                )
                if b64_wave:
                    assessment.chart_base64_list.append(b64_wave)
    
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
        """生成 HTML 格式的精美報告 (美化版)"""
        
        # 使用更現代的字體堆疊
        font_style = "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft JhengHei', '微軟正黑體', 'Noto Sans TC', Arial, sans-serif;"
        
        # 時間計算
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')
        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')

        # 若無風險的顯示
        if not assessments:
            return f"""
            <html>
            <body style="margin: 0; padding: 20px; background-color: #f0f4f8; {font_style}">
                <div style="max-width: 800px; margin: 0 auto; background: linear-gradient(135deg, #E8F5E9 0%, #C8E6C9 100%); padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                    <h2 style="color: #2E7D32; margin: 0 0 15px 0; font-size: 28px; font-weight: 700;">
                        🟢 System Status: All Safe
                    </h2>
                    <p style="color: #1B5E20; font-size: 16px; line-height: 1.6; margin: 0;">
                        未來 48 小時內所有靠泊港口均處於安全範圍<br>
                        <span style="font-size: 14px; opacity: 0.8;">All ports are within safe limits for the next 48 hours.</span>
                    </p>
                    <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(46, 125, 50, 0.2); font-size: 13px; color: #558B2F;">
                        📅 Updated: {now_str_LT} (TPE) | {now_str_UTC} (UTC)
                    </div>
                </div>
            </body>
            </html>
            """
            
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        # Email Header (美化版)
        html = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin: 0; padding: 0; background-color: #F0F4F8; {font_style}">
            <div style="max-width: 900px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.12);">
        
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #004B97 0%, #0066CC 100%); color: white; padding: 32px 40px;">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div>
                        <h1 style="margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px;">
                            ⛴️ WHL Port Weather Risk Monitor
                        </h1>
                        <div style="margin-top: 8px; font-size: 14px; color: #B3D9FF; font-weight: 500;">
                            48-Hour Weather Forecast & Risk Assessment
                        </div>
                    </div>
                </div>
                <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 13px; color: #D6EBFF; font-weight: 500;">
                    📅 UPDATED: <strong>{now_str_LT}</strong> (TPE) <span style="opacity: 0.6;">|</span> <strong>{now_str_UTC}</strong> (UTC)
                </div>
            </div>

            <!-- Content -->
            <div style="padding: 40px;">
            
                <!-- Summary Alert -->
                <div style="background: linear-gradient(135deg, #FFF5F5 0%, #FEE2E2 100%); border-left: 6px solid #DC2626; padding: 24px; border-radius: 8px; margin-bottom: 30px; box-shadow: 0 2px 8px rgba(220, 38, 38, 0.1);">
                    <h2 style="margin: 0 0 12px 0; font-size: 18px; color: #991B1B; font-weight: 700; display: flex; align-items: center;">
                        <span style="font-size: 24px; margin-right: 10px;">⚠️</span>
                        未來 48 小時風險港口監控摘要
                    </h2>
                    <div style="font-size: 16px; color: #450A0A; line-height: 1.8;">
                        目前共有 <span style="display: inline-block; background-color: #DC2626; color: white; padding: 4px 16px; border-radius: 20px; font-size: 22px; font-weight: 800; vertical-align: middle; margin: 0 8px; box-shadow: 0 2px 8px rgba(220, 38, 38, 0.3);">{len(assessments)}</span> 個港口具有潛在氣象風險
                    </div>
                </div>

                <!-- Warning Notice -->
                <div style="background: linear-gradient(to right, #FEF3C7, #FDE68A); padding: 20px 24px; border-radius: 8px; border: 2px solid #F59E0B; margin-bottom: 30px;">
                    <div style="display: flex; align-items: flex-start;">
                        <span style="font-size: 28px; margin-right: 12px;">⚠️</span>
                        <div style="font-size: 15px; color: #78350F; line-height: 1.7;">
                            <strong style="font-size: 16px;">請船管 PIC 留意下列港口動態</strong><br>
                            並通知業管屬輪做好相關<span style="background-color: #DC2626; color: white; padding: 4px 12px; border-radius: 4px; font-weight: 700; font-size: 13px; margin: 0 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">風險評估措施</span>
                        </div>
                    </div>
                </div>
        """

        # 風險等級樣式定義 (美化版)
        styles = {
            3: {
                'color': '#991B1B', 
                'bg': 'linear-gradient(135deg, #FEF2F2 0%, #FEE2E2 100%)', 
                'title': '🔴 POTENTIAL DANGER PORTS', 
                'subtitle': '條件: 風速 ≥ 8級 (34 kts) / 陣風 ≥ 9級 (41 kts) / 浪高 ≥ 4.0 m',
                'border': '#DC2626', 
                'header_bg': 'linear-gradient(to right, #FEE2E2, #FECACA)',
                'badge_bg': '#DC2626'
            },
            2: {
                'color': '#92400E', 
                'bg': 'linear-gradient(135deg, #FFFBEB 0%, #FEF3C7 100%)', 
                'title': '🟠 POTENTIAL WARNING PORTS',
                'subtitle': '條件: 風速 ≥ 7級 (28 kts) / 陣風 ≥ 8級 (34 kts) / 浪高 ≥ 3.5 m',
                'border': '#F59E0B', 
                'header_bg': 'linear-gradient(to right, #FEF3C7, #FDE68A)',
                'badge_bg': '#F59E0B'
            },
            1: {
                'color': '#075985', 
                'bg': 'linear-gradient(135deg, #F0F9FF 0%, #E0F2FE 100%)', 
                'title': '🟡 POTENTIAL CAUTION PORTS',
                'subtitle': '條件: 風速 ≥ 6級 (22 kts) / 陣風 ≥ 7級 (28 kts) / 浪高 ≥ 2.5 m',
                'border': '#0EA5E9', 
                'header_bg': 'linear-gradient(to right, #E0F2FE, #BAE6FD)',
                'badge_bg': '#0EA5E9'
            }
        }

        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports:
                continue
            
            style = styles[level]
            
            # 該等級的標題 (美化版)
            html += f"""
            <div style="margin: 40px 0 20px 0;">
                <div style="background: {style['header_bg']}; padding: 16px 24px; border-radius: 8px; border-left: 6px solid {style['border']}; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                    <div style="font-weight: 800; font-size: 17px; color: {style['color']}; margin-bottom: 4px; {font_style}">
                        {style['title']}
                    </div>
                    <div style="font-size: 13px; color: {style['color']}; opacity: 0.8; {font_style}">
                        {style['subtitle']}
                    </div>
                </div>
            </div>
            
            <table style="width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; border: 2px solid {style['border']}; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08);">
                <thead>
                    <tr style="background: {style['header_bg']}; color: {style['color']}; text-align: left;">
                        <th style="padding: 16px 20px; border-bottom: 2px solid {style['border']}; width: 25%; font-weight: 700; {font_style}">港口資訊<br><span style="font-size: 12px; font-weight: 500; opacity: 0.8;">Port Information</span></th>
                        <th style="padding: 16px 20px; border-bottom: 2px solid {style['border']}; width: 30%; font-weight: 700; {font_style}">氣象數據<br><span style="font-size: 12px; font-weight: 500; opacity: 0.8;">Weather Data</span></th>
                        <th style="padding: 16px 20px; border-bottom: 2px solid {style['border']}; font-weight: 700; {font_style}">高風險時段<br><span style="font-size: 12px; font-weight: 500; opacity: 0.8;">High-Risk Periods</span></th>
                    </tr>
                </thead>
                <tbody>
            """
            
            for index, p in enumerate(ports):
                row_bg = "#FFFFFF" if index % 2 == 0 else "#F9FAFB"
                
                try:
                    w_utc = p.max_wind_time_utc[5:] if len(p.max_wind_time_utc) > 5 else p.max_wind_time_utc
                    w_lct = p.max_wind_time_lct.split(' ')[1] if ' ' in p.max_wind_time_lct else p.max_wind_time_lct
                    g_utc = p.max_gust_time_utc[5:] if len(p.max_gust_time_utc) > 5 else p.max_gust_time_utc
                    g_lct = p.max_gust_time_lct.split(' ')[1] if ' ' in p.max_gust_time_lct else p.max_gust_time_lct
                    v_utc = p.max_wave_time_utc[5:] if len(p.max_wave_time_utc) > 5 else p.max_wave_time_utc
                    v_lct = p.max_wave_time_lct.split(' ')[1] if ' ' in p.max_wave_time_lct else p.max_wave_time_lct
                except:
                    w_utc, w_lct = p.max_wind_time_utc, p.max_wind_time_lct
                    g_utc, g_lct = p.max_gust_time_utc, p.max_gust_time_lct
                    v_utc, v_lct = p.max_wave_time_utc, p.max_wave_time_lct

                # 準備圖表 HTML (使用 Base64)
                chart_row = ""
                if p.chart_base64_list:
                    chart_imgs = ""
                    for idx, b64 in enumerate(p.chart_base64_list):
                        chart_imgs += f'<img src="data:image/png;base64,{b64}" style="width: 100%; max-width: 100%; height: auto; border: 2px solid #E5E7EB; border-radius: 8px; margin-top: {10 if idx == 0 else 15}px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">'
                    
                    chart_row = f"""
                    <tr style="background-color: {row_bg};">
                        <td colspan="3" style="padding: 20px 24px; border-bottom: 2px solid {style['border']};">
                            <div style="font-size: 15px; color: #374151; margin-bottom: 12px; font-weight: 600; {font_style}">
                                📈 氣象趨勢圖表 Weather Trend Charts
                            </div>
                            {chart_imgs}
                        </td>
                    </tr>
                    """
                
                wind_style = "color: #DC2626; font-weight: 800;" if p.max_wind_kts >= 28 else "color: #1F2937; font-weight: 700;"
                gust_style = "color: #DC2626; font-weight: 800;" if p.max_gust_kts >= 34 else "color: #1F2937; font-weight: 700;"
                wave_style = "color: #DC2626; font-weight: 800;" if p.max_wave >= 3.0 else "color: #1F2937; font-weight: 700;"
                
                html += f"""
                    <tr style="background-color: {row_bg};">
                        <!-- 港口資訊 -->
                        <td style="padding: 20px; vertical-align: top; border-bottom: 1px solid #E5E7EB; {font_style}">
                            <div style="background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%); padding: 12px 16px; border-radius: 8px; border-left: 4px solid #004B97; margin-bottom: 12px;">
                                <div style="font-size: 18px; font-weight: 800; color: #1E40AF; letter-spacing: 0.5px; margin-bottom: 6px;">{p.port_code}</div>
                                <div style="font-size: 13px; font-weight: 600; color: #1E3A8A; line-height: 1.4;">{p.port_name}</div>
                            </div>
                            <div style="font-size: 13px; color: #6B7280; line-height: 1.6;">
                                <div style="margin-bottom: 6px; display: flex; align-items: center;">
                                    <span style="font-size: 16px; margin-right: 6px;">📍</span>
                                    <strong>{p.country}</strong>
                                </div>
                                <div style="padding: 8px 12px; background-color: #F3F4F6; border-radius: 6px; font-size: 11px; color: #6B7280;">
                                    <div style="margin-bottom: 2px;"><strong>發布時間 Issued:</strong></div>
                                    <div>{p.issued_time[4:6]}/{p.issued_time[6:8]} {p.issued_time[9:11]}:{p.issued_time[11:13]} UTC</div>
                                </div>
                            </div>
                        </td>

                        <!-- 氣象數據 -->
                        <td style="padding: 20px; vertical-align: top; border-bottom: 1px solid #E5E7EB; {font_style}">
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr>
                                    <td style="padding: 10px 0; border-bottom: 1px solid #F3F4F6;">
                                        <div style="font-size: 12px; color: #6B7280; margin-bottom: 4px; font-weight: 600;">最大風速 Max Wind</div>
                                                                                <div style="font-size: 18px; {wind_style}">{p.max_wind_kts:.0f} <span style="font-size:13px; font-weight:normal; color:#6B7280;">kts</span> <span style="background-color: #E5E7EB; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 700; color: #374151;">BF{p.max_wind_bft}</span></div>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px 0; border-bottom: 1px solid #F3F4F6;">
                                        <div style="font-size: 12px; color: #6B7280; margin-bottom: 4px; font-weight: 600;">最大陣風 Max Gust</div>
                                        <div style="font-size: 18px; {gust_style}">{p.max_gust_kts:.0f} <span style="font-size:13px; font-weight:normal; color:#6B7280;">kts</span> <span style="background-color: #E5E7EB; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 700; color: #374151;">BF{p.max_gust_bft}</span></div>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px 0;">
                                        <div style="font-size: 12px; color: #6B7280; margin-bottom: 4px; font-weight: 600;">最大浪高 Max Wave</div>
                                        <div style="font-size: 18px; {wave_style}">{p.max_wave:.1f} <span style="font-size:13px; font-weight:normal; color:#6B7280;">m</span></div>
                                    </td>
                                </tr>
                            </table>
                        </td>

                        <!-- 高風險時段 -->
                        <td style="padding: 20px; vertical-align: top; border-bottom: 1px solid #E5E7EB; {font_style}">
                            <div style="margin-bottom: 14px;">
                                <div style="background: linear-gradient(135deg, #FEF2F2 0%, #FEE2E2 100%); border: 2px solid #FCA5A5; padding: 10px 14px; border-radius: 8px; box-shadow: 0 2px 6px rgba(220, 38, 38, 0.1);">
                                    <div style="font-size: 11px; color: #991B1B; font-weight: 700; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px;">⚠️ 潛在危險因子</div>
                                    <div style="font-size: 14px; color: #DC2626; font-weight: 700;">{', '.join(p.risk_factors)}</div>
                                </div>
                            </div>
                            
                            <div style="background-color: #F9FAFB; border-radius: 8px; padding: 14px 16px; border: 1px solid #E5E7EB;">
                                
                                <div style="margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #E5E7EB;">
                                    <div style="display: flex; align-items: center; margin-bottom: 6px;">
                                        <span style="font-size: 18px; margin-right: 8px;" title="Max Wind Time">💨</span>
                                        <span style="font-size: 12px; color: #6B7280; font-weight: 600;">預估最大風速時間</span>
                                    </div>
                                    <div style="padding-left: 26px;">
                                        <div style="font-weight: 700; color: #1F2937; font-size: 14px;">{w_lct} <span style="font-size: 11px; color: #9CA3AF; font-weight: 500;">(LT)</span></div>
                                        <div style="font-size: 12px; color: #6B7280; margin-top: 2px;">{w_utc} <span style="font-size: 10px; color: #9CA3AF;">(UTC)</span></div>
                                    </div>
                                </div>

                                <div style="margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #E5E7EB;">
                                    <div style="display: flex; align-items: center; margin-bottom: 6px;">
                                        <span style="font-size: 18px; margin-right: 8px;" title="Max Gust Time">🌬️</span>
                                        <span style="font-size: 12px; color: #6B7280; font-weight: 600;">預估最大陣風時間</span>
                                    </div>
                                    <div style="padding-left: 26px;">
                                        <div style="font-weight: 700; color: #1F2937; font-size: 14px;">{g_lct} <span style="font-size: 11px; color: #9CA3AF; font-weight: 500;">(LT)</span></div>
                                        <div style="font-size: 12px; color: #6B7280; margin-top: 2px;">{g_utc} <span style="font-size: 10px; color: #9CA3AF;">(UTC)</span></div>
                                    </div>
                                </div>

                                <div>
                                    <div style="display: flex; align-items: center; margin-bottom: 6px;">
                                        <span style="font-size: 18px; margin-right: 8px;" title="Max Wave Time">🌊</span>
                                        <span style="font-size: 12px; color: #6B7280; font-weight: 600;">預估最大浪高時間</span>
                                    </div>
                                    <div style="padding-left: 26px;">
                                        <div style="font-weight: 700; color: #1F2937; font-size: 14px;">{v_lct} <span style="font-size: 11px; color: #9CA3AF; font-weight: 500;">(LT)</span></div>
                                        <div style="font-size: 12px; color: #6B7280; margin-top: 2px;">{v_utc} <span style="font-size: 10px; color: #9CA3AF;">(UTC)</span></div>
                                    </div>
                                </div>

                            </div>
                        </td>
                    </tr>
                    {chart_row}
                """
            
            html += "</tbody></table>"

        # Footer (美化版)
        html += f"""
                <div style="margin-top: 50px; border-top: 2px solid #E5E7EB; padding-top: 30px; text-align: center; {font_style}">
                    <div style="background: linear-gradient(135deg, #F0F9FF 0%, #E0F2FE 100%); padding: 24px; border-radius: 10px; border: 1px solid #BAE6FD;">
                        <div style="font-size: 18px; font-weight: 700; color: #004B97; margin-bottom: 8px;">
                            Wan Hai Lines Ltd.
                        </div>
                        <div style="font-size: 15px; color: #0369A1; font-weight: 600; margin-bottom: 4px;">
                            Marine Technology Division
                        </div>
                        <div style="font-size: 16px; color: #DC2626; font-weight: 700; margin-top: 8px; padding: 8px 16px; background-color: #FEF2F2; border-radius: 6px; display: inline-block;">
                            📊 Presented by Fleet Risk Department
                        </div>
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #BAE6FD; font-size: 12px; color: #6B7280;">
                            <div style="margin-bottom: 4px;">
                                <strong>Data Source:</strong> Weathernews Inc. (WNI)
                            </div>
                            <div style="color: #9CA3AF;">
                                🤖 Automated Weather Monitoring System | Generated at {now_str_UTC} UTC
                            </div>
                        </div>
                    </div>
                </div>
            </div> 
            </div> 
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

