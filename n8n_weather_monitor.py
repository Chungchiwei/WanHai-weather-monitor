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
        """繪製風速趨勢圖，回傳 Base64 字串（高解析度版）"""
        if not assessment.raw_records:
            print(f"      ⚠️ {port_code} 沒有原始資料記錄")
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            if df.empty:
                print(f"      ⚠️ {port_code} DataFrame 為空")
                return None
            
            print(f"      📊 準備繪製 {port_code} 的風速圖 (資料點數: {len(df)})")
            
            plt.style.use('seaborn-v0_8-darkgrid')
            
            # 🔥 增加圖表尺寸
            fig, ax = plt.subplots(figsize=(18, 8))
            
            # 繪製曲線 - 加粗線條
            ax.plot(df['time'], df['wind_speed'], color='#2563EB', 
                label='Wind Speed (kts)', linewidth=3.5, marker='o', markersize=6, zorder=3)
            ax.plot(df['time'], df['wind_gust'], color='#DC2626', 
                linestyle='--', label='Gust (kts)', linewidth=2.8, marker='s', markersize=5, zorder=3)
            
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
            
            # 閾值線
            ax.axhline(RISK_THRESHOLDS['wind_danger'], color="#DC2626", 
                    linestyle=':', linewidth=2.5, label=f'Danger ({RISK_THRESHOLDS["wind_danger"]} kts)', zorder=2)   
            ax.axhline(RISK_THRESHOLDS['wind_warning'], color="#F59E0B", 
                    linestyle='--', linewidth=2.5, label=f'Warning ({RISK_THRESHOLDS["wind_warning"]} kts)', zorder=2)        
            ax.axhline(RISK_THRESHOLDS['wind_caution'], color="#FCD34D", 
                    linestyle=':', linewidth=2.2, label=f'Caution ({RISK_THRESHOLDS["wind_caution"]} kts)', zorder=2)
            
            # 標題與標籤 - 加大字體
            ax.set_title(f"{assessment.port_name} ({assessment.port_code}) - Wind Speed & Gust Trend (48 Hrs)", 
                        fontsize=20, fontweight='bold', pad=25, color='#1F2937')
            ax.set_ylabel('Speed (knots)', fontsize=16, fontweight='600', color='#374151')
            ax.set_xlabel('Date / Time (UTC)', fontsize=16, fontweight='600', color='#374151')
            ax.legend(loc='upper left', frameon=True, fontsize=13, shadow=True, fancybox=True)
            ax.grid(True, alpha=0.4, linestyle='--', linewidth=1)
            
            # 設定背景顏色
            ax.set_facecolor('#F9FAFB')
            fig.patch.set_facecolor('white')
            
            # 日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=30, ha='right', fontsize=12)
            plt.yticks(fontsize=12)
            
            # 加入邊框
            for spine in ax.spines.values():
                spine.set_edgecolor('#D1D5DB')
                spine.set_linewidth(2)
            
            plt.tight_layout()
            
            # 1. 存檔（高解析度）
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
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
        """繪製浪高趨勢圖，回傳 Base64 字串（高解析度版）"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            if df['wave_height'].max() < 1.0:
                return None

            plt.style.use('seaborn-v0_8-darkgrid')
            
            # 🔥 增加圖表尺寸
            fig, ax = plt.subplots(figsize=(18, 8))
            
            # 繪製曲線
            ax.plot(df['time'], df['wave_height'], color='#059669', 
                   label='Sig. Wave Height (m)', linewidth=3.5, marker='o', markersize=6, zorder=3)
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
                      linestyle=':', linewidth=2.2, label=f'Caution ({RISK_THRESHOLDS["wave_caution"]} m)', zorder=2)
            ax.axhline(RISK_THRESHOLDS['wave_warning'], color="#F59E0B", 
                      linestyle='--', linewidth=2.5, label=f'Warning ({RISK_THRESHOLDS["wave_warning"]} m)', zorder=2)
            ax.axhline(RISK_THRESHOLDS['wave_danger'], color="#DC2626", 
                      linestyle=':', linewidth=2.5, label=f'Danger ({RISK_THRESHOLDS["wave_danger"]} m)', zorder=2)    
            
            ax.set_title(f"{assessment.port_name} ({assessment.port_code}) - Wave Height Trend (48 Hrs)", 
                        fontsize=20, fontweight='bold', pad=25, color='#1F2937')
            ax.set_ylabel('Height (m)', fontsize=16, fontweight='600', color='#374151')
            ax.set_xlabel('Date / Time (UTC)', fontsize=16, fontweight='600', color='#374151')
            ax.legend(loc='upper left', frameon=True, fontsize=13, shadow=True, fancybox=True)
            ax.grid(True, alpha=0.4, linestyle='--', linewidth=1)
            
            ax.set_facecolor('#F9FAFB')
            fig.patch.set_facecolor('white')
            
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=30, ha='right', fontsize=12)
            plt.yticks(fontsize=12)
            
            for spine in ax.spines.values():
                spine.set_edgecolor('#D1D5DB')
                spine.set_linewidth(2)
            
            plt.tight_layout()
            
            # 1. 存檔（高解析度）
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
            
            # 2. 轉 Base64（高解析度）
            base64_str = self._fig_to_base64(fig, dpi=150)
            
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
        
        # 🔧 修正：為所有風險港口生成圖表（不限制等級）
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
        """生成 HTML 格式的精美報告 (WHL 風險管理 - Outlook 相容修正版)"""
        
        # ==================== 0. 基礎設置 ====================
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))

        # 時間計算
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')}(TPE)"
        now_str_UTC = f"{utc_now.strftime('%Y-%m-%d %H:%M')}(UTC)"
        
        # 輔助函數
        def format_time_display(time_str):
            if not time_str: return "N/A"
            try:
                if '(' in time_str: return time_str.split('(')[0].strip()
                return time_str
            except: return time_str

        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"

        # ==================== 1. 無風險情境 (Return Early) ====================
        if not assessments:
            return f"""
            <!DOCTYPE html>
            <html>
            <head><meta charset="UTF-8"></head>
            <body style="margin: 0; padding: 20px; background-color: #F0F4F8; {font_style}">
                <div style="max-width: 900px; margin: 0 auto; background-color: #E8F5E9; padding: 40px; border-left: 8px solid #4CAF50; border-radius: 4px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 15px;">✅</div>
                    <h2 style="margin: 0 0 10px 0; font-size: 28px; color: #2E7D32;">所有港口安全 All Ports Safe</h2>
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

        # ==================== 2. 風險分組 ====================
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        # ==================== 3. HTML 結構開始 ====================
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
        </head>
        <body style="margin: 0; padding: 0; background-color: #F0F4F8; {font_style}">
            <center>
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 900px; margin: 20px auto; background-color: #ffffff; box-shadow: 0 5px 15px rgba(0,0,0,0.1);">
        """
        # ==================== 4. 風險摘要 (Outlook 修復：使用 bgcolor) ====================
        html += f"""
                <tr>
                    <td style="padding: 25px 25px 0 25px;">
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 4px solid #DC2626;">
                            <tr>
                                <td bgcolor="#DC2626" style="background-color: #DC2626; padding: 25px 20px;">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                        <td width="70" valign="top" align="center"><font size="6" color="#ffffff">⚠️</font>
                                        </td>
                                        <td valign="middle">
                                            <b><font face="Microsoft JhengHei, sans-serif" size="5" color="#ffffff">
                                                WHL Port Weather Risk Monitor
                                            </font></b><br>                                            
                                            <font face="Arial, sans-serif" size="3" color="#FEE2E2">
                                                未來 48 小時港口惡劣天候預警系統<br>
                                                Weather Alert System for Ports in the Next 48 Hours
                                            </font><br>
                                            <font face="Arial, sans-serif" size="2" color="#cccccc"> Data Source: Weathernews Inc. (WNI)
                                            </font>
                                        </td>

                                        <td align="right" width="300">
                                            &nbsp;
                                        </td>
                                            <td align="right" width="300">
                                                <table border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 10px;">
                                                    <tr>
                                                        <td align="center" bgcolor="#ffffff" style="padding: 12px 15px;">
                                                            <div style="font-size: 32px; font-weight: bold; color: #DC2626; line-height: 1;">{len(risk_groups[3])}</div>
                                                            <div style="font-size: 12px; color: #999;">DANGER</div>
                                                        </td>
                                                        <td align="center" bgcolor="#ffffff" style="padding: 12px 15px; border-left: 2px solid #F3F4F6;">
                                                            <div style="font-size: 32px; font-weight: bold; color: #F59E0B; line-height: 1;">{len(risk_groups[2])}</div>
                                                            <div style="font-size: 12px; color: #999;">WARNING</div>
                                                        </td>
                                                        <td align="center" bgcolor="#ffffff" style="padding: 12px 15px; border-left: 2px solid #F3F4F6;">
                                                            <div style="font-size: 32px; font-weight: bold; color: #0EA5E9; line-height: 1;">{len(risk_groups[1])}</div>
                                                            <div style="font-size: 12px; color: #999;">CAUTION</div>
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
        """

        # ==================== 5. 行動指引 (Action Guidelines) ====================
        html += """
                <tr>
                    <td style="padding: 25px 25px 15px 25px;">
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #FFFBEB; border-left: 5px solid #F59E0B; margin-bottom: 25px;">
                            <tr>
                                <td bgcolor="#FFFBEB" style="padding: 20px 22px;">
                                    <div style="font-size: 15px; font-weight: 700; margin-bottom: 15px; color: #92400E; border-bottom: 1px solid rgba(146, 64, 14, 0.2); padding-bottom: 8px;">
                                        📋 船隊風險應對措施 Fleet Risk Response Actions:
                                    </div>
                                    <div style="margin-bottom: 12px;">
                                        <div style="font-size: 14px; font-weight: 700; color: #451a03;">✅ 請立即確認貴輪靠泊港口是否在風險名單中，並評估可能影響</div>
                                        <div style="font-size: 13px; color: #B45309; margin-top: 3px;">Immediately verify if your vessel's port of call is on the alert list and assess potential impacts.</div>
                                    </div>
                                    <div style="margin-bottom: 12px;">
                                        <div style="font-size: 14px; font-weight: 700; color: #451a03;">✅ 根據風險等級制定應對策略，包括可能的靠泊時間調整或替代方案</div>
                                        <div style="font-size: 13px; color: #B45309; margin-top: 3px;">Formulate response strategies based on risk levels, including schedule adjustments.</div>
                                    </div>
                                    <div>
                                        <div style="font-size: 14px; font-weight: 700; color: #451a03;">✅ 與岸基團隊、當地代理保持密切聯繫，及時報告船舶狀態和決策</div>
                                        <div style="font-size: 13px; color: #B45309; margin-top: 3px;">Maintain close contact with the Shore-based team and local agents; report decisions promptly.</div>
                                    </div>
                                </td>
                            </tr>
                        </table>
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 25px 0;">
                            <tr>
                                <td style="border-top: 3px dashed #D1D5DB; padding-top: 20px; text-align: center;">
                                    <div style="font-size: 15px; color: #4B5563; font-weight: 700; letter-spacing: 1px;">⬇️ 以下為各港口詳細氣象風險資料 ⬇️</div>
                                    <div style="font-size: 12px; color: #9CA3AF; margin-top: 5px; font-weight: 600;">DETAILED WEATHER RISK DATA FOR EACH PORT</div>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
        """

        # ==================== 6. 詳細港口資料區 (Detail Loops) ====================
        styles_detail = {
            3: {'color': '#DC2626', 'bg': '#FEF2F2', 'title_zh': '🔴 危險等級港口', 'title_en': 'DANGER LEVEL PORTS', 'border': '#DC2626', 'header_bg': '#FEE2E2', 'desc': '條件: Wind > 34 kts / Gust > 41 kts / Wave > 4.0 m'},
            2: {'color': '#F59E0B', 'bg': '#FFFBEB', 'title_zh': '🟠 警告等級港口', 'title_en': 'WARNING LEVEL PORTS', 'border': '#F59E0B', 'header_bg': '#FEF3C7', 'desc': '條件: Wind > 28 kts / Gust > 34 kts / Wave > 3.5 m'},
            1: {'color': '#0EA5E9', 'bg': '#F0F9FF', 'title_zh': '🟡 注意等級港口', 'title_en': 'CAUTION LEVEL PORTS', 'border': '#0EA5E9', 'header_bg': '#E0F2FE', 'desc': '條件: Wind > 22 kts / Gust > 28 kts / Wave > 2.5 m'}
        }

        # Main Loop for Levels
        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports: continue
            
            style = styles_detail[level]
            
            # Level Header
            html += f"""
                <tr><td style="padding: 0 25px;">
                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 10px;">
                        <tr><td bgcolor="{style['color']}" style="background-color: {style['color']}; color: white; padding: 10px 15px; font-weight: bold; font-size: 15px;">{style['title_zh']} {style['title_en']}</td></tr>
                        <tr><td style="font-size: 11px; color: #666; padding: 5px 0 8px 0;">{style['desc']}</td></tr>
                    </table>
                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 1px solid #E5E7EB; margin-bottom: 30px;">
                        <tr bgcolor="{style['header_bg']}" style="background-color: {style['header_bg']}; font-size: 12px; color: #666;">
                            <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 25%; font-weight: 600;">港口資訊 Port Info</th>
                            <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 30%; font-weight: 600;">氣象數據 Weather Data</th>
                            <th align="left" style="padding: 10px; border-bottom: 2px solid {style['border']}; width: 45%; font-weight: 600;">風險時段 Risk Period</th>
                        </tr>
            """
            
            # Inner Loop for Ports
            for index, p in enumerate(ports):
                # 變數計算
                row_bg = "#FFFFFF" if index % 2 == 0 else "#FAFBFC"
                
                # Styles
                wind_style = "color: #DC2626; font-weight: bold;" if p.max_wind_kts >= 28 else "color: #333;"
                gust_style = "color: #DC2626; font-weight: bold;" if p.max_gust_kts >= 34 else "color: #333;"
                wave_style = "color: #DC2626; font-weight: bold;" if p.max_wave >= 3.5 else "color: #333;"

                # Levels Text/Color
                if p.risk_level == 3: risk_level_bg, risk_level_color, risk_level_text, risk_level_icon = "#FEF2F2", "#DC2626", "高度風險 HIGH RISK", "🔴"
                elif p.risk_level == 2: risk_level_bg, risk_level_color, risk_level_text, risk_level_icon = "#FFFBEB", "#F59E0B", "中度風險 MEDIUM RISK", "🟠"
                else: risk_level_bg, risk_level_color, risk_level_text, risk_level_icon = "#F0F9FF", "#0EA5E9", "低度風險 LOW RISK", "🟡"

                wind_level_text = "強風" if p.max_wind_kts >= 34 else "中強風" if p.max_wind_kts >= 28 else "微風" if p.max_wind_kts >= 22 else ""
                wind_level_color = "#DC2626" if p.max_wind_kts >= 34 else "#F59E0B" if p.max_wind_kts >= 28 else "#0EA5E9" if p.max_wind_kts >= 22 else "#333"
                
                gust_level_text = "危險陣風" if p.max_gust_kts >= 41 else "強陣風" if p.max_gust_kts >= 34 else "中陣風" if p.max_gust_kts >= 28 else ""
                gust_level_color = "#DC2626" if p.max_gust_kts >= 41 else "#F59E0B" if p.max_gust_kts >= 34 else "#0EA5E9" if p.max_gust_kts >= 28 else "#333"
                
                wave_level_text = "危險浪高" if p.max_wave >= 4.0 else "高浪" if p.max_wave >= 3.5 else "中浪" if p.max_wave >= 2.5 else ""
                wave_level_color = "#DC2626" if p.max_wave >= 4.0 else "#F59E0B" if p.max_wave >= 3.5 else "#0EA5E9" if p.max_wave >= 2.5 else "#333"

                risk_duration = f"{len(p.risk_periods) * 3}" if p.risk_periods else "0"
                
                # Times
                w_utc, w_lct = format_time_display(p.max_wind_time_utc), format_time_display(p.max_wind_time_lct)
                g_utc, g_lct = format_time_display(p.max_gust_time_utc), format_time_display(p.max_gust_time_lct)
                v_utc, v_lct = format_time_display(p.max_wave_time_utc), format_time_display(p.max_wave_time_lct)

                # Append Row (加入 bgcolor 確保條紋效果在 Outlook 顯示)
                html += f"""
                    <tr bgcolor="{row_bg}" style="background-color: {row_bg}; border-bottom: 1px solid #E5E7EB;">
                        <td valign="top" style="padding: 15px;">
                            <div style="font-size: 20px; font-weight: 800; color: #1E3A8A; margin-bottom: 4px;">{p.port_code}</div>
                            <div style="font-size: 13px; color: #4B5563; font-weight: 600; margin-bottom: 4px;">{p.port_name}</div>
                            <div style="font-size: 12px; color: #6B7280; margin-bottom: 8px;">📍 {p.country}</div>
                            <div><span style="background-color: {risk_level_bg}; color: {risk_level_color}; font-size: 11px; font-weight: 700; padding: 3px 6px; border-radius: 3px; display: inline-block;">{risk_level_icon} {risk_level_text}</span></div>
                        </td>
                        <td valign="top" style="padding: 15px;">
                            <div style="margin-bottom: 10px; display: flex;">
                                <div style="width: 24px; font-size: 16px;">💨</div>
                                <div><span style="font-size: 11px; color: #6B7280; display: block;">風速 Wind</span>
                                    <span style="{wind_style} font-size: 16px; font-weight: 700;">{p.max_wind_kts:.0f} <span style="font-size: 12px;">kts</span></span>
                                    <span style="font-size: 11px; color: {wind_level_color}; margin-left: 6px; font-weight: 600;">{wind_level_text}</span>
                                </div>
                            </div>
                            <div style="margin-bottom: 10px; display: flex;">
                                <div style="width: 24px; font-size: 16px;">🌪️</div>
                                <div><span style="font-size: 11px; color: #6B7280; display: block;">陣風 Gust</span>
                                    <span style="{gust_style} font-size: 16px; font-weight: 700;">{p.max_gust_kts:.0f} <span style="font-size: 12px;">kts</span></span>
                                    <span style="font-size: 11px; color: {gust_level_color}; margin-left: 6px; font-weight: 600;">{gust_level_text}</span>
                                </div>
                            </div>
                            <div style="display: flex;">
                                <div style="width: 24px; font-size: 16px;">🌊</div>
                                <div><span style="font-size: 11px; color: #6B7280; display: block;">浪高 Wave</span>
                                    <span style="{wave_style} font-size: 16px; font-weight: 700;">{p.max_wave:.1f} <span style="font-size: 12px;">m</span></span>
                                    <span style="font-size: 11px; color: {wave_level_color}; margin-left: 6px; font-weight: 600;">{wave_level_text}</span>
                                </div>
                            </div>
                        </td>
                        <td valign="top" style="padding: 15px;">
                            <div style="margin-bottom: 12px;">
                                <span style="background-color: #FEF2F2; color: #B91C1C; border: 1px solid #FCA5A5; font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px; display: inline-block;">⚠️ 風險因素 Risk Factors: {', '.join(p.risk_factors[:2])}</span>
                            </div>
                            <table border="0" cellpadding="2" cellspacing="0" width="100%" style="font-size: 12px;">
                                <tr><td style="color:#6B7280; width:85px;">Max Wind:</td><td><b>{w_utc}</b> UTC / {w_lct} LT</td></tr>
                                <tr><td style="color:#6B7280;">Max Gust:</td><td><b>{g_utc}</b> UTC / {g_lct} LT</td></tr>
                                <tr><td style="color:#6B7280;">Max Wave:</td><td><b>{v_utc}</b> UTC / {v_lct} LT</td></tr>
                                <tr><td style="color:#991B1B; padding-top:8px; border-top:1px dashed #E5E7EB;">Duration:</td><td style="color:#991B1B; padding-top:8px; border-top:1px dashed #E5E7EB; font-weight:bold;">{risk_duration} Hours</td></tr>
                            </table>
                        </td>
                    </tr>
                """

                # 圖表列 (加入 bgcolor)
                if hasattr(p, 'chart_base64_list') and p.chart_base64_list:
                    chart_imgs = ""
                    for idx, b64 in enumerate(p.chart_base64_list):
                        b64_clean = b64.replace('\n', '').replace('\r', '').replace(' ', '')
                        chart_imgs += f"""
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 10px;">
                                <tr><td align="center"><img src="data:image/png;base64,{b64_clean}" width="750" style="display:block; max-width: 100%; height: auto; border: 1px solid #ddd;" alt="Chart"></td></tr>
                            </table>"""
                    html += f"""
                        <tr bgcolor="{row_bg}" style="background-color: {row_bg};"><td colspan="3" style="padding: 15px; border-bottom: 1px solid #eee;">
                            <div style="font-size: 13px; color: #666; margin-bottom: 8px; font-weight: 600;">📈 風速趨勢圖表 Wind Trend Chart:</div>
                            {chart_imgs}
                        </td></tr>
                    """

            html += "</table></td></tr>"

        # ==================== 7. Header (Outlook 修復：使用 bgcolor) ====================
        html += f"""
                <tr>
                    <td bgcolor="#004B97" style="background-color: #004B97; padding: 30px 25px; border-bottom: 4px solid #002D5A;">
                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                            <tr>
                                <td align="left" valign="middle">
                                    <div style="font-size: 28px; color: #ffffff; font-weight: 800; letter-spacing: 1.5px; margin-bottom: 6px; line-height: 1.2;">
                                        ⛴️ WHL 港口氣象風險通知
                                    </div>
                                    <div style="font-size: 16px; color: #BAE6FD; font-weight: 600; margin-bottom: 15px; letter-spacing: 0.5px; font-family: Helvetica, Arial, sans-serif;">
                                        WHL Port Weather Risk Notification
                                    </div>
                                    <table border="0" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td bgcolor="#005bb5" style="background-color: #005bb5; border: 1px solid rgba(255,255,255,0.25); border-radius: 4px; padding: 6px 12px;">
                                                <span style="font-size: 13px; color: #E0F2FE; font-weight: 500;">
                                                    📊 未來 48 小時天氣預報 <span style="color: #7DD3FC;">|</span> 48-Hour Weather Forecast
                                                </span>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                                <td align="right" valign="bottom" width="220">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 1px solid rgba(255,255,255,0.15);">
                                        <tr>
                                            <td bgcolor="#003d7a" style="background-color: #003d7a; padding: 10px 15px; text-align: right; border-radius: 8px;">
                                                <div style="font-size: 10px; color: #94A3B8; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600;">
                                                    REPORT GENERATED
                                                </div>
                                                <div style="font-family: monospace, sans-serif; font-size: 16px; color: #ffffff; font-weight: 700; margin-bottom: 2px;">
                                                    {now_str_TPE} <span style="font-size: 12px; color: #38BDF8; font-weight: normal;"></span>
                                                </div>
                                                <div style="font-family: monospace, sans-serif; font-size: 13px; color: #94A3B8;">
                                                    {now_str_UTC} <span style="font-size: 11px; color: #64748B;"></span>
                                                </div>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
        """
        # ==================== 8. Footer (移除 Logo 圖片，移除 W WH) ====================
        html += f"""
                <tr>
                    <td bgcolor="#F8F9FA" style="background-color: #F8F9FA; padding: 40px 20px; text-align: center; border-top: 1px solid #E5E7EB;">
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 0 auto;">
                            <tr>
                                <td align="center">
                                    <p style="margin: 0 0 4px 0; font-size: 16px; color: #111827; font-weight: 800; letter-spacing: 1px; font-family: 'Arial', sans-serif;">
                                        WAN HAI LINES LTD.
                                    </p>
                                    <p style="margin: 0 0 25px 0; font-size: 14px; color: #4B5563; font-weight: 500;">
                                        萬海航運股份有限公司
                                    </p>

                                    <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding-bottom: 20px;"><div style="border-top: 1px solid #D1D5DB; width: 40px;"></div></td></tr></table>
                                    
                                    <p style="margin: 0 0 5px 0; font-size: 13px; color: #4B5563; font-weight: 600;">
                                        Marine Technology Division | Fleet Risk Management Dept.
                                    </p>
                                    
                                    <table border="0" cellpadding="0" cellspacing="0" align="center" style="margin-bottom: 20px; margin-top: 10px;">
                                    </table>

                                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #F3F4F6; border: 1px solid #E5E7EB; border-radius: 6px;">
                                        <tr>
                                            <td style="padding: 12px 15px; text-align: left;">
                                                <div style="font-size: 11px; color: #9CA3AF; margin-bottom: 4px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">⚠️ 免責聲明 Disclaimer</div>
                                                <div style="font-size: 12px; color: #6B7280; line-height: 1.5;">
                                                    本信件內容僅供參考，船長仍應依據實際天候狀況與專業判斷採取適當措施。<br>
                                                    <span style="color: #9CA3AF;">This report is for reference only. Captains should take appropriate actions based on actual weather conditions.</span>
                                                </div>
                                            </td>
                                        </tr>
                                    </table>
                                    
                                    <div style="margin-top: 25px; font-size: 10px; color: #D1D5DB;">&copy; {now_str_TPE[:4]} Wan Hai Lines Ltd. All Rights Reserved.</div>
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

