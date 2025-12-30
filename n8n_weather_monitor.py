# n8n_weather_monitor.py
"""
N8N 自動化氣象監控腳本（基於 Streamlit App 架構）
用途：每天自動抓取港口天氣，分析高風險港口，並發送到 Teams
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import traceback
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

# 導入自定義模組
from wni_crawler import PortWeatherCrawler, WeatherDatabase
from weather_parser import WeatherParser, WeatherRecord

# ================= 設定區 =================

# 1. WNI 氣象網站爬蟲帳密 (必要，從 GitHub Secrets 讀取)
AEDYN_USERNAME = os.getenv('AEDYN_USERNAME', 'harry_chung@wanhai.com')
AEDYN_PASSWORD = os.getenv('AEDYN_PASSWORD', 'wanhai888')

# 2. Gmail 接力發信用 (必要，從 GitHub Secrets 讀取) 
MAIL_USER = os.getenv('MAIL_USER')         # 你的 Gmail 帳號
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD') # 你的 Gmail 應用程式密碼

# 3. 接力信件的目標與暗號
TARGET_EMAIL = "harry_chung@wanhai.com"
TRIGGER_SUBJECT = "GITHUB_TRIGGER_WEATHER_REPORT"

# 4. Teams Webhook (選填)
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL', 'https://default2b20eccf1c1e43ce93400edfe3a226.6f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/65ec3ae244bf4489b02b7bb6a52b42f5/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=YBZsB6XYwTDMighYOKnQqsIf4dVAUYTKyVTtWhhUQfY')

# 5. 檔案路徑
EXCEL_FILE_PATH = os.getenv('EXCEL_FILE_PATH', 'WHL_all_ports_list.xlsx')
DB_FILE_PATH = os.getenv('DB_FILE_PATH', 'WNI_port_weather.db')

# 風險閾值（與 Streamlit App 一致）
RISK_THRESHOLDS = {
    'wind_caution': 25,  # bf 5
    'wind_warning': 30,  # bf 6
    'wind_danger': 40,   # bf 8
    'gust_caution': 35,  # bf 8
    'gust_warning': 40,  # bf 9
    'gust_danger': 50,   # bf 10
    'wave_caution': 2.0,
    'wave_warning': 2.5,
    'wave_danger': 4.0,
}


@dataclass
class RiskAssessment:
    """風險評估結果"""
    port_code: str
    port_name: str
    country: str
    risk_level: int  # 0=Safe, 1=Caution, 2=Warning, 3=Danger
    risk_factors: List[str]
    max_wind_kts: float
    max_wind_bft: int
    max_gust_kts: float
    max_gust_bft: int
    max_wave: float
    max_wind_time: str
    max_gust_time: str
    risk_periods: List[Dict[str, Any]]
    issued_time: str
    latitude: float
    longitude: float
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典"""
        return asdict(self)


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
        """分析單筆記錄的風險"""
        risks = []
        risk_level = 0

        # 風速檢查
        if record.wind_speed_kts >= RISK_THRESHOLDS['wind_danger']:
            risks.append(f"⛔ 風速危險: {record.wind_speed_kts:.1f} kts / (Bf {record.wind_speed_bft})")
            risk_level = max(risk_level, 3)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_warning']:
            risks.append(f"⚠️ 風速警告: {record.wind_speed_kts:.1f} kts / (Bf {record.wind_speed_bft})")
            risk_level = max(risk_level, 2)
        elif record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
            risks.append(f"⚡ 風速注意: {record.wind_speed_kts:.1f} kts / (Bf {record.wind_speed_bft})")
            risk_level = max(risk_level, 1)

        # 陣風檢查
        if record.wind_gust_kts >= RISK_THRESHOLDS['gust_danger']:
            risks.append(f"⛔ 陣風危險: {record.wind_gust_kts:.1f} kts / (Bf {record.wind_gust_bft})")
            risk_level = max(risk_level, 3)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_warning']:
            risks.append(f"⚠️ 陣風警告: {record.wind_gust_kts:.1f} kts / (Bf {record.wind_gust_bft})")
            risk_level = max(risk_level, 2)
        elif record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
            risks.append(f"⚡ 陣風注意: {record.wind_gust_kts:.1f} kts / (Bf {record.wind_gust_bft})")
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
            'risks': risks,
            'time': record.time,
            'wind_speed_kts': record.wind_speed_kts,
            'wind_speed_bft': record.wind_speed_bft,
            'wind_gust_kts': record.wind_gust_kts,
            'wind_gust_bft': record.wind_gust_bft,
            'wave_height': record.wave_height,
            'wind_direction': record.wind_direction,
            'wave_direction': record.wave_direction,
        }

    @classmethod
    def get_risk_label(cls, risk_level: int) -> str:
        """取得風險等級標籤"""
        return {
            0: "港口風險等級:安全 Safe",
            1: "港口風險等級:注意 Caution",
            2: "港口風險等級:警告 Warning",
            3: "港口風險等級:危險 Danger"
        }.get(risk_level, "未知 Unknown")

    @classmethod
    def analyze_port_risk(cls, port_code: str, port_info: Dict[str, Any],
                         content: str, issued_time: str) -> Optional[RiskAssessment]:
        """分析單一港口的風險"""
        try:
            parser = WeatherParser()
            port_name, records, warnings = parser.parse_content(content)
            
            if not records:
                return None
            
            all_analyzed = []
            risk_periods = []
            max_level = 0
            
            max_wind_record = max(records, key=lambda r: r.wind_speed_kts)
            max_gust_record = max(records, key=lambda r: r.wind_gust_kts)
            
            for record in records:
                analyzed = cls.analyze_record(record)
                all_analyzed.append(analyzed)
                
                if analyzed['risks']:
                    risk_periods.append({
                        'time': record.time.strftime('%Y-%m-%d %H:%M'),
                        'wind_speed_kts': record.wind_speed_kts,
                        'wind_speed_bft': record.wind_speed_bft,
                        'wind_gust_kts': record.wind_gust_kts,
                        'wind_gust_bft': record.wind_gust_bft,
                        'wave_height': record.wave_height,
                        'wind_direction': record.wind_direction,
                        'wave_direction': record.wave_direction,
                        'risks': analyzed['risks'],
                        'risk_level': analyzed['risk_level']
                    })
                    max_level = max(max_level, analyzed['risk_level'])
            
            if max_level == 0:
                return None
            
            risk_factors = []
            if max_wind_record.wind_speed_kts >= RISK_THRESHOLDS['wind_caution']:
                risk_factors.append(
                    f"風速 {max_wind_record.wind_speed_kts:.1f} kts (Bf {max_wind_record.wind_speed_bft})"
                )
            if max_gust_record.wind_gust_kts >= RISK_THRESHOLDS['gust_caution']:
                risk_factors.append(
                    f"陣風 {max_gust_record.wind_gust_kts:.1f} kts (Bf {max_gust_record.wind_gust_bft})"
                )
            
            max_wave = max(r.wave_height for r in records)
            if max_wave >= RISK_THRESHOLDS['wave_caution']:
                risk_factors.append(f"浪高 {max_wave:.1f} m")
            
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
                max_wave=max_wave,
                max_wind_time=max_wind_record.time.strftime('%Y-%m-%d %H:%M'),
                max_gust_time=max_gust_record.time.strftime('%Y-%m-%d %H:%M'),
                risk_periods=risk_periods,
                issued_time=issued_time,
                latitude=port_info.get('latitude', 0.0),
                longitude=port_info.get('longitude', 0.0)
            )
            
        except Exception as e:
            print(f"❌ 分析港口 {port_code} 時發生錯誤: {e}")
            traceback.print_exc()
            return None


