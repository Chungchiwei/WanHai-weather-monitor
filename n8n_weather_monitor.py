# n8n_weather_monitor.py
import os
import sys
import json
import traceback
import smtplib
import io
import base64
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field

# 第三方套件
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
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

# 2. 公司 SMTP 設定
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.office365.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', 'your_account@wanhai.com')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')

# 3. Power Automate 觸發信箱
PA_TRIGGER_EMAIL = os.getenv('PA_TRIGGER_EMAIL', 'whl.weather.bot@wanhai.com')
PA_TRIGGER_SUBJECT_FLEET = "WHL_WEATHER_FLEET_REPORT"
PA_TRIGGER_SUBJECT_PORT = "WHL_WEATHER_PORT_NOTIFICATION"
PA_TRIGGER_SUBJECT_COUNTRY = "WHL_WEATHER_COUNTRY_SUMMARY"

# 4. 船隊收件人
TARGET_EMAIL = os.getenv('TARGET_EMAIL', 'harry_chung@wanhai.com')

# 5. Teams Webhook
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL', '')

# 6. 檔案路徑
EXCEL_FILE_PATH = os.getenv('EXCEL_FILE_PATH', 'WHL_all_ports_list.xlsx')
PORT_AGENTS_DB_PATH = os.getenv('PORT_AGENTS_DB_PATH', 'port_agents.json')
CHART_OUTPUT_DIR = 'charts'

# 7. 風險閾值
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

# ================= 資料結構 =================

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

# ================= 港口代理管理器 =================

class PortAgentManager:
    """港口代理信箱管理器（支援國家層級）"""
    
    def __init__(self, db_path: str = PORT_AGENTS_DB_PATH):
        self.db_path = db_path
        self.agents_data = self._load_agents_db()
    
    def _load_agents_db(self) -> Dict[str, Any]:
        """載入代理資料庫"""
        try:
            if not os.path.exists(self.db_path):
                print(f"⚠️ 警告: 找不到代理資料庫 {self.db_path}，將使用空資料庫")
                return {"countries": {}}
            
            with open(self.db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            total_ports = sum(len(country['ports']) for country in data.get('countries', {}).values())
            print(f"✅ 已載入 {len(data.get('countries', {}))} 個國家，共 {total_ports} 個港口")
            return data
            
        except Exception as e:
            print(f"❌ 載入代理資料庫失敗: {e}")
            return {"countries": {}}
    
    def get_country_code(self, port_code: str) -> Optional[str]:
        """根據港口代碼取得國家代碼"""
        for country_code, country_data in self.agents_data.get('countries', {}).items():
            if port_code in country_data.get('ports', {}):
                return country_code
        return None
    
    def get_country_info(self, country_code: str) -> Optional[Dict[str, Any]]:
        """取得國家資訊"""
        return self.agents_data.get('countries', {}).get(country_code)
    
    def get_port_info(self, port_code: str) -> Optional[Dict[str, Any]]:
        """取得港口資訊"""
        country_code = self.get_country_code(port_code)
        if not country_code:
            return None
        
        country_data = self.get_country_info(country_code)
        if not country_data:
            return None
        
        port_info = country_data.get('ports', {}).get(port_code)
        if port_info:
            # 加入國家資訊
            port_info['country_code'] = country_code
            port_info['country_name'] = country_data.get('country_name', '')
            port_info['country_name_en'] = country_data.get('country_name_en', '')
        
        return port_info
    
    def get_port_agent_emails(self, port_code: str) -> List[str]:
        """取得港口代理信箱"""
        port_info = self.get_port_info(port_code)
        if not port_info:
            return []
        return port_info.get('agent_emails', [])
    
    def get_country_emails(self, country_code: str) -> List[str]:
        """取得國家層級信箱"""
        country_info = self.get_country_info(country_code)
        if not country_info:
            return []
        return country_info.get('country_emails', [])
    
    def should_send_individual(self, port_code: str) -> bool:
        """檢查是否要發送單一港口通知"""
        port_info = self.get_port_info(port_code)
        if not port_info:
            return False
        return port_info.get('send_individual', False) and len(port_info.get('agent_emails', [])) > 0
    
    def should_send_country_summary(self, country_code: str) -> bool:
        """檢查是否要發送國家摘要"""
        country_info = self.get_country_info(country_code)
        if not country_info:
            return False
        return country_info.get('send_country_summary', False) and len(country_info.get('country_emails', [])) > 0
    
    def get_country_risk_ports(self, country_code: str, risk_assessments: List[RiskAssessment]) -> List[RiskAssessment]:
        """取得該國家的所有風險港口"""
        country_info = self.get_country_info(country_code)
        if not country_info:
            return []
        
        port_codes = set(country_info.get('ports', {}).keys())
        return [a for a in risk_assessments if a.port_code in port_codes]
    
    def reload(self):
        """重新載入代理資料庫"""
        self.agents_data = self._load_agents_db()
        print(f"🔄 代理資料庫已重新載入")

# ================= 內部郵件發送器 =================

class InternalEmailSender:
    """使用公司 SMTP 發送內部郵件"""
    
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.smtp_user = SMTP_USER
        self.smtp_password = SMTP_PASSWORD
        self.pa_trigger_email = PA_TRIGGER_EMAIL
    
    def send_email(self, subject: str, body_html: str, 
                   attachments: Optional[Dict[str, str]] = None) -> bool:
        """發送郵件到 Power Automate 觸發信箱"""
        
        if not self.smtp_user or not self.smtp_password:
            print("⚠️ 未設定 SMTP 帳密")
            return False
        
        try:
            msg = MIMEMultipart('mixed')
            msg['From'] = self.smtp_user
            msg['To'] = self.pa_trigger_email
            msg['Subject'] = subject
            msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0800')
            
            # HTML 內容
            msg_alternative = MIMEMultipart('alternative')
            msg_alternative.attach(MIMEText(body_html, 'html', 'utf-8'))
            msg.attach(msg_alternative)
            
            # 附件（JSON 資料）
            if attachments:
                for filename, content in attachments.items():
                    attachment = MIMEText(content, 'plain', 'utf-8')
                    attachment.add_header('Content-Disposition', 'attachment', 
                                        filename=filename)
                    msg.attach(attachment)
            
            print(f"📧 正在發送郵件到 {self.pa_trigger_email}...")
            print(f"   主旨: {subject}")
            
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, self.pa_trigger_email, msg.as_string())
            server.quit()
            
            print(f"✅ 郵件發送成功")
            return True
            
        except Exception as e:
            print(f"❌ 郵件發送失敗: {e}")
            traceback.print_exc()
            return False

