# n8n_weather_monitor.py
"""
N8N 自動化氣象監控腳本 (含圖表生成功能)
用途：每天自動抓取港口天氣，分析高風險港口，生成趨勢圖，並發送到 Teams 與 Email
"""

import os
import sys
import json
import traceback
import sqlite3
import smtplib
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
from email.mime.image import MIMEImage

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
    chart_cids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ['raw_records', 'chart_cids']:
            d.pop(key, None)
        return d


# ================= 繪圖模組 =================

class ChartGenerator:
    """圖表生成器"""
    
    def __init__(self, output_dir: str = CHART_OUTPUT_DIR):
        self.output_dir = output_dir
        
        # 清空舊圖表
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
        """將 WeatherRecord 列表轉換為 DataFrame"""
        data = []
        for r in records:
            data.append({
                'time': r.time,
                'wind_speed': r.wind_speed_kts,
                'wind_gust': r.wind_gust_kts,
                'wave_height': r.wave_height
            })
        return pd.DataFrame(data)

    def generate_wind_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製風速趨勢圖"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            plt.style.use('bmh')
            fig, ax = plt.subplots(figsize=(10, 4.5))
            
            # 繪製曲線
            ax.plot(df['time'], df['wind_speed'], color='#1f77b4', 
                   label='Wind Speed (kts)', linewidth=2, marker='o', markersize=3)
            ax.plot(df['time'], df['wind_gust'], color='#ff7f0e', 
                   linestyle='--', label='Gust (kts)', linewidth=1.5, marker='s', markersize=3)
            
            # 填充
            ax.fill_between(df['time'], df['wind_speed'], alpha=0.2, color='#1f77b4')
            
            # 閾值線
            ax.axhline(RISK_THRESHOLDS['wind_caution'], color='#F59E0B', 
                      linestyle=':', linewidth=1.5, label=f'Caution ({RISK_THRESHOLDS["wind_caution"]}kts)')
            ax.axhline(RISK_THRESHOLDS['wind_warning'], color='#D9534F', 
                      linestyle='--', linewidth=1.5, label=f'Warning ({RISK_THRESHOLDS["wind_warning"]}kts)')
            
            # 標題與標籤
            ax.set_title(f'{assessment.port_name} ({port_code}) - Wind Trend', 
                        fontsize=13, fontweight='bold', pad=15)
            ax.set_ylabel('Speed (knots)', fontsize=11)
            ax.set_xlabel('Date / Time (UTC)', fontsize=11)
            ax.legend(loc='upper left', frameon=True, fontsize=9)
            ax.grid(True, alpha=0.3)
            
            # 日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %Hh'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=15, ha='right', fontsize=9)
            plt.yticks(fontsize=9)
            plt.tight_layout()
            
            # 存檔
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            plt.savefig(filepath, dpi=100, bbox_inches='tight')
            plt.close(fig)
            
            print(f"   ✅ 風速圖已生成: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"   ❌ 繪製風速圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None

    def generate_wave_chart(self, assessment: RiskAssessment, port_code: str) -> Optional[str]:
        """繪製浪高趨勢圖"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            
            # 如果浪很小就不畫
            if df['wave_height'].max() < 1.0:
                return None

            plt.style.use('bmh')
            fig, ax = plt.subplots(figsize=(10, 4.5))
            
            # 繪製曲線
            ax.plot(df['time'], df['wave_height'], color='#2ca02c', 
                   label='Sig. Wave Height (m)', linewidth=2, marker='o', markersize=3)
            ax.fill_between(df['time'], df['wave_height'], alpha=0.2, color='#2ca02c')
            
            # 閾值線
            ax.axhline(RISK_THRESHOLDS['wave_caution'], color='#F59E0B', 
                      linestyle=':', linewidth=1.5, label=f'Caution ({RISK_THRESHOLDS["wave_caution"]}m)')
            ax.axhline(RISK_THRESHOLDS['wave_warning'], color='#D9534F', 
                      linestyle='--', linewidth=1.5, label=f'Warning ({RISK_THRESHOLDS["wave_warning"]}m)')
            
            # 標題與標籤
            ax.set_title(f'{assessment.port_name} ({port_code}) - Wave Trend', 
                        fontsize=13, fontweight='bold', pad=15)
            ax.set_ylabel('Height (m)', fontsize=11)
            ax.set_xlabel('Date / Time (UTC)', fontsize=11)
            ax.legend(loc='upper left', frameon=True, fontsize=9)
            ax.grid(True, alpha=0.3)
            
            # 日期格式
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %Hh'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.xticks(rotation=15, ha='right', fontsize=9)
            plt.yticks(fontsize=9)
            plt.tight_layout()
            
            # 存檔
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            plt.savefig(filepath, dpi=100, bbox_inches='tight')
            plt.close(fig)
            
            print(f"   ✅ 浪高圖已生成: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"   ❌ 繪製浪高圖失敗 {port_code}: {e}")
            traceback.print_exc()
            return None


# ================= 風險分析模組 =================

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
            
            max_wind_record = max(records, key=lambda r: r.wind_speed_kts)
            max_gust_record = max(records, key=lambda r: r.wind_gust_kts)
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
                max_wind_kts=max_wind_record.wind_speed_kts,
                max_wind_bft=max_wind_record.wind_speed_bft,
                max_gust_kts=max_gust_record.wind_gust_kts,
                max_gust_bft=max_gust_record.wind_gust_bft,
                max_wave=max_wave_record.wave_height,
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


# ================= Gmail 通知器 =================

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
        Args:
            report_data: JSON 資料
            report_html: HTML 報告
            images: {'cid': 'file_path'} 例如 {'wind_KHH': 'charts/wind_KHH.png'}
        """
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密 (MAIL_USER / MAIL_PASSWORD)")
            return False

        # Root: MIMEMultipart('related') 用於嵌入圖片
        msg = MIMEMultipart('related')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_trigger
        
        # Alternative 部分 (純文字 + HTML)
        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        # 1. 純文字 (JSON)
        json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
        msg_alternative.attach(MIMEText(json_text, 'plain', 'utf-8'))
        
        # 2. HTML
        msg_alternative.attach(MIMEText(report_html, 'html', 'utf-8'))

        # 3. 嵌入圖片
        if images:
            for cid, file_path in images.items():
                if not os.path.exists(file_path):
                    print(f"⚠️ 圖片檔案不存在: {file_path}")
                    continue
                    
                try:
                    with open(file_path, 'rb') as fp:
                        img_data = fp.read()
                        img = MIMEImage(img_data)
                        img.add_header('Content-ID', f'<{cid}>')
                        img.add_header('Content-Disposition', 'inline', 
                                     filename=os.path.basename(file_path))
                        msg.attach(img)
                    print(f"   ✅ 圖片已附加: {cid} -> {file_path}")
                except Exception as e:
                    print(f"   ❌ 無法附加圖片 {file_path}: {e}")

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
            
            print(f"✅ Email 發送成功！(含 {len(images) if images else 0} 張圖表)")
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
        print(f"\n📈 步驟 3: 生成氣象趨勢圖 (針對 {len([r for r in risk_assessments if r.risk_level >= 2])} 個高風險港口)...")
        generated_charts = self._generate_charts(risk_assessments)
        
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
                report_data, report_html, generated_charts
            )
        except Exception as e:
            print(f"⚠️ 發信過程發生異常: {e}")
            traceback.print_exc()
        
        report_data['email_sent'] = email_sent
        report_data['teams_sent'] = teams_sent
        report_data['charts_generated'] = len(generated_charts)
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print(f"   - 風險港口: {len(risk_assessments)}")
        print(f"   - 圖表生成: {len(generated_charts)}")
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
    
    def _generate_charts(self, assessments: List[RiskAssessment]) -> Dict[str, str]:
        """生成圖表"""
        generated_charts = {}
        
        # 優先處理高風險港口
        chart_targets = [r for r in assessments if r.risk_level >= 2]
        
        # 如果高風險港口少，補充部分 Caution 港口
        if len(chart_targets) < 5:
            cautions = [r for r in assessments if r.risk_level == 1]
            chart_targets.extend(cautions[:(10 - len(chart_targets))])
        
        for assessment in chart_targets:
            # 風速圖
            wind_path = self.chart_generator.generate_wind_chart(
                assessment, assessment.port_code
            )
            if wind_path:
                cid = f"wind_{assessment.port_code}"
                generated_charts[cid] = wind_path
                assessment.chart_cids.append(cid)
            
            # 浪高圖 (只在有高浪風險時生成)
            if assessment.max_wave >= RISK_THRESHOLDS['wave_caution']:
                wave_path = self.chart_generator.generate_wave_chart(
                    assessment, assessment.port_code
                )
                if wave_path:
                    cid = f"wave_{assessment.port_code}"
                    generated_charts[cid] = wave_path
                    assessment.chart_cids.append(cid)
        
        return generated_charts
    
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
        """生成 HTML 格式的精美報告 (整合 File 1 美感與 File 2 功能)"""
        
        # 定義字型堆疊：微軟正黑體 > Segoe UI > Arial
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"
        
        # 時間計算
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')
        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')

        # 若無風險的顯示
        if not assessments:
            return f"""
            <div style="{font_style} color: #2E7D32; padding: 20px; border: 1px solid #4CAF50; background-color: #E8F5E9; border-radius: 5px;">
                <h3 style="margin-top: 0;">🟢 System Status: Safety</h3>
                <p>未來48Hrs內所有靠泊港口均處於安全範圍 (All ports are within safe limits).</p>
            </div>
            """
            
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        # Email Header (完全套用 File 1 風格)
        html = f"""
        <html>
        <body style="margin: 0; padding: 0; background-color: #f4f4f4; {font_style}">
            <div style="max-width: 800px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        
            <div style="background-color: #004B97; color: white; padding: 24px 30px;">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <h2 style="margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px;">
                        ⛴️ WHL Port Weather Risk Monitor
                    </h2>
                </div>
                <div style="margin-top: 8px; font-size: 13px; color: #a3cbe8; font-weight: 500;">
                    📅 UPDATED: {now_str_LT} (TPE) <span style="opacity: 0.5;">|</span> {now_str_UTC} (UTC)
                </div>
            </div>

            <div style="padding: 30px;">
            
                <div style="background-color: #fff5f5; border-left: 5px solid #D9534F; padding: 20px; border-radius: 4px; margin-bottom: 20px;">
                    <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #D9534F; font-weight: bold;">
                        📊 未來 48Hrs 風險港口監控摘要
                    </h3>
                    <div style="font-size: 15px; color: #333; line-height: 1.6;">
                        目前共有 <span style="font-size: 24px; font-weight: bold; color: #D9534F; vertical-align: middle; margin: 0 5px;">{len(assessments)}</span> 個港口具有潛在氣象風險。
                    </div>
                </div>

                <div style="font-size: 14px; color: #555; background-color: #f8f9fa; padding: 15px; border-radius: 6px; border: 1px solid #eee;">
                    <span style="font-size: 16px;">⚠️</span> 
                    請船管 PIC留意下列港口動態並通知業管屬輪做好相關<span style="background-color: red; color: white; padding: 3px 0px; border-radius: 0px; font-weight: bold; font-size: 12px;">風險評估措施。</span> 
                </div>
        """

        # 風險等級樣式定義
        styles = {
            3: {'color': '#D9534F', 'bg': '#FEF2F2', 'title': '🔴 POTENTIAL DANGER PORT (條件: 風速 > 8級 / 陣風 > 9級 / 浪高 > 4.0 m)', 'border': '#D9534F', 'header_bg': '#FEE2E2'},
            2: {'color': '#F59E0B', 'bg': '#FFFBEB', 'title': '🟠 POTENTIAL WARNING PORT (條件: 風速 > 7級 / 陣風 > 8級 / 浪高 > 3.5 m)', 'border': '#F59E0B', 'header_bg': '#FEF3C7'},
            1: {'color': '#0EA5E9', 'bg': '#F0F9FF', 'title': '🟡 POTENTIAL CAUTION PORT (條件: 風速 > 6級 / 陣風 > 7級 / 浪高 > 2.5 m)', 'border': '#0EA5E9', 'header_bg': '#E0F2FE'}
        }

        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports:
                continue
            
            style = styles[level]
            
            # 該等級的標題
            html += f"""
            <div style="margin-top: 30px; margin-bottom: 12px;">
                <span style="background-color: {style['color']}; color: white; padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 14px; {font_style}">
                    {style['title']}
                </span>
            </div>
            
            <table style="width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; border: 1px solid #e5e7eb; border-radius: 6px; overflow: hidden;">
                <thead>
                    <tr style="background-color: {style['header_bg']}; color: #4b5563; text-align: left;">
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; width: 25%; {font_style}">港口名稱(Port Name)</th>
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; width: 35%; {font_style}">潛在風險(Potential Crisis) (Met Data)</th>
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; {font_style}">高風險時段(High-risk periods) & Time</th>
                    </tr>
                </thead>
                <tbody>
            """
            
            for index, p in enumerate(ports):
                row_bg = "#ffffff" if index % 2 == 0 else "#f9fafb"
                wind_val_style = "color: #D9534F; font-weight: bold; font-size: 15px;" if p.max_wind_kts >= 30 else "font-weight: bold;"
                wave_val_style = "color: #D9534F; font-weight: bold; font-size: 15px;" if p.max_wave >= 3.0 else "font-weight: bold;"
                
                # 處理時間顯示
                # 嘗試安全擷取 MM-DD HH:MM 格式，若格式不符則顯示原字串
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

                # 準備圖表 HTML (若有圖表，將顯示在獨立的列)
                chart_row = ""
                if p.chart_cids:
                    chart_imgs = ""
                    for cid in p.chart_cids:
                        chart_imgs += f'<img src="cid:{cid}" style="max-width: 100%; height: auto; border: 1px solid #eee; border-radius: 4px; margin-top: 10px;">'
                    
                    chart_row = f"""
                    <tr style="background-color: {row_bg};">
                        <td colspan="3" style="padding: 0 15px 15px 15px; border-bottom: 1px solid #e5e7eb;">
                            <div style="font-size: 20px; color: #666; margin-bottom: 10px;">📈 未來24Hrs風力趨勢圖:</div>
                            {chart_imgs}
                        </td>
                    </tr>
                    """

                html += f"""
                <tr style="background-color: {row_bg};">
                    <td style="padding: 12px 15px; border-bottom: {('1px solid #e5e7eb' if not p.chart_cids else 'none')}; vertical-align: top; {font_style}">
                        <div style="font-size: 16px; font-weight: bold; color: #1f2937;">{p.port_code}</div>
                        <div style="margin-top: 2px; color: #374151;">{p.port_name}</div>
                        <div style="margin-top: 4px; color: #6b7280; font-size: 12px;">📍 {p.country}</div>
                        <div style="margin-top: 8px; font-size: 11px; color: #999;">📡 Issued: {p.issued_time}</div>
                    </td>
                    <td style="padding: 12px 15px; border-bottom: {('1px solid #e5e7eb' if not p.chart_cids else 'none')}; vertical-align: top; {font_style}">
                        <div style="margin-bottom: 6px;">
                            <span style="color: #6b7280; width: 45px; display: inline-block;">Wind:</span> 
                            <span style="{wind_val_style}">{p.max_wind_kts:.0f} kts</span> <span style="font-size:12px; color:#666;">(Bf {p.max_wind_bft})</span>
                        </div>
                        <div style="margin-bottom: 6px;">
                            <span style="color: #6b7280; width: 45px; display: inline-block;">Gust:</span> 
                            <span style="font-weight: bold;">{p.max_gust_kts:.0f} kts</span> <span style="font-size:12px; color:#666;">(Bf {p.max_gust_bft})</span>
                        </div>
                        <div>
                            <span style="color: #6b7280; width: 45px; display: inline-block;">Wave:</span> 
                            <span style="{wave_val_style}">{p.max_wave:.1f} m</span>
                        </div>
                    </td>
                    <td style="padding: 12px 15px; border-bottom: {('1px solid #e5e7eb' if not p.chart_cids else 'none')}; vertical-align: top; {font_style}">
                        <div style="margin-bottom: 6px; color: #b91c1c; background-color: #fef2f2; display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 13px;">
                            ⚠️ {', '.join(p.risk_factors)}
                        </div>
                        
                        <div style="color: #4b5563; font-size: 13px; margin-top: 4px; line-height: 1.4;">
                            <span style="display:inline-block; width:16px;">💨</span> 
                            預估最高風速發生時間: <b>{w_utc}</b> (UTC) <span style="color:#999">/</span> {w_lct} (LT)
                        </div>
                        
                        <div style="color: #4b5563; font-size: 13px; margin-top: 4px; line-height: 1.4;">
                            <span style="display:inline-block; width:16px;">💨</span> 
                            預估最高陣風發生時間: <b>{g_utc}</b> (UTC) <span style="color:#999">/</span> {g_lct} (LT)
                        </div>
                        
                        <div style="color: #4b5563; font-size: 13px; margin-top: 4px; line-height: 1.4;">
                            <span style="display:inline-block; width:16px;">🌊</span> 
                           預估最大浪高發生時間: <b>{v_utc}</b> (UTC) <span style="color:#999">/</span> {v_lct} (LT)
                        </div>
                    </td>
                </tr>
                {chart_row}
                """
            
            html += "</tbody></table>"

        # Footer
        html += f"""
                <div style="margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 20px; font-size: 15px; color: #9ca3af; text-align: center; {font_style}">
                    <p style="margin: 0;">Wan Hai Lines Ltd. | Marine Technology Division</p>
                    <p style="margin: 0;color: #004B97; font-weight:bold;">Present by Fleet Risk Department</p>
                    <p style="margin: 5px 0 0 0; font-size: 12px;">Data Source: Weathernews Inc. (WNI) | Automated System</p>
                </div>
            </div> </div> </body>
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