class TeamsNotifier:
    """Teams 通知發送器"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_risk_alert(self, risk_assessments: List[RiskAssessment]) -> bool:
        """發送風險警報到 Teams"""
        if not self.webhook_url:
            print("⚠️ 未設定 Teams Webhook URL")
            return False
        
        if not risk_assessments:
            print("ℹ️ 沒有需要通知的高風險港口")
            return self._send_all_safe_notification()
        
        try:
            card = self._create_adaptive_card(risk_assessments)
            
            # ✅ 修正：移除 verify=False，恢復安全連線
            response = requests.post(
                self.webhook_url,
                json=card,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"✅ Teams 通知發送成功 ({len(risk_assessments)} 個高風險港口)")
                return True
            else:
                print(f"❌ Teams 通知發送失敗 (HTTP {response.status_code})")
                print(f"   回應: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ 發送 Teams 通知時發生錯誤: {e}")
            traceback.print_exc()
            return False
    
    def _send_all_safe_notification(self) -> bool:
        """發送「全部港口安全」的通知"""
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
                                "type": "Container",
                                "style": "good",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": "✅ WHL 港口氣象監控系統 \n\n present by MariTech-FRM",
                                        "weight": "Bolder",
                                        "size": "Medium",
                                        "color": "Good",
                                        "wrap": True
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": f"📅 最後更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M')} (UTC)",
                                        "isSubtle": True,
                                        "spacing": "None"
                                    }
                                ]
                            },
                            {
                                "type": "Container",
                                "spacing": "Medium",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": "🟢 所有監控港口均處於安全狀態",
                                        "wrap": True,
                                        "weight": "Bolder",
                                        "size": "Medium"
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": "未來 48 小時內，所有港口的風速、陣風和浪高均在安全範圍內。",
                                        "wrap": True,
                                        "spacing": "Small",
                                        "isSubtle": True
                                    }
                                ]
                            }
                        ]
                    }
                }]
            }
            
            response = requests.post(
                self.webhook_url,
                json=card,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            return response.status_code == 200
            
        except Exception as e:
            print(f"❌ 發送安全通知時發生錯誤: {e}")
            return False
    
    def _create_adaptive_card(self, risk_assessments: List[RiskAssessment]) -> Dict[str, Any]:
        """建立 Adaptive Card 格式的訊息"""
        
        danger_ports = [r for r in risk_assessments if r.risk_level == 3]
        warning_ports = [r for r in risk_assessments if r.risk_level == 2]
        caution_ports = [r for r in risk_assessments if r.risk_level == 1]
        
        danger_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
        warning_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
        caution_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
        
        body = [
            {
                "type": "Container",
                "style": "attention",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "⚠️ WHL 港口氣象監控系統",
                        "weight": "Bolder",
                        "size": "ExtraLarge",
                        "wrap": True
                    },
                    {
                        "type": "TextBlock",
                        "text": "present by MariTech-FRM",
                        "size": "Small",
                        "isSubtle": True,
                        "spacing": "None"
                    },
                    {
                        "type": "TextBlock",
                        "text": f"📅 最後更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M')} (UTC)",
                        "isSubtle": True,
                        "spacing": "Small",
                        "size": "Small"
                    }
                ]
            }
        ]
        
        summary_items = [
            {
                "type": "TextBlock",
                "text": "📊 未來 48 Hrs 港區風險統計",
                "weight": "Bolder",
                "size": "Medium",
                "horizontalAlignment": "Center",
                "spacing": "Medium"
            }
        ]

        columns = []
        if danger_ports:
            columns.append({
                "type": "Column",
                "width": "stretch",
                "items": [{
                    "type": "TextBlock",
                    "text": f"🔴 危險等級: {len(danger_ports)}個",
                    "weight": "Bolder",
                    "color": "Attention",
                    "size": "Medium",
                    "horizontalAlignment": "Center"
                }]
            })

        if warning_ports:
            columns.append({
                "type": "Column",
                "width": "stretch",
                "items": [{
                    "type": "TextBlock",
                    "text": f"🟠 警告港口: {len(warning_ports)}個",
                    "weight": "Bolder",
                    "color": "Warning",
                    "size": "Medium",
                    "horizontalAlignment": "Center"
                }]
            })

        if caution_ports:
            columns.append({
                "type": "Column",
                "width": "stretch",
                "items": [{
                    "type": "TextBlock",
                    "text": f"🟡 注意港口: {len(caution_ports)}個",
                    "weight": "Bolder",
                    "color": "Accent",
                    "size": "Medium",
                    "horizontalAlignment": "Center"
                }]
            })

        if columns:
            summary_items.append({
                "type": "ColumnSet",
                "columns": columns,
                "spacing": "Small"
            })
        else:
            summary_items.append({
                "type": "TextBlock",
                "text": "🟢 全線安全無風險",
                "horizontalAlignment": "Center",
                "color": "Good",
                "weight": "Bolder"
            })
            
        body.extend(summary_items)

        if danger_ports:
            body.append({
                "type": "Container",
                "style": "attention",
                "spacing": "Large",
                "separator": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "🔴(Danger)危險等級港口",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Attention",
                        "horizontalAlignment": "Center",
                        "wrap": True
                    },
                    {
                        "type": "TextBlock",
                        "text": "(條件: 風速 > 40 kts / 陣風 > 50 kts / 浪高 > 4.0 m)",
                        "size": "Small",
                        "isSubtle": True,
                        "horizontalAlignment": "Center",
                        "spacing": "None",
                        "wrap": True
                    }
                ]
            })
            
            for port in danger_ports[:20]:
                body.append(self._create_port_container(port, "attention"))
        
        if warning_ports:
            body.append({
                "type": "Container",
                "style": "warning",
                "spacing": "Large",
                "separator": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "🟠(Warning)警告等級港口清單",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Warning",
                        "horizontalAlignment": "Center",
                        "wrap": True
                    },
                    {
                        "type": "TextBlock",
                        "text": "(條件: 風速 > 30 kts /  陣風 > 40 kts / 浪高 > 2.5 m)",
                        "size": "Small",
                        "isSubtle": True,
                        "horizontalAlignment": "Center",
                        "spacing": "None",
                        "wrap": True
                    }
                ]
            })
            
            for port in warning_ports[:20]:
                body.append(self._create_port_container(port, "warning"))
        
        if caution_ports:
            body.append({
                "type": "Container",
                "style": "accent",
                "spacing": "Medium",
                "separator": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "🟡(Caution)注意等級港口清單",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Accent",
                        "horizontalAlignment": "Center",
                        "wrap": True
                    },
                    {
                        "type": "TextBlock",
                        "text": "(條件: 風速 > 25 kts /  陣風 > 35 kts / 浪高 > 2.0 m)",
                        "size": "Small",
                        "isSubtle": True,
                        "horizontalAlignment": "Center",
                        "spacing": "None",
                        "wrap": True
                    }
                ]
            })
            
            for port in caution_ports[:20]:
                body.append(self._create_port_container(port, "default"))
            
            if len(caution_ports) > 20:
                body.append({
                    "type": "TextBlock",
                    "text": f"... 還有 {len(caution_ports) - 20} 個注意港口",
                    "isSubtle": True,
                    "spacing": "Small",
                    "horizontalAlignment": "Center"
                })
        
        body.append({
            "type": "Container",
            "spacing": "Large",
            "separator": True,
            "items": [
                {
                    "type": "TextBlock",
                    "text": "⚠️ 請船管PIC注意業管船舶安全，並提前做好防範措施",
                    "wrap": True,
                    "color": "Warning",
                    "weight": "Bolder",
                    "horizontalAlignment": "Center"
                }
            ]
        })
        
        card = {
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
        
        return card
    
    def _create_port_container(self, assessment: RiskAssessment, style: str) -> Dict[str, Any]:
        """建立單一港口的資訊容器"""
        risk_emoji = self._get_risk_emoji(assessment.risk_level)
        
        header_section = {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": f"{risk_emoji} {assessment.port_name} ({assessment.port_code})",
                            "weight": "Bolder",
                            "size": "Large",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": f"📍 {assessment.country}",
                            "isSubtle": True,
                            "spacing": "None",
                            "size": "Small",
                            "wrap": True
                        }
                    ]
                }
            ]
        }

        high_risk_count = len([p for p in assessment.risk_periods if p['risk_level'] >= 2])
        period_summary = f"共 {len(assessment.risk_periods)} 個時段"
        if high_risk_count > 0:
            period_summary += f" ({high_risk_count} 個警告+)"

        stats_section = {
            "type": "Container",
            "style": "emphasis",
            "spacing": "Small",
            "items": [
                {
                    "type": "FactSet",
                    "spacing": "Small",
                    "facts": [
                        {"title": "💨 未來48Hrs最大風速", "value": f"**{assessment.max_wind_kts:.0f}** kts (Bf: {assessment.max_wind_bft})"},
                        {"title": "🌬️ 未來48Hrs最大陣風", "value": f"**{assessment.max_gust_kts:.0f}** kts (Bf: {assessment.max_gust_bft})"},
                        {"title": "🌊 未來48Hrs最大浪高", "value": f"**{assessment.max_wave:.1f}** m"},
                        {"title": "⚠️ 風險因素", "value": ", ".join(assessment.risk_factors)},
                        {"title": "🕐 時段統計", "value": period_summary}
                    ]
                }
            ]
        }

        list_section_items = []
        
        if assessment.risk_periods:
            list_section_items.append({
                "type": "TextBlock",
                "text": "📋 主要高風險時段 (Top5)",
                "weight": "Bolder",
                "size": "Small",
                "color": "Accent",
                "spacing": "Medium"
            })

            for period in assessment.risk_periods[:5]:
                try:
                    date_part = period['time'].split(' ')[0]
                    time_part = period['time'].split(' ')[1]
                    month_day = date_part.split('-')[1] + '/' + date_part.split('-')[2]
                    time_str = f"{month_day} {time_part}"
                except:
                    time_str = period['time']

                detail_text = (
                    f"💨風速:{int(period['wind_speed_kts'])}kt(Bf:{period['wind_speed_bft']})  "
                    f"🌬️陣風:{int(period['wind_gust_kts'])}kt(Bf:{period['wind_gust_bft']})  "
                    f"🌊浪高:{period['wave_height']:.1f}m"
                )

                row = {
                    "type": "ColumnSet",
                    "spacing": "Small",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "auto",
                            "items": [{
                                "type": "TextBlock",
                                "text": f"🕒 {time_str}",
                                "weight": "Bolder",
                                "size": "Small",
                                "color": "Attention" if period['risk_level'] >= 2 else "Default"
                            }]
                        },
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [{
                                "type": "TextBlock",
                                "text": detail_text,
                                "size": "Small",
                                "isSubtle": True,
                                "wrap": True
                            }]
                        }
                    ]
                }
                list_section_items.append(row)

        list_container = {
            "type": "Container",
            "spacing": "Small",
            "items": list_section_items
        }

        return {
            "type": "Container",
            "spacing": "Medium",
            "separator": True,
            "items": [
                header_section,
                stats_section,
                list_container
            ]
        }
    
    def _get_risk_emoji(self, risk_level: int) -> str:
        """取得風險等級對應的 emoji"""
        return {
            0: '🟢',
            1: '🟡',
            2: '🟠',
            3: '🔴'
        }.get(risk_level, '⚪')


class GmailRelayNotifier:
    """
    Gmail 接力發信器 (修正版 - Port 587 STARTTLS)
    同時發送 JSON 和 HTML 格式，方便 Power Automate 解析
    """
    def __init__(self):
        self.user = os.getenv('MAIL_USER')
        self.password = os.getenv('MAIL_PASSWORD')
        self.target = "harry_chung@wanhai.com"
        self.subject_trigger = "GITHUB_TRIGGER_WEATHER_REPORT"

    def send_trigger_email(self, report_data: dict, report_html: str) -> bool:
        """
        發送觸發信件（同時包含 JSON 和 HTML）
        
        Args:
            report_data: 報告數據字典（JSON 格式）
            report_html: HTML 格式的報告
        """
        if not self.user or not self.password:
            print("⚠️ 未設定 Gmail 帳密，無法發送信件")
            return False

        # 建立 multipart 郵件（同時包含純文字和 HTML）
        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = self.subject_trigger
        
        # Part 1: 純文字版本（JSON 格式，方便 Power Automate 解析）
        json_text = json.dumps(report_data, ensure_ascii=False, indent=2)
        text_part = MIMEText(json_text, 'plain', 'utf-8')
        
        # Part 2: HTML 版本（美化顯示）
        html_part = MIMEText(report_html, 'html', 'utf-8')
        
        msg.attach(text_part)
        msg.attach(html_part)

        try:
            print(f"📧 正在透過 Gmail (Port 587 STARTTLS) 發送報表給 {self.target}...")
            
            # ✅ 使用 Port 587 + STARTTLS（相容性最好）
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
            print("🔑 正在登入...")
            server.login(self.user, self.password)
            
            print("📨 正在傳送資料...")
            server.sendmail(self.user, self.target, msg.as_string())
            
            server.quit()
            print("✅ 觸發信件發送成功！")
            return True
            
        except smtplib.SMTPAuthenticationError:
            print("❌ Gmail 認證失敗，請檢查帳號密碼是否正確")
            print("💡 提示：請確認已啟用「兩步驟驗證」並使用「應用程式密碼」")
            return False
        except smtplib.SMTPException as e:
            print(f"❌ SMTP 錯誤: {e}")
            return False
        except Exception as e:
            print(f"❌ Gmail 發送失敗: {e}")
            traceback.print_exc()
            return False


class WeatherMonitorService:
    """氣象監控服務（主要執行類別）"""
    
    def __init__(self, username: str, password: str,
                 teams_webhook_url: str = '',
                 excel_path: str = EXCEL_FILE_PATH):
        """初始化監控服務"""
        print("🔧 正在初始化氣象監控服務...")
        
        self.crawler = PortWeatherCrawler(
            username=username,
            password=password,
            excel_path=excel_path,
            auto_login=False
        )
        self.analyzer = WeatherRiskAnalyzer()
        self.notifier = TeamsNotifier(teams_webhook_url) # 負責 Teams (Adaptive Cards)
        self.db = WeatherDatabase()
        self.email_notifier = GmailRelayNotifier()       # 負責 Email (HTML)
        
        print(f"✅ 系統初始化完成，共載入 {len(self.crawler.port_list)} 個港口")
    
    def run_daily_monitoring(self) -> Dict[str, Any]:
        """執行每日監控"""
        print("=" * 80)
        print(f"🚀 開始執行每日氣象監控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        # 步驟 1: 下載所有港口氣象資料
        print("\n📡 步驟 1: 下載所有港口氣象資料...")
        download_stats = self.crawler.fetch_all_ports()
        
        # 步驟 2: 分析所有港口風險
        print("\n🔍 步驟 2: 分析港口風險...")
        risk_assessments = self._analyze_all_ports()
        
        # ==========================================
        # 分流處理：這裡分別處理 Teams 和 Email
        # ==========================================

        # 步驟 3: 發送 Teams 通知 (使用 Adaptive Cards JSON)
        notification_sent = False
        if self.notifier.webhook_url:
            print("\n📢 步驟 3: 發送 Teams 通知 (Adaptive Cards)...")
            # TeamsNotifier 內部會呼叫 _create_adaptive_card 生成 JSON
            notification_sent = self.notifier.send_risk_alert(risk_assessments)
        
        # 步驟 4: 生成基礎數據報告 (JSON Data)
        print("\n📊 步驟 4: 生成數據報告...")
        report_data = self._generate_data_report(download_stats, risk_assessments, notification_sent)
        
        # 步驟 5: 生成 HTML 報告並發送 Email (使用 HTML/CSS)
        print("\n📧 步驟 5: 發送 Email 通知 (HTML)...")
        # 這裡呼叫專門的 HTML 生成器
        report_html = self._generate_html_report(risk_assessments)
        
        try:
            # 發送郵件：同時包含 JSON數據(給機器讀) 和 HTML(給人讀)
            self.email_notifier.send_trigger_email(report_data, report_html)
        except Exception as e:
            print(f"⚠️ 發信過程發生異常: {e}")
            traceback.print_exc()
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print("=" * 80)
        
        return report_data
    
    def _generate_data_report(self, download_stats: Dict[str, int],
                        risk_assessments: List[RiskAssessment],
                        notification_sent: bool) -> Dict[str, Any]:
        """生成純數據報告 (JSON 結構，不含 UI 格式)"""
        
        risk_distribution = {
            'danger': sum(1 for r in risk_assessments if r.risk_level == 3),
            'warning': sum(1 for r in risk_assessments if r.risk_level == 2),
            'caution': sum(1 for r in risk_assessments if r.risk_level == 1),
        }
        
        report = {
            'execution_time': datetime.now().isoformat(),
            'download_stats': download_stats,
            'risk_analysis': {
                'total_risk_ports': len(risk_assessments),
                'risk_distribution': risk_distribution,
                'top_risk_ports': [a.to_dict() for a in sorted(
                        risk_assessments,
                        key=lambda x: (x.risk_level, x.max_wind_kts),
                        reverse=True
                    )[:20]
                ]
            },
            'notification': {
                'sent': notification_sent,
                'recipient': 'Microsoft Teams & Email'
            }
        }
        return report

    def _generate_html_report(self, assessments: List[RiskAssessment]) -> str:
        """生成 HTML 格式的精美報告 (專供 Email 使用)"""
        
        # 定義字型堆疊：微軟正黑體 > Segoe UI > Arial
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', Arial, sans-serif;"
        
        if not assessments:
            return f"""
            <div style="{font_style} color: #2E7D32; padding: 20px; border: 1px solid #4CAF50; background-color: #E8F5E9; border-radius: 5px;">
                <h3 style="margin-top: 0;">🟢 System Status: ALL CLEAR</h3>
                <p>今日所有監控港口均處於安全範圍 (All ports are within safe limits).</p>
            </div>
            """
            
        risk_groups = {3: [], 2: [], 1: []}
        for a in assessments:
            risk_groups[a.risk_level].append(a)

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        
        # Email Header
        html = f"""
        <html>
        <body style="{font_style} color: #333; line-height: 1.5; background-color: #ffffff;">
            <div style="background-color: #004B97; color: white; padding: 20px; border-radius: 6px 6px 0 0;">
                <h2 style="margin: 0; font-size: 22px; font-weight: bold; {font_style}">⛴️ WHL Port Weather Risk Monitor</h2>
                <p style="margin: 8px 0 0 0; font-size: 13px; opacity: 0.9; {font_style}">
                    Present by Marine Technology Division - Fleet Risk Department | Update: {now_str} (UTC+8)
                </p>
            </div>

            <div style="background-color: #f8f9fa; border: 1px solid #e9ecef; border-top: none; padding: 15px; margin-bottom: 25px; border-radius: 0 0 6px 6px;">
                <strong style="font-size: 15px; {font_style}">📊 未來48Hrs內風險港口監控摘要:</strong><br>
                <div style="margin-top: 8px; font-size: 14px; {font_style}">
                    共有 <span style="color: #D9534F; font-weight: bold; font-size: 16px;">{len(assessments)}</span> 個港口有潛在氣象風險。
                    請 <span style="background-color: #fff3cd; padding: 2px 4px; border-radius: 3px;">船管PIC</span> 留意下列港口動態。
                </div>
            </div>
        """

        # 風險等級樣式定義 (Email 用 HTML/CSS)
        styles = {
            3: {'color': '#D9534F', 'bg': '#FEF2F2', 'title': '🔴 POTENTIAL DANGER PORT (條件: 風速 > 40 kts / 陣風 > 50 kts / 浪高 > 4.0 m)', 'border': '#D9534F', 'header_bg': '#FEE2E2'},
            2: {'color': '#F59E0B', 'bg': '#FFFBEB', 'title': '🟠 POTENTIAL WARNING PORT (條件: 風速 > 30 kts / 陣風 > 40 kts / 浪高 > 2.5 m)', 'border': '#F59E0B', 'header_bg': '#FEF3C7'},
            1: {'color': '#0EA5E9', 'bg': '#F0F9FF', 'title': '🟡 POTENTIAL CAUTION PORT (條件: 風速 > 25 kts / 陣風 > 30 kts / 浪高 > 2.0 m)', 'border': '#0EA5E9', 'header_bg': '#E0F2FE'}
        }

        for level in [3, 2, 1]:
            ports = risk_groups[level]
            if not ports:
                continue
            
            style = styles[level]
            
            # 該等級的標題
            html += f"""
            <div style="margin-top: 25px; margin-bottom: 12px;">
                <span style="background-color: {style['color']}; color: white; padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 14px; {font_style}">
                    {style['title']}
                </span>
            </div>
            
            <table style="width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; border: 1px solid #e5e7eb; border-radius: 6px; overflow: hidden;">
                <thead>
                    <tr style="background-color: {style['header_bg']}; color: #4b5563; text-align: left;">
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; width: 25%; {font_style}">港口名稱(Port Name)</th>
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; width: 35%; {font_style}">潛在風險(Potential Crisis)</th>
                        <th style="padding: 12px 15px; border-bottom: 2px solid {style['border']}; {font_style}">高風險時段(High-risk periods) & Time</th>
                    </tr>
                </thead>
                <tbody>
            """
            
            for index, p in enumerate(ports):
                # 表格斑馬紋
                row_bg = "#ffffff" if index % 2 == 0 else "#f9fafb"
                
                # 數值強調樣式
                wind_val_style = "color: #D9534F; font-weight: bold; font-size: 15px;" if p.max_wind_kts >= 30 else "font-weight: bold;"
                wave_val_style = "color: #D9534F; font-weight: bold; font-size: 15px;" if p.max_wave >= 3.0 else "font-weight: bold;"
                
                html += f"""
                <tr style="background-color: {row_bg};">
                    <td style="padding: 12px 15px; border-bottom: 1px solid #e5e7eb; vertical-align: top; {font_style}">
                        <div style="font-size: 16px; font-weight: bold; color: #1f2937;">{p.port_code}</div>
                        <div style="margin-top: 2px; color: #374151;">{p.port_name}</div>
                        <div style="margin-top: 4px; color: #6b7280; font-size: 12px;">📍 {p.country}</div>
                    </td>
                    <td style="padding: 12px 15px; border-bottom: 1px solid #e5e7eb; vertical-align: top; {font_style}">
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
                    <td style="padding: 12px 15px; border-bottom: 1px solid #e5e7eb; vertical-align: top; {font_style}">
                        <div style="margin-bottom: 6px; color: #b91c1c; background-color: #fef2f2; display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 13px;">
                            ⚠️ {', '.join(p.risk_factors)}
                        </div>
                        <div style="color: #4b5563; font-size: 13px; margin-top: 4px;">
                            🕒 Time: <b>{p.max_wind_time}</b>
                        </div>
                    </td>
                </tr>
                """
            
            html += "</tbody></table>"

        # Footer
        html += f"""
            <div style="margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 20px; font-size: 12px; color: #9ca3af; text-align: center; {font_style}">
                <p style="margin: 0;">Wan Hai Lines Ltd. | Marine Technology Division</p>
                <p style="margin: 5px 0 0 0;">Data Source: Weathernews Inc. (WNI) | Automated System</p>
            </div>
        </body>
        </html>
        """
        
        return html
    
    # _analyze_all_ports 方法保持不變
    def _analyze_all_ports(self) -> List[RiskAssessment]:
        # (這裡放原本的代碼，無需更動)
        risk_assessments = []
        total_ports = len(self.crawler.port_list)
        print(f"開始分析 {total_ports} 個港口...")
        for i, port_code in enumerate(self.crawler.port_list, 1):
            try:
                data = self.db.get_latest_content(port_code)
                if not data: continue
                content, issued_time, port_name = data
                port_info = self.crawler.get_port_info(port_code)
                if not port_info: continue
                assessment = self.analyzer.analyze_port_risk(port_code, port_info, content, issued_time)
                if assessment:
                    risk_assessments.append(assessment)
                    risk_label = self.analyzer.get_risk_label(assessment.risk_level)
                    print(f"   [{i}/{total_ports}] ⚠️ {port_code} ({assessment.port_name}): {risk_label}")
                else:
                    print(f"   [{i}/{total_ports}] ✅ {port_code}: 安全")
            except Exception as e:
                print(f"   [{i}/{total_ports}] ❌ {port_code}: 分析錯誤 - {e}")
                continue
        print(f"\n✅ 分析完成，發現 {len(risk_assessments)} 個需要關注的港口")
        return risk_assessments

    def save_report_to_file(self, report: Dict[str, Any], output_dir: str = 'reports') -> str:
        # (保持原樣)
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"weather_monitor_report_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n💾 報告已儲存至: {filepath}")
        return filepath


# ================= 主程式進入點 =================
def main():
    """主程式"""
    print("=" * 80)
    print("🌊 WNI 港口氣象自動監控系統")
    print("=" * 80)
    
    if not AEDYN_USERNAME or not AEDYN_PASSWORD:
        print("❌ 錯誤: 未設定 AEDYN_USERNAME 或 AEDYN_PASSWORD")
        sys.exit(1)
    
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ 警告: 未設定 TEAMS_WEBHOOK_URL，將無法發送 Teams 通知")
    
    try:
        service = WeatherMonitorService(
            username=AEDYN_USERNAME,
            password=AEDYN_PASSWORD,
            teams_webhook_url=TEAMS_WEBHOOK_URL,
            excel_path=EXCEL_FILE_PATH
        )
        
        # 執行每日監控（已包含發送 Email）
        report = service.run_daily_monitoring()
        
        # 儲存報告
        report_file = service.save_report_to_file(report)
        
        # 輸出 JSON 格式的報告（供 N8N 使用）
        print("\n" + "=" * 80)
        print("📤 JSON 輸出 (供 N8N 使用):")
        print("=" * 80)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n❌ 執行過程中發生錯誤: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