# ================= Power Automate 觸發器 =================

class PowerAutomateEmailTrigger:
    """透過郵件觸發 Power Automate（支援國家層級通知）"""
    
    def __init__(self, agent_manager: Optional[PortAgentManager] = None):
        self.email_sender = InternalEmailSender()
        self.agent_manager = agent_manager or PortAgentManager()
    
    def send_fleet_report_trigger(self, report_data: dict, report_html: str,
                                  risk_assessments: List[RiskAssessment]) -> bool:
        """發送船隊報告觸發郵件（包含所有風險港口）"""
        
        json_data = json.dumps({
            "trigger_type": "fleet_report",
            "risk_count": len(risk_assessments),
            "report_data": report_data,
            "timestamp": datetime.now().isoformat(),
            "target_email": TARGET_EMAIL
        }, ensure_ascii=False, indent=2)
        
        attachments = {
            "report_data.json": json_data
        }
        
        return self.email_sender.send_email(
            subject=PA_TRIGGER_SUBJECT_FLEET,
            body_html=report_html,
            attachments=attachments
        )
    
    def send_port_notification_trigger(self, assessment: RiskAssessment,
                                      single_port_html: str) -> bool:
        """發送單一港口通知觸發郵件"""
        
        port_code = assessment.port_code
        
        if not self.agent_manager.should_send_individual(port_code):
            return False
        
        port_info = self.agent_manager.get_port_info(port_code)
        country_code = self.agent_manager.get_country_code(port_code)
        
        risk_label = {
            3: "🔴 DANGER", 
            2: "🟠 WARNING", 
            1: "🟡 CAUTION"
        }.get(assessment.risk_level, "⚪ INFO")
        
        json_data = json.dumps({
            "trigger_type": "port_notification",
            "port_code": port_code,
            "port_name": assessment.port_name,
            "country_code": country_code,
            "agent_emails": port_info['agent_emails'],
            "agent_name": port_info.get('agent_name', 'Port Agent'),
            "risk_level": assessment.risk_level,
            "risk_label": risk_label,
            "max_wind_kts": assessment.max_wind_kts,
            "max_gust_kts": assessment.max_gust_kts,
            "max_wave": assessment.max_wave,
            "timestamp": datetime.now().isoformat()
        }, ensure_ascii=False, indent=2)
        
        attachments = {
            f"{port_code}_port_data.json": json_data
        }
        
        subject = f"{PA_TRIGGER_SUBJECT_PORT}_{port_code}"
        
        print(f"   📧 發送 {port_code} 單一港口通知")
        print(f"      收件者: {', '.join(port_info['agent_emails'])}")
        
        return self.email_sender.send_email(
            subject=subject,
            body_html=single_port_html,
            attachments=attachments
        )
    
    def send_country_summary_trigger(self, country_code: str,
                                    country_assessments: List[RiskAssessment],
                                    country_summary_html: str) -> bool:
        """發送國家摘要通知觸發郵件"""
        
        if not self.agent_manager.should_send_country_summary(country_code):
            return False
        
        country_info = self.agent_manager.get_country_info(country_code)
        
        json_data = json.dumps({
            "trigger_type": "country_summary",
            "country_code": country_code,
            "country_name": country_info['country_name'],
            "country_name_en": country_info['country_name_en'],
            "country_emails": country_info['country_emails'],
            "risk_port_count": len(country_assessments),
            "risk_ports": [a.port_code for a in country_assessments],
            "timestamp": datetime.now().isoformat()
        }, ensure_ascii=False, indent=2)
        
        attachments = {
            f"{country_code}_country_summary.json": json_data
        }
        
        subject = f"{PA_TRIGGER_SUBJECT_COUNTRY}_{country_code}"
        
        print(f"   📧 發送 {country_code} ({country_info['country_name']}) 國家摘要")
        print(f"      收件者: {', '.join(country_info['country_emails'])}")
        print(f"      包含港口: {', '.join([a.port_code for a in country_assessments])}")
        
        return self.email_sender.send_email(
            subject=subject,
            body_html=country_summary_html,
            attachments=attachments
        )
    
    def send_all_notifications(self, risk_assessments: List[RiskAssessment]) -> Dict[str, Any]:
        """批次發送所有通知（港口 + 國家）"""
        
        results = {
            'port_notifications': {},
            'country_summaries': {}
        }
        
        # 1. 發送單一港口通知
        print(f"\n📧 步驟 1: 發送單一港口通知...")
        port_count = 0
        for assessment in risk_assessments:
            if self.agent_manager.should_send_individual(assessment.port_code):
                single_port_html = self._generate_single_port_html(assessment)
                success = self.send_port_notification_trigger(assessment, single_port_html)
                results['port_notifications'][assessment.port_code] = success
                if success:
                    port_count += 1
                time.sleep(1)
        
        if results['port_notifications']:
            print(f"   ✅ 單一港口通知: {port_count}/{len(results['port_notifications'])} 成功")
        else:
            print(f"   ⚠️ 沒有港口需要發送單一通知")
        
        # 2. 按國家分組
        print(f"\n📧 步驟 2: 發送國家摘要通知...")
        country_groups = {}
        for assessment in risk_assessments:
            country_code = self.agent_manager.get_country_code(assessment.port_code)
            if country_code:
                if country_code not in country_groups:
                    country_groups[country_code] = []
                country_groups[country_code].append(assessment)
        
        # 3. 發送國家摘要
        country_count = 0
        for country_code, assessments in country_groups.items():
            if self.agent_manager.should_send_country_summary(country_code):
                country_summary_html = self._generate_country_summary_html(country_code, assessments)
                success = self.send_country_summary_trigger(country_code, assessments, country_summary_html)
                results['country_summaries'][country_code] = success
                if success:
                    country_count += 1
                time.sleep(1)
        
        if results['country_summaries']:
            print(f"   ✅ 國家摘要通知: {country_count}/{len(results['country_summaries'])} 成功")
        else:
            print(f"   ⚠️ 沒有國家需要發送摘要通知")
        
        return results
    
    def _generate_single_port_html(self, assessment: RiskAssessment) -> str:
        """為單一港口生成 HTML 報告"""
        
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"
        
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        
        risk_styles = {
            3: {'color': '#DC2626', 'bg': '#FEF2F2', 'label': '🔴 DANGER'},
            2: {'color': '#F59E0B', 'bg': '#FFFBEB', 'label': '🟠 WARNING'},
            1: {'color': '#0EA5E9', 'bg': '#F0F9FF', 'label': '🟡 CAUTION'}
        }
        
        style = risk_styles.get(assessment.risk_level, risk_styles[1])
        
        # 圖表處理
        chart_html = ""
        if hasattr(assessment, 'chart_base64_list') and assessment.chart_base64_list:
            for idx, b64 in enumerate(assessment.chart_base64_list):
                b64_clean = b64.replace('\n', '').replace('\r', '').replace(' ', '')
                chart_html += f"""
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 15px;">
                    <tr>
                        <td align="center">
                            <img src="data:image/png;base64,{b64_clean}" 
                                width="750" 
                                style="display:block; max-width: 100%; height: auto; border: 1px solid #ddd;" 
                                alt="Weather Chart {idx+1}">
                        </td>
                    </tr>
                </table>
                """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="margin: 0; padding: 0; background-color: #F0F4F8; {font_style}">
            <center>
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 900px; margin: 20px auto; background-color: #ffffff;">
                
                <tr>
                    <td style="background-color: #004B97; padding: 20px;">
                        <h1 style="margin: 0; font-size: 22px; color: #ffffff; font-weight: bold;">
                            ⚠️ Port Weather Risk Alert
                        </h1>
                        <div style="margin-top: 3px; font-size: 13px; color: #B3D9FF;">
                            48-Hour Weather Forecast | 未來 48 小時天氣預報
                        </div>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 25px;">
                        
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: {style['bg']}; border-left: 6px solid {style['color']}; margin-bottom: 20px;">
                            <tr>
                                <td style="padding: 20px;">
                                    <div style="font-size: 32px; font-weight: bold; color: {style['color']}; margin-bottom: 10px;">
                                        {style['label']} - {assessment.port_code}
                                    </div>
                                    <div style="font-size: 20px; color: #374151; margin-bottom: 5px;">
                                        {assessment.port_name} | {assessment.country}
                                    </div>
                                    <div style="font-size: 14px; color: #6B7280; margin-top: 10px;">
                                        📅 Issued: {now_str_TPE}
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 20px;">
                            <tr>
                                <td style="padding: 15px; background-color: #F9FAFB; border: 1px solid #E5E7EB;">
                                    <table border="0" cellpadding="8" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="50%" style="font-size: 14px; color: #6B7280;">
                                                <strong style="color: #111827;">Max Wind Speed:</strong><br>
                                                <span style="font-size: 24px; color: #DC2626; font-weight: bold;">{assessment.max_wind_kts:.0f} kts</span>
                                                <span style="font-size: 14px; color: #6B7280;">(BF{assessment.max_wind_bft})</span><br>
                                                <span style="font-size: 11px; color: #9CA3AF;">at {assessment.max_wind_time_utc}</span>
                                            </td>
                                            <td width="50%" style="font-size: 14px; color: #6B7280;">
                                                <strong style="color: #111827;">Max Gust:</strong><br>
                                                <span style="font-size: 24px; color: #DC2626; font-weight: bold;">{assessment.max_gust_kts:.0f} kts</span>
                                                <span style="font-size: 14px; color: #6B7280;">(BF{assessment.max_gust_bft})</span><br>
                                                <span style="font-size: 11px; color: #9CA3AF;">at {assessment.max_gust_time_utc}</span>
                                            </td>
                                        </tr>
                                        <tr>
                                            <td colspan="2" style="font-size: 14px; color: #6B7280; padding-top: 10px;">
                                                <strong style="color: #111827;">Max Wave Height:</strong><br>
                                                <span style="font-size: 24px; color: #0EA5E9; font-weight: bold;">{assessment.max_wave:.1f} m</span><br>
                                                <span style="font-size: 11px; color: #9CA3AF;">at {assessment.max_wave_time_utc}</span>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>

                        {chart_html}

                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-top: 20px; background-color: #FFFBEB; border-left: 4px solid #F59E0B;">
                            <tr>
                                <td style="padding: 15px; font-size: 13px; color: #78350F; line-height: 1.7;">
                                    <strong>⚠️ Action Required:</strong><br>
                                    • Please confirm latest port operation status<br>
                                    • Prepare necessary safety measures<br>
                                    • Monitor weather updates regularly<br>
                                    • Coordinate with vessel master for berthing arrangements
                                </td>
                            </tr>
                        </table>

                    </td>
                </tr>

                <tr>
                    <td style="background-color: #F8F9FA; padding: 20px; text-align: center; color: #9CA3AF; font-size: 12px; border-top: 1px solid #E5E7EB;">
                        <p style="margin: 0 0 6px 0; font-size: 13px; color: #6B7280;">
                            <strong>Wan Hai Lines Ltd. | 萬海航運股份有限公司</strong>
                        </p>
                        <p style="margin: 0; font-size: 11px; color: #D1D5DB;">
                            Marine Technology Division | Automated Weather Monitoring System
                        </p>
                    </td>
                </tr>
            </table>
            </center>
        </body>
        </html>
        """
        
        return html
    
    def _generate_country_summary_html(self, country_code: str, 
                                      assessments: List[RiskAssessment]) -> str:
        """為國家生成摘要 HTML 報告"""
        
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"
        
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        
        country_info = self.agent_manager.get_country_info(country_code)
        country_name = country_info['country_name']
        country_name_en = country_info['country_name_en']
        
        # 風險分組
        danger_ports = [a for a in assessments if a.risk_level == 3]
        warning_ports = [a for a in assessments if a.risk_level == 2]
        caution_ports = [a for a in assessments if a.risk_level == 1]
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="margin: 0; padding: 0; background-color: #F0F4F8; {font_style}">
            <center>
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 900px; margin: 20px auto; background-color: #ffffff;">
                
                <tr>
                    <td style="background-color: #004B97; padding: 20px;">
                        <h1 style="margin: 0; font-size: 22px; color: #ffffff; font-weight: bold;">
                            ⚠️ {country_name} ({country_name_en}) 港口氣象風險摘要
                        </h1>
                        <h1 style="margin: 5px 0 0 0; font-size: 22px; color: #ffffff; font-weight: bold;">
                            Weather Risk Summary
                        </h1>
                        <div style="margin-top: 8px; font-size: 13px; color: #B3D9FF;">
                            48-Hour Weather Forecast | 未來 48 小時天氣預報
                        </div>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 25px;">
                        
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background: linear-gradient(135deg, #FEE2E2 0%, #FEF2F2 100%); border-left: 6px solid #DC2626; margin-bottom: 20px;">
                            <tr>
                                <td style="padding: 20px;">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="60" valign="top" style="font-size: 36px;">⚠️</td>
                                            <td valign="middle">
                                                <div style="font-size: 24px; font-weight: bold; color: #DC2626; margin-bottom: 5px;">
                                                    {country_name} 共 {len(assessments)} 個港口有氣象風險
                                                </div>
                                                <div style="font-size: 20px; font-weight: bold; color: #DC2626;">
                                                    {len(assessments)} Ports with Weather Risks in {country_name_en}
                                                </div>
                                            </td>
                                            <td align="right" valign="middle" width="220">
                                                <table border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px;">
                                                    <tr>
                                                        <td align="center" style="padding: 8px 10px;">
                                                            <div style="font-size: 24px; font-weight: bold; color: #DC2626;">{len(danger_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🔴 DANGER</div>
                                                        </td>
                                                        <td align="center" style="padding: 8px 10px; border-left: 1px solid #E5E7EB;">
                                                            <div style="font-size: 24px; font-weight: bold; color: #F59E0B;">{len(warning_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🟠 WARNING</div>
                                                        </td>
                                                        <td align="center" style="padding: 8px 10px; border-left: 1px solid #E5E7EB;">
                                                            <div style="font-size: 24px; font-weight: bold; color: #0EA5E9;">{len(caution_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🟡 CAUTION</div>
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
                        
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 2px solid #004B97; margin-bottom: 20px;">
                            <tr>
                                <td style="background-color: #004B97; padding: 12px; color: #ffffff; font-weight: bold; font-size: 16px;">
                                    📋 風險港口列表 Risk Ports List
                                </td>
                            </tr>
        """
        
        # 列出所有風險港口
        for assessment in sorted(assessments, key=lambda x: x.risk_level, reverse=True):
            risk_emoji = {3: "🔴", 2: "🟠", 1: "🟡"}.get(assessment.risk_level, "⚪")
            risk_label = {3: "DANGER", 2: "WARNING", 1: "CAUTION"}.get(assessment.risk_level, "INFO")
            risk_color = {3: "#DC2626", 2: "#F59E0B", 1: "#0EA5E9"}.get(assessment.risk_level, "#6B7280")
            risk_bg = {3: "#FEF2F2", 2: "#FFFBEB", 1: "#F0F9FF"}.get(assessment.risk_level, "#F9FAFB")
            
            html += f"""
                            <tr>
                                <td style="padding: 15px; border-bottom: 1px solid #E5E7EB; background-color: {risk_bg};">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="200" valign="top">
                                                <div style="font-size: 18px; font-weight: bold; color: {risk_color};">
                                                    {risk_emoji} {assessment.port_code} - {risk_label}
                                                </div>
                                                <div style="font-size: 14px; color: #666; margin-top: 3px;">
                                                    {assessment.port_name}
                                                </div>
                                            </td>
                                            <td style="font-size: 13px; color: #374151;">
                                                <div style="margin-bottom: 3px;">💨 風速: <strong>{assessment.max_wind_kts:.0f} kts</strong> (BF{assessment.max_wind_bft})</div>
                                                <div style="margin-bottom: 3px;">💨 陣風: <strong>{assessment.max_gust_kts:.0f} kts</strong> (BF{assessment.max_gust_bft})</div>
                                                <div>🌊 浪高: <strong>{assessment.max_wave:.1f} m</strong></div>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
            """
        
        html += f"""
                        </table>

                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #FFFBEB; border-left: 4px solid #F59E0B; margin-top: 20px;">
                            <tr>
                                <td style="padding: 15px; font-size: 13px; color: #78350F; line-height: 1.7;">
                                    <strong>⚠️ Action Required:</strong><br>
                                    • Please review weather conditions for all listed ports<br>
                                    • Coordinate with local agents for latest updates<br>
                                    • Prepare necessary safety measures<br>
                                    • Monitor weather updates regularly
                                </td>
                            </tr>
                        </table>

                    </td>
                </tr>

                <tr>
                    <td style="background-color: #F8F9FA; padding: 20px; text-align: center; color: #9CA3AF; font-size: 12px; border-top: 1px solid #E5E7EB;">
                        <p style="margin: 0 0 6px 0; font-size: 13px; color: #6B7280;">
                            <strong>Wan Hai Lines Ltd. | 萬海航運股份有限公司</strong>
                        </p>
                        <p style="margin: 0; font-size: 11px; color: #D1D5DB;">
                            Marine Technology Division | Automated Weather Monitoring System
                        </p>
                        <p style="margin: 6px 0 0 0; font-size: 11px; color: #D1D5DB;">
                            📅 {now_str_TPE}
                        </p>
                    </td>
                </tr>
            </table>
            </center>
        </body>
        </html>
        """
        
        return html

