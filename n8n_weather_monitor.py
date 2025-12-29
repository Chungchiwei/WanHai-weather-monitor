# n8n_weather_monitor.py
"""
N8N 自動化氣象監控腳本（基於 Streamlit App 架構）
用途：每天自動抓取港口天氣，分析高風險港口，並發送到 Teams
"""
import os
import sys
import json
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import traceback
import sqlite3

# 導入自定義模組
from wni_crawler import PortWeatherCrawler, WeatherDatabase
from weather_parser import WeatherParser, WeatherRecord

# ================= 設定區 =================
AEDYN_USERNAME = os.getenv('AEDYN_USERNAME', 'harry_chung@wanhai.com')
AEDYN_PASSWORD = os.getenv('AEDYN_PASSWORD', 'wanhai888')
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL', 'https://default2b20eccf1c1e43ce93400edfe3a226.6f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/65ec3ae244bf4489b02b7bb6a52b42f5/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=YBZsB6XYwTDMighYOKnQqsIf4dVAUYTKyVTtWhhUQfY')
EXCEL_FILE_PATH = os.getenv('EXCEL_FILE_PATH', 'WHL_all_ports_list.xlsx')
DB_FILE_PATH = os.getenv('DB_FILE_PATH', 'WNI_port_weather.db')

# 風險閾值（與 Streamlit App 一致）
RISK_THRESHOLDS = {
    'wind_caution': 25, #bf 5
    'wind_warning': 30, #bf 6
    'wind_danger': 40, #bf 8
    'gust_caution': 35, #bf 8
    'gust_warning': 40, #bf 9
    'gust_danger': 50, #bf 10
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
    max_wind_time: str  # 最大風速時段
    max_gust_time: str  # 最大陣風時段
    risk_periods: List[Dict[str, Any]]
    issued_time: str
    latitude: float
    longitude: float
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典"""
        return asdict(self)


class WeatherRiskAnalyzer:
    """氣象風險分析器（與 Streamlit App 一致）"""
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
        """
        分析單一港口的風險
        
        Args:
            port_code: 港口代碼
            port_info: 港口資訊
            content: 氣象內容
            issued_time: 發布時間
            
        Returns:
            RiskAssessment 或 None
        """
        try:
            parser = WeatherParser()
            port_name, records, warnings = parser.parse_content(content)
            
            if not records:
                return None
            
            # 分析所有記錄
            all_analyzed = []
            risk_periods = []
            max_level = 0
            
            # 追蹤最大值及其時間
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
            
            # 如果風險等級為 0（安全），不需要回報
            if max_level == 0:
                return None
            
            # 收集風險因素
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
        """
        發送風險警報到 Teams
        
        Args:
            risk_assessments: 風險評估結果列表
            
        Returns:
            bool: 發送成功返回 True
        """
        if not self.webhook_url:
            print("⚠️ 未設定 Teams Webhook URL")
            return False
        
        if not risk_assessments:
            print("ℹ️ 沒有需要通知的高風險港口")
            # 發送「全部安全」的通知
            return self._send_all_safe_notification()
        
        try:
            # 建立 Adaptive Card 訊息
            card = self._create_adaptive_card(risk_assessments)
            
            # 發送到 Teams
            response = requests.post(
                self.webhook_url,
                json=card,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"✅  ({len(risk_assessments)} 個高風險港口)")
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
                                        "text": "✅ WHL 海技部：港口氣象監控報告 \n\n present by FRM",
                                        "weight": "Bolder",
                                        "size": "Medium",
                                        "color": "Good",
                                        "wrap": True
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} 更新",
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
            """建立 Adaptive Card 格式的訊息（分區顯示 - 美化版）"""
            
            # 1. 依風險等級分組並排序
            danger_ports = [r for r in risk_assessments if r.risk_level == 3]
            warning_ports = [r for r in risk_assessments if r.risk_level == 2]
            caution_ports = [r for r in risk_assessments if r.risk_level == 1]
            
            danger_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
            warning_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
            caution_ports.sort(key=lambda x: x.max_wind_kts, reverse=True)
            
            # 2. 初始化 body 列表，並放入「主標題」
            body = [
                {
                    "type": "Container",
                    "style": "attention", # 深色背景強調標題
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "⚠️ WHL 海技部：港口氣象監控報告",
                            "weight": "Bolder",
                            "size": "ExtraLarge",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": "present by FRM",
                            "size": "Small",
                            "isSubtle": True,
                            "spacing": "None"
                        },
                        {
                            "type": "TextBlock",
                            "text": f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} 更新",
                            "isSubtle": True,
                            "spacing": "Small",
                            "size": "Small"
                        }
                    ]
                }
            ]
            
            # 3. 建立「儀表板摘要 (Summary)」並加入 body
            # --- Summary 邏輯開始 ---
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
            # 危險等級 (紅)
            if danger_ports:
                columns.append({
                    "type": "Column",
                    "width": "stretch",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"🔴 危險: {len(danger_ports)}",
                        "weight": "Bolder",
                        "color": "Attention",
                        "size": "Large",
                        "horizontalAlignment": "Center"
                    }]
                })

            # 警告等級 (橘)
            if warning_ports:
                columns.append({
                    "type": "Column",
                    "width": "stretch",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"🟠 警告: {len(warning_ports)}",
                        "weight": "Bolder",
                        "color": "Warning",
                        "size": "Large",
                        "horizontalAlignment": "Center"
                    }]
                })

            # 注意等級 (黃)
            if caution_ports:
                columns.append({
                    "type": "Column",
                    "width": "stretch",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"🟡 注意: {len(caution_ports)}",
                        "weight": "Bolder",
                        "color": "Accent",
                        "size": "Large",
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
                
            # 將摘要加入 body
            body.extend(summary_items)
            # --- Summary 邏輯結束 ---

            
            # 4. 依序加入各等級港口清單

            # 🔴 危險等級港口
            if danger_ports:
                body.append({
                    "type": "Container",
                    "style": "attention", # 紅色背景區塊
                    "spacing": "Large",
                    "separator": True,
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "🔴 危險等級 (Danger) 港口清單",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Attention",
                            "horizontalAlignment": "Center",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": "(條件: 風速 > 40 kts 或 陣風 > 50 kts)",
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
            
            # 🟠 警告等級港口
            if warning_ports:
                body.append({
                    "type": "Container",
                    "style": "warning", # 橘色背景區塊 (Teams 可能顯示淺黃)
                    "spacing": "Large",
                    "separator": True,
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "🟠 警告等級 (Warning) 港口清單",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Warning",
                            "horizontalAlignment": "Center",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": "(條件: 風速 > 30 kts 或 陣風 > 40 kts)",
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
            
            # 🟡 注意等級港口
            if caution_ports:
                body.append({
                    "type": "Container",
                    "style": "accent", # 淺藍/灰色背景
                    "spacing": "Medium",
                    "separator": True,
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "🟡 注意等級 (Caution) 港口清單",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Accent",
                            "horizontalAlignment": "Center",
                            "wrap": True
                        },
                        {
                            "type": "TextBlock",
                            "text": "(條件: 風速 > 25 kts 或 陣風 > 35 kts)",
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
            
            # 5. 底部提示 (Footer)
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
            
            # 6. 回傳完整的 Card
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
            """建立單一港口的資訊容器 (美化版)"""
            risk_emoji = self._get_risk_emoji(assessment.risk_level)
            
            # --- 1. 標題區塊 (Header) ---
            # 讓港口名稱大一點，國家資訊縮小並加上定位圖示
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
                                "size": "Large",  # 加大字體
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

            # --- 2. 核心數據區塊 (Statistics) ---
            # 使用 emphasis (灰色背景) 讓數據突顯出來
            
            # 簡化時段統計文字
            high_risk_count = len([p for p in assessment.risk_periods if p['risk_level'] >= 2])
            period_summary = f"共 {len(assessment.risk_periods)} 個時段"
            if high_risk_count > 0:
                period_summary += f" ({high_risk_count} 個警告+)"

            stats_section = {
                "type": "Container",
                "style": "emphasis",  # 關鍵：加入灰色背景
                "spacing": "Small",
                "items": [
                    {
                        "type": "FactSet",
                        "spacing": "Small",
                        "facts": [
                            # 使用 .0f 去除不必要的小數點 (如 23.0 -> 23)
                            {"title": "💨 未來48Hrs最大風速", "value": f"**{assessment.max_wind_kts:.0f}** kts (Bf: {assessment.max_wind_bft})"},
                            {"title": "🌬️ 未來48Hrs最大陣風", "value": f"**{assessment.max_gust_kts:.0f}** kts (Bf: {assessment.max_gust_bft})"},
                            {"title": "🌊 未來48Hrs最大浪高", "value": f"**{assessment.max_wave:.1f}** m"},
                            {"title": "⚠️ 風險因素", "value": ", ".join(assessment.risk_factors)},
                            {"title": "🕐 時段統計", "value": period_summary}
                        ]
                    }
                ]
            }

            # --- 3. 高風險列表區塊 (List) ---
            # 使用表格化呈現，解決文字擠在一起的問題
            list_section_items = []
            
            if assessment.risk_periods:
                # 列表標題
                list_section_items.append({
                    "type": "TextBlock",
                    "text": "📋 主要高風險時段 (Top5)",
                    "weight": "Bolder",
                    "size": "Small",
                    "color": "Accent",
                    "spacing": "Medium"
                })

                # 迴圈建立每一列 (Row)
                for period in assessment.risk_periods[:5]:
                    # 處理時間格式：只取 HH:MM (例如 "2025-12-28 14:30" -> "14:30")
                    try:
                        time_str = period['time'].split(' ')[1]
                    except:
                        time_str = period['time']

                    # 準備數據文字 (使用 emoji 縮短長度，方便手機閱讀)
                    # 範例: 💨20kt(Bf:5) 🌬️29kt(Bf:7) 🌊2.2m
                    detail_text = (
                        f"💨風速:{int(period['wind_speed_kts'])}kt(Bf:{period['wind_speed_bft']})  "
                        f"🌬️陣風:{int(period['wind_gust_kts'])}kt(Bf:{period['wind_gust_bft']})  "
                        f"🌊浪高:{period['wave_height']:.1f}m"
                    )

                    # 建立表格列 (左邊時間，右邊數據)
                    row = {
                        "type": "ColumnSet",
                        "spacing": "Small",
                        "columns": [
                            {
                                "type": "Column",
                                "width": "auto",  # 自動調整寬度以適應時間
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
                                "width": "stretch",  # 剩餘空間給數據
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

            # 將列表包裝進 Container
            list_container = {
                "type": "Container",
                "spacing": "Small",
                "items": list_section_items
            }

            # --- 4. 組合最終容器 ---
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
        self.notifier = TeamsNotifier(teams_webhook_url)
        self.db = WeatherDatabase()
        
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
        
        # 步驟 3: 發送 Teams 通知
        print("\n📢 步驟 3: 發送 Teams 通知...")
        notification_sent = self.notifier.send_risk_alert(risk_assessments)
        
        # 步驟 4: 生成報告
        print("\n📊 步驟 4: 生成執行報告...")
        report = self._generate_report(download_stats, risk_assessments, notification_sent)
        
        print("\n" + "=" * 80)
        print("✅ 每日監控執行完成")
        print("=" * 80)
        
        return report
    
    def _analyze_all_ports(self) -> List[RiskAssessment]:
        """分析所有港口的風險"""
        risk_assessments = []
        total_ports = len(self.crawler.port_list)
        
        print(f"開始分析 {total_ports} 個港口...")
        
        for i, port_code in enumerate(self.crawler.port_list, 1):
            try:
                # 從資料庫讀取最新資料
                data = self.db.get_latest_content(port_code)
                if not data:
                    continue
                
                content, issued_time, port_name = data
                
                # 取得港口資訊
                port_info = self.crawler.get_port_info(port_code)
                if not port_info:
                    continue
                
                # 分析風險
                assessment = self.analyzer.analyze_port_risk(
                    port_code, port_info, content, issued_time
                )
                
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
    
    def _generate_report(self, download_stats: Dict[str, int],
                        risk_assessments: List[RiskAssessment],
                        notification_sent: bool) -> Dict[str, Any]:
        """生成執行報告"""
        
        # 統計風險等級分布
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
                'top_risk_ports': [
                    {
                        'port_code': a.port_code,
                        'port_name': a.port_name,
                        'country': a.country,
                        'risk_level': a.risk_level,
                        'risk_label': self.analyzer.get_risk_label(a.risk_level),
                        'max_wind_kts': a.max_wind_kts,
                        'max_wind_bft': a.max_wind_bft,
                        'max_wind_time': a.max_wind_time,
                        'max_gust_kts': a.max_gust_kts,
                        'max_gust_bft': a.max_gust_bft,
                        'max_gust_time': a.max_gust_time,
                        'max_wave': a.max_wave,
                        'risk_factors': a.risk_factors,
                        'risk_period_count': len(a.risk_periods)
                    }
                    for a in sorted(
                        risk_assessments,
                        key=lambda x: (x.risk_level, x.max_wind_kts),
                        reverse=True
                    )[:20]
                ]
            },
            'notification': {
                'sent': notification_sent,
                'recipient': 'Microsoft Teams'
            }
        }
        
        # 輸出報告摘要
        print("\n📋 執行報告摘要:")
        print(f"   下載成功: {download_stats['success']} 個港口")
        print(f"   下載略過: {download_stats['skip']} 個港口")
        print(f"   下載失敗: {download_stats['fail']} 個港口")
        print(f"   風險港口: {len(risk_assessments)} 個")
        print(f"     - 危險等級: {risk_distribution['danger']} 個")
        print(f"     - 警告風險: {risk_distribution['warning']} 個")
        print(f"     - 注意警戒: {risk_distribution['caution']} 個")
        print(f"   Teams 通知: {'✅ 已發送' if notification_sent else '❌ 發送失敗'}")
        
        return report
    
    def save_report_to_file(self, report: Dict[str, Any],
                           output_dir: str = 'reports') -> str:
        """儲存報告到檔案"""
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"weather_monitor_report_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n💾 報告已儲存至: {filepath}")
        
        return filepath

def send_teams_notification():
    # --- 1. 從 GitHub Secrets 讀取 Webhook 網址 ---
    webhook_url = os.environ.get('TEAMS_WEBHOOK')
    
    if not webhook_url:
        print("❌ 錯誤：找不到 TEAMS_WEBHOOK 環境變數")
        return

    # --- 2. 準備要傳送的訊息內容 ---
    # 這裡你可以放任何你想說的話
    report_title = "🌤️ 每日天氣監控報告"
    report_content = "**系統狀態**"
    
    # 這是 Teams 看得懂的資料格式 (Adaptive Card 的簡化版)
    payload = {
        "title": report_title,
        "text": report_content,
        "themeColor": "0076D7" # 藍色邊條
    }

    # --- 3. 發射！ ---
    try:
        print("正在傳送訊息到 Teams...")
        # 這裡的 requests 必須要確保你有安裝 (pip install requests)
        response = requests.post(
            webhook_url, 
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code == 200:
            print("✅ Teams 通知發送成功！")
        else:
            print(f"❌ 發送失敗，錯誤代碼: {response.status_code}")
            print(response.text)
            sys.exit(1) # 讓 GitHub 知道出錯了

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        sys.exit(1)

# --- 執行主程式 ---
if __name__ == "__main__":
    # 這裡是你原本的爬蟲邏輯...
    # ...
    
    # 最後發送通知
    send_teams_notification()

# ================= 主程式進入點 =================
def main():
    """主程式"""
    print("=" * 80)
    print("🌊 WNI 港口氣象自動監控系統")
    print("=" * 80)
    
    # 檢查必要的環境變數
    if not AEDYN_USERNAME or not AEDYN_PASSWORD:
        print("❌ 錯誤報告: 未設定 AEDYN_USERNAME 或 AEDYN_PASSWORD 環境變數")
        print("\n請設定以下環境變數:")
        print("  export AEDYN_USERNAME='your_username@example.com'")
        print("  export AEDYN_PASSWORD='your_password'")
        print("  export TEAMS_WEBHOOK_URL='https://outlook.office.com/webhook/...'")
        sys.exit(1)
    
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ 錯誤報告: 未設定 TEAMS_WEBHOOK_URL，將無法發送 Teams 通知")
    
    try:
        # 初始化監控服務
        service = WeatherMonitorService(
            username=AEDYN_USERNAME,
            password=AEDYN_PASSWORD,
            teams_webhook_url=TEAMS_WEBHOOK_URL,
            excel_path=EXCEL_FILE_PATH
        )
        
        # 執行每日監控
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