# ================= 圖表生成器 =================

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
        """繪製風速趨勢圖，回傳 Base64 字串"""
        if not assessment.raw_records:
            return None
            
        try:
            df = self._prepare_dataframe(assessment.raw_records)
            if df.empty:
                return None
            
            plt.style.use('seaborn-v0_8-darkgrid')
            fig, ax = plt.subplots(figsize=(18, 8))
            
            ax.plot(df['time'], df['wind_speed'], color='#2563EB', 
                label='Wind Speed (kts)', linewidth=3.5, marker='o', markersize=6, zorder=3)
            ax.plot(df['time'], df['wind_gust'], color='#DC2626', 
                linestyle='--', label='Gust (kts)', linewidth=2.8, marker='s', markersize=5, zorder=3)
            
            ax.fill_between(df['time'], df['wind_speed'], alpha=0.15, color='#2563EB', zorder=1)
            
            ax.axhline(RISK_THRESHOLDS['wind_danger'], color="#DC2626", 
                    linestyle=':', linewidth=2.5, label=f'Danger ({RISK_THRESHOLDS["wind_danger"]} kts)', zorder=2)   
            ax.axhline(RISK_THRESHOLDS['wind_warning'], color="#F59E0B", 
                    linestyle='--', linewidth=2.5, label=f'Warning ({RISK_THRESHOLDS["wind_warning"]} kts)', zorder=2)        
            ax.axhline(RISK_THRESHOLDS['wind_caution'], color="#FCD34D", 
                    linestyle=':', linewidth=2.2, label=f'Caution ({RISK_THRESHOLDS["wind_caution"]} kts)', zorder=2)
            
            ax.set_title(f"{assessment.port_name} ({assessment.port_code}) - Wind Speed & Gust Trend (48 Hrs)", 
                        fontsize=20, fontweight='bold', pad=25, color='#1F2937')
            ax.set_ylabel('Speed (knots)', fontsize=16, fontweight='600', color='#374151')
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
            
            filepath = os.path.join(self.output_dir, f"wind_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
            
            base64_str = self._fig_to_base64(fig, dpi=150)
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"      ❌ 繪製風速圖失敗 {port_code}: {e}")
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
            fig, ax = plt.subplots(figsize=(18, 8))
            
            ax.plot(df['time'], df['wave_height'], color='#059669', 
                   label='Sig. Wave Height (m)', linewidth=3.5, marker='o', markersize=6, zorder=3)
            ax.fill_between(df['time'], df['wave_height'], alpha=0.15, color='#059669', zorder=1)
            
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
            
            filepath = os.path.join(self.output_dir, f"wave_{port_code}.png")
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
            
            base64_str = self._fig_to_base64(fig, dpi=150)
            plt.close(fig)
            return base64_str
            
        except Exception as e:
            print(f"   ❌ 繪製浪高圖失敗 {port_code}: {e}")
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

        if record.wind_speed_kts >= RISK_THRESHOLDS['wind_danger']:
            risks.append(f"⛔ 風速危險: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_warning']:
            risks.append(f"⚠️ 風速警告: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
            risks.append(f"⚡ 風速注意: {record.wind_speed_kts:.1f} kts")
            risk_level = max(risk_level, 1)

        if record.wind_gust_kts >= RISK_THRESHOLDS['gust_danger']:
            risks.append(f"⛔ 陣風危險: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 3)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_warning']:
            risks.append(f"⚠️ 陣風警告: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 2)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
            risks.append(f"⚡ 陣風注意: {record.wind_gust_kts:.1f} kts")
            risk_level = max(risk_level, 1)

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
                max_gust_kts=max_wind_record.wind_gust_kts,
                max_gust_bft=max_wind_record.wind_gust_bft,
                max_wave=max_wave_record.wave_height,
                
                max_wind_time_utc=f"{max_wind_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_gust_time_utc=f"{max_gust_record.time.strftime('%m/%d %H:%M')} (UTC)",
                max_wave_time_utc=f"{max_wave_record.time.strftime('%m/%d %H:%M')} (UTC)",
                
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
                print(f"❌ Teams 通知發送失敗: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 發送 Teams 通知時發生錯誤: {e}")
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
                    {"title": "🔴 危險", "value": str(len(danger_ports))},
                    {"title": "🟠 警告", "value": str(len(warning_ports))},
                    {"title": "🟡 注意", "value": str(len(caution_ports))}
                ],
                "spacing": "Medium"
            }
        ]
        
        # 加入各港口詳細資訊
        for assessment in sorted(risk_assessments, key=lambda x: x.risk_level, reverse=True):
            risk_emoji = {3: "🔴", 2: "🟠", 1: "🟡"}.get(assessment.risk_level, "⚪")
            risk_color = {3: "Attention", 2: "Warning", 1: "Accent"}.get(assessment.risk_level, "Default")
            
            body.append({
                "type": "Container",
                "style": "emphasis",
                "items": [
                    {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "width": "auto",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": risk_emoji,
                                        "size": "Large"
                                    }
                                ]
                            },
                            {
                                "type": "Column",
                                "width": "stretch",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": f"{assessment.port_code} - {assessment.port_name}",
                                        "weight": "Bolder",
                                        "color": risk_color
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": f"風速: {assessment.max_wind_kts:.0f} kts (BF{assessment.max_wind_bft}) | 陣風: {assessment.max_gust_kts:.0f} kts | 浪高: {assessment.max_wave:.1f} m",
                                        "size": "Small",
                                        "isSubtle": True,
                                        "wrap": True
                                    }
                                ]
                            }
                        ]
                    }
                ],
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

# ================= 主服務 =================

class WeatherMonitorService:
    """氣象監控主服務"""
    
    def __init__(self):
        self.crawler = PortWeatherCrawler(AEDYN_USERNAME, AEDYN_PASSWORD)
        self.analyzer = WeatherRiskAnalyzer()
        self.chart_gen = ChartGenerator()
        self.notifier = TeamsNotifier(TEAMS_WEBHOOK_URL)
        self.agent_manager = PortAgentManager()
        self.pa_trigger = PowerAutomateEmailTrigger(self.agent_manager)
    
    def _analyze_all_ports(self) -> List[RiskAssessment]:
        """分析所有港口的風險"""
        risk_assessments = []
        
        for port_code, port_info in self.crawler.ports_data.items():
            content = self.crawler.db.get_latest_content(port_code)
            if not content:
                continue
            
            issued_time = self.crawler.db.get_latest_issued_time(port_code)
            assessment = self.analyzer.analyze_port_risk(
                port_code, port_info, content, issued_time
            )
            
            if assessment:
                risk_assessments.append(assessment)
                risk_label = self.analyzer.get_risk_label(assessment.risk_level)
                print(f"   [{len(risk_assessments)}/{len(self.crawler.ports_data)}] ⚠️ {port_code}: {risk_label}")
        
        return risk_assessments
    
    def _generate_charts(self, risk_assessments: List[RiskAssessment]):
        """為風險港口生成圖表"""
        if not risk_assessments:
            print("   ℹ️ 沒有風險港口需要生成圖表")
            return
        
        print(f"   📊 準備為 {len(risk_assessments)} 個港口生成圖表...")
        
        for i, assessment in enumerate(risk_assessments, 1):
            print(f"   [{i}/{len(risk_assessments)}] 正在處理 {assessment.port_code}...")
            
            wind_b64 = self.chart_gen.generate_wind_chart(assessment, assessment.port_code)
            if wind_b64:
                assessment.chart_base64_list.append(wind_b64)
                print(f"      ✅ 風速圖已生成")
            
            wave_b64 = self.chart_gen.generate_wave_chart(assessment, assessment.port_code)
            if wave_b64:
                assessment.chart_base64_list.append(wave_b64)
                print(f"      ✅ 浪高圖已生成")
        
        success_count = sum(1 for a in risk_assessments if a.chart_base64_list)
        print(f"   ✅ 圖表生成完成：{success_count}/{len(risk_assessments)} 個港口成功")
    
    def _generate_data_report(self, download_stats: Dict, 
                             risk_assessments: List[RiskAssessment],
                             teams_sent: bool) -> Dict[str, Any]:
        """生成數據報告"""
        return {
            'execution_time': datetime.now().isoformat(),
            'download_stats': download_stats,
            'risk_summary': {
                'total_risk_ports': len(risk_assessments),
                'danger_count': len([a for a in risk_assessments if a.risk_level == 3]),
                'warning_count': len([a for a in risk_assessments if a.risk_level == 2]),
                'caution_count': len([a for a in risk_assessments if a.risk_level == 1])
            },
            'risk_ports': [a.to_dict() for a in risk_assessments],
            'teams_notification_sent': teams_sent
        }
    
    def _generate_html_report(self, risk_assessments: List[RiskAssessment]) -> str:
        """生成船隊 HTML 報告（包含所有風險港口）"""
        
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"
        
        try:
            from zoneinfo import ZoneInfo
            taipei_tz = ZoneInfo('Asia/Taipei')
        except ImportError:
            taipei_tz = timezone(timedelta(hours=8))
        
        utc_now = datetime.now(timezone.utc)
        tpe_now = utc_now.astimezone(taipei_tz)
        now_str_TPE = f"{tpe_now.strftime('%Y-%m-%d %H:%M')} (TPE)"
        
        danger_ports = [a for a in risk_assessments if a.risk_level == 3]
        warning_ports = [a for a in risk_assessments if a.risk_level == 2]
        caution_ports = [a for a in risk_assessments if a.risk_level == 1]
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="margin: 0; padding: 0; background-color: #F0F4F8; {font_style}">
            <center>
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 900px; margin: 20px auto; background-color: #ffffff;">
                
                <tr>
                    <td style="background-color: #004B97; padding: 20px;">
                        <h1 style="margin: 0; font-size: 24px; color: #ffffff; font-weight: bold;">
                            ⚠️ WHL 港口氣象風險報告
                        </h1>
                        <h1 style="margin: 5px 0 0 0; font-size: 24px; color: #ffffff; font-weight: bold;">
                            Port Weather Risk Report
                        </h1>
                        <div style="margin-top: 8px; font-size: 13px; color: #B3D9FF;">
                            48-Hour Weather Forecast | 未來 48 小時天氣預報
                        </div>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 25px;">
                        
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background: linear-gradient(135deg, #FEE2E2 0%, #FEF2F2 100%); border-left: 6px solid #DC2626; margin-bottom: 20px;">
                            <tr>
                                <td style="padding: 20px;">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="60" valign="top" style="font-size: 36px;">⚠️</td>
                                            <td valign="middle">
                                                <div style="font-size: 26px; font-weight: bold; color: #DC2626; margin-bottom: 5px;">
                                                    共 {len(risk_assessments)} 個港口有氣象風險
                                                </div>
                                                <div style="font-size: 22px; font-weight: bold; color: #DC2626;">
                                                    {len(risk_assessments)} Ports with Weather Risks
                                                </div>
                                            </td>
                                            <td align="right" valign="middle" width="220">
                                                <table border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px;">
                                                    <tr>
                                                        <td align="center" style="padding: 8px 10px;">
                                                            <div style="font-size: 26px; font-weight: bold; color: #DC2626;">{len(danger_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🔴 DANGER</div>
                                                        </td>
                                                        <td align="center" style="padding: 8px 10px; border-left: 1px solid #E5E7EB;">
                                                            <div style="font-size: 26px; font-weight: bold; color: #F59E0B;">{len(warning_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🟠 WARNING</div>
                                                        </td>
                                                        <td align="center" style="padding: 8px 10px; border-left: 1px solid #E5E7EB;">
                                                            <div style="font-size: 26px; font-weight: bold; color: #0EA5E9;">{len(caution_ports)}</div>
                                                            <div style="font-size: 12px; color: #666;">🟡 CAUTION</div>
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
        """
        
        # 按風險等級分組顯示
        for level, level_name, level_emoji, level_color in [
            (3, "DANGER 危險", "🔴", "#DC2626"),
            (2, "WARNING 警告", "🟠", "#F59E0B"),
            (1, "CAUTION 注意", "🟡", "#0EA5E9")
        ]:
            level_ports = [a for a in risk_assessments if a.risk_level == level]
            if not level_ports:
                continue
            
            html += f"""
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border: 2px solid {level_color}; margin-bottom: 20px;">
                            <tr>
                                <td style="background-color: {level_color}; padding: 12px; color: #ffffff; font-weight: bold; font-size: 16px;">
                                    {level_emoji} {level_name} ({len(level_ports)} 個港口)
                                </td>
                            </tr>
            """
            
            for assessment in level_ports:
                html += f"""
                            <tr>
                                <td style="padding: 15px; border-bottom: 1px solid #E5E7EB;">
                                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                        <tr>
                                            <td width="200" valign="top">
                                                <div style="font-size: 18px; font-weight: bold; color: {level_color};">
                                                    {assessment.port_code}
                                                </div>
                                                <div style="font-size: 14px; color: #666; margin-top: 3px;">
                                                    {assessment.port_name}
                                                </div>
                                                <div style="font-size: 12px; color: #999; margin-top: 2px;">
                                                    {assessment.country}
                                                </div>
                                            </td>
                                            <td style="font-size: 13px; color: #374151;">
                                                <div style="margin-bottom: 3px;">💨 風速: <strong>{assessment.max_wind_kts:.0f} kts</strong> (BF{assessment.max_wind_bft})</div>
                                                <div style="margin-bottom: 3px;">💨 陣風: <strong>{assessment.max_gust_kts:.0f} kts</strong> (BF{assessment.max_gust_bft})</div>
                                                <div>🌊 浪高: <strong>{assessment.max_wave:.1f} m</strong></div>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                """
            
            html += """
                        </table>
            """
        
        html += f"""
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #FFFBEB; border-left: 4px solid #F59E0B; margin-top: 20px;">
                            <tr>
                                <td style="padding: 15px; font-size: 13px; color: #78350F; line-height: 1.7;">
                                    <strong>⚠️ Action Required:</strong><br>
                                    • Please review all risk ports and coordinate with local agents<br>
                                    • Monitor weather updates regularly<br>
                                    • Prepare necessary safety measures<br>
                                    • Individual port notifications have been sent to respective agents
                                </td>
                            </tr>
                        </table>

                    </td>
                </tr>

                <tr>
                    <td style="background-color: #F8F9FA; padding: 20px; text-align: center; color: #9CA3AF; font-size: 12px; border-top: 1px solid #E5E7EB;">
                        <p style="margin: 0 0 6px 0; font-size: 13px; color: #6B7280;">
                            <strong>Wan Hai Lines Ltd. | 萬海航運股份有限公司</strong>
                        </p>
                        <p style="margin: 0; font-size: 11px; color: #D1D5DB;">
                            Marine Technology Division | Automated Weather Monitoring System
                        </p>
                        <p style="margin: 6px 0 0 0; font-size: 11px; color: #D1D5DB;">
                            📅 {now_str_TPE}
                        </p>
                    </td>
                </tr>
            </table>
            </center>
        </body>
        </html>
        """
        
        return html
    
    def run_daily_monitoring(self) -> Dict[str, Any]:
        """執行每日監控"""
        print("=" * 80)
        print(f"🚀 開始執行每日氣象監控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        # 1. 下載資料
        print("\n📡 步驟 1: 下載所有港口氣象資料...")
        download_stats = self.crawler.fetch_all_ports()
        
        # 2. 分析風險
        print(f"\n🔍 步驟 2: 分析港口風險...")
        risk_assessments = self._analyze_all_ports()
        
        # 3. 生成圖表
        print(f"\n📈 步驟 3: 生成氣象趨勢圖...")
        self._generate_charts(risk_assessments)
        
        # 4. Teams 通知
        teams_sent = False
        if self.notifier.webhook_url:
            print("\n📢 步驟 4: 發送 Teams 通知...")
            teams_sent = self.notifier.send_risk_alert(risk_assessments)
        else:
            print("\n⚠️ 步驟 4: 跳過 Teams 通知 (未設定 Webhook)")
        
        # 5. 生成報告
        print("\n📊 步驟 5: 生成數據報告...")
        report_data = self._generate_data_report(download_stats, risk_assessments, teams_sent)
        
        # 6. 發送船隊報告
        print("\n📧 步驟 6: 發送船隊報告觸發郵件...")
        print(f"   - 包含所有 {len(risk_assessments)} 個風險港口")
        report_html = self._generate_html_report(risk_assessments)
        fleet_email_sent = self.pa_trigger.send_fleet_report_trigger(
            report_data, report_html, risk_assessments
        )
        
        # 7. 發送港口 + 國家通知
        print("\n📧 步驟 7: 發送港口與國家通知觸發郵件...")
        notification_results = self.pa_trigger.send_all_notifications(risk_assessments)
        
        report_data['fleet_email_sent'] = fleet_email_sent
        report_data['teams_sent'] = teams_sent
        report_data['notification_results'] = notification_results
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print(f"   - 總風險港口: {len(risk_assessments)}")
        print(f"   - Teams 通知: {'✅' if teams_sent else '❌'}")
        print(f"   - 船隊報告: {'✅' if fleet_email_sent else '❌'}")
        print(f"   - 單一港口通知: {sum(1 for v in notification_results['port_notifications'].values() if v)}/{len(notification_results['port_notifications'])} 成功")
        print(f"   - 國家摘要通知: {sum(1 for v in notification_results['country_summaries'].values() if v)}/{len(notification_results['country_summaries'])} 成功")
        print("=" * 80)
        
        return report_data

# ================= 主程式入口 =================

def main():
    try:
        service = WeatherMonitorService()
        result = service.run_daily_monitoring()
        
        # 儲存報告到檔案
        report_file = f"weather_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n📄 報告已儲存: {report_file}")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 執行過程中發生錯誤: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()