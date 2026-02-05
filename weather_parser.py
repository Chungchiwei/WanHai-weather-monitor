# weather_parser.py
import re
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass
from constant import (
    kts_to_bft, wind_dir_deg, 
    HIGH_WIND_SPEED_kts, HIGH_WIND_SPEED_Bft, 
    HIGH_GUST_SPEED_kts, HIGH_GUST_SPEED_Bft, 
    HIGH_WAVE_SIG
)


@dataclass
class WeatherRecord:
    """氣象記錄資料結構(風浪資料)"""
    time: datetime              # UTC 時間
    lct_time: datetime          # LCT 當地時間
    wind_direction: str         # 風向 (例如: NNE)
    wind_speed_kts: float       # 風速 (knots)
    wind_gust_kts: float        # 陣風 (knots)
    wave_direction: str         # 浪向
    wave_height: float          # 顯著浪高 (meters)
    wave_max: float             # 最大浪高 (meters)
    wave_period: float          # 週期 (seconds)
    
    def __post_init__(self):
        """資料驗證與轉換"""
        # 確保數值欄位是浮點數
        self.wind_speed_kts = float(self.wind_speed_kts)
        self.wind_gust_kts = float(self.wind_gust_kts)
        self.wave_height = float(self.wave_height)
        self.wave_max = float(self.wave_max)
        self.wave_period = float(self.wave_period)
        
        # 確保方向是字串
        self.wind_direction = str(self.wind_direction).strip().upper()
        self.wave_direction = str(self.wave_direction).strip().upper()
    
    @property
    def wind_speed_ms(self) -> float:
        """風速轉換為 m/s"""
        return self.wind_speed_kts * 0.514444
    
    @property
    def wind_speed_bft(self) -> int:
        """風速轉換為 BFT"""
        return kts_to_bft(self.wind_speed_kts)
    
    @property
    def wind_gust_ms(self) -> float:
        """陣風轉換為 m/s"""
        return self.wind_gust_kts * 0.514444
    
    @property
    def wind_gust_bft(self) -> int:
        """陣風轉換為 BFT"""
        return kts_to_bft(self.wind_gust_kts)
    
    @property
    def wind_dir_deg(self) -> float:
        """風向轉換為度數"""
        return wind_dir_deg(self.wind_direction)
    
    @property
    def wave_dir_deg(self) -> float:
        """浪向轉換為度數"""
        return wind_dir_deg(self.wave_direction)
    
    @property
    def wave_sig_m(self) -> float:
        """顯著浪高 (保持原始 meters)"""
        return self.wave_height
    
    @property
    def wave_max_m(self) -> float:
        """最大浪高 (保持原始 meters)"""
        return self.wave_max
    
    @property
    def wave_period_s(self) -> float:
        """週期 (保持原始 seconds)"""
        return self.wave_period
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典格式"""
        return {
            'time': self.time,
            'lct_time': self.lct_time,
            'wind_direction': self.wind_direction,
            'wind_speed_kts': self.wind_speed_kts,
            'wind_speed_ms': self.wind_speed_ms,
            'wind_speed_bft': self.wind_speed_bft,
            'wind_gust_kts': self.wind_gust_kts,
            'wind_gust_ms': self.wind_gust_ms,
            'wind_gust_bft': self.wind_gust_bft,
            'wave_direction': self.wave_direction,
            'wave_height': self.wave_height,
            'wave_max': self.wave_max,
            'wave_period': self.wave_period,
            'wind_dir_deg': self.wind_dir_deg,
            'wave_dir_deg': self.wave_dir_deg
        }
    
    def __repr__(self) -> str:
        """字串表示"""
        return (f"WeatherRecord(time={self.time.strftime('%Y-%m-%d %H:%M')}, "
                f"wind={self.wind_direction} {self.wind_speed_kts:.1f}kts (gust {self.wind_gust_kts:.1f}kts), "
                f"LCT={self.lct_time.strftime('%H:%M')}, "
                f"wave={self.wave_direction} {self.wave_height:.1f}m)")


@dataclass
class WeatherConditionRecord:
    """天氣狀況記錄資料結構(溫度、降雨、氣壓、能見度等)"""
    time: datetime
    lct_time: datetime
    temperature: Optional[float]  # ✅ 改為 Optional
    precipitation: float
    pressure: Optional[float]     # ✅ 改為 Optional
    visibility: str
    weather_code: str
    
    def __post_init__(self):
        """資料驗證與轉換"""
        # ✅ 溫度驗證（允許 None，但排除異常值）
        if self.temperature is not None:
            try:
                self.temperature = float(self.temperature)
                # 排除異常值（地球表面溫度範圍約 -90°C ~ 60°C）
                if self.temperature < -100 or self.temperature > 100:
                    self.temperature = None
            except (ValueError, TypeError):
                self.temperature = None
        
        # 降雨量（不允許 None，預設 0.0）
        try:
            self.precipitation = float(self.precipitation) if self.precipitation is not None else 0.0
        except (ValueError, TypeError):
            self.precipitation = 0.0
        
        # ✅ 氣壓驗證（允許 None，但排除異常值）
        if self.pressure is not None:
            try:
                self.pressure = float(self.pressure)
                # 排除異常值（地球表面氣壓範圍約 870 ~ 1085 hPa）
                if self.pressure < 800 or self.pressure > 1100:
                    self.pressure = None
            except (ValueError, TypeError):
                self.pressure = None
        
        self.visibility = str(self.visibility).strip()
        self.weather_code = str(self.weather_code).strip().upper()
    
    @property
    def visibility_meters(self) -> Optional[float]:
        """能見度轉換為公尺(若可解析)"""
        vis = self.visibility.replace('<', '').replace('>', '').strip()
        
        if vis == "100":
            return 100.0
        elif "km" in vis:
            try:
                km = float(vis.replace('km', '').strip())
                return km * 1000
            except:
                return None
        else:
            try:
                return float(vis)
            except:
                return None
    
    @property
    def weather_description(self) -> str:
        """天氣代碼轉中文描述"""
        weather_map = {
            'CLR': '晴朗',
            'FOG': '霧',
            'MIST': '薄霧',
            'HAZE': '霾',
            'RAIN': '雨',
            'DRIZZLE': '毛毛雨',
            'SNOW': '雪',
            'SLEET': '雨夾雪',
            'THUNDER': '雷暴',
            'CLOUDY': '多雲',
            'OVERCAST': '陰天',
            'N/A': '無資料'
        }
        return weather_map.get(self.weather_code, self.weather_code)
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典格式"""
        return {
            'time': self.time,
            'lct_time': self.lct_time,
            'temperature': self.temperature,
            'precipitation': self.precipitation,
            'pressure': self.pressure,
            'visibility': self.visibility,
            'visibility_meters': self.visibility_meters,
            'weather_code': self.weather_code,
            'weather_description': self.weather_description
        }
    
    def __repr__(self) -> str:
        return (f"WeatherConditionRecord(time={self.time.strftime('%Y-%m-%d %H:%M')}, "
                f"LCT={self.lct_time.strftime('%H:%M')}, "
                f"temp={self.temperature}°C, precip={self.precipitation}mm/h, "
                f"pressure={self.pressure}hPa, vis={self.visibility}, wx={self.weather_code})")


class WeatherParser:    
    """WNI 氣象資料解析器 (支援 48h 和 7d 預報)"""
    
    LINE_PATTERN = re.compile(r'^\s*\d{4}\s+\d{4}\s+\d{4}\s+\d{4}')
    WIND_BLOCK_KEY = "WIND kts"
    WEATHER_BLOCK_KEY = "2. WEATHER"

    def detect_forecast_type(self, content: str) -> str:
        """
        自動偵測預報類型
        
        Args:
            content: 氣象檔案內容
            
        Returns:
            '48h' 或 '7d'
        """
        first_line = content.strip().split('\n')[0].upper()
        if '7 DAY' in first_line or '7-DAY' in first_line or '7DAY' in first_line:
            return '7d'
        elif '48 HOUR' in first_line or '48-HOUR' in first_line or '48HOUR' in first_line:
            return '48h'
        else:
            # 預設為 48h
            return '48h'

    def parse_content(self, content: str, port_timezone: Optional[str] = None, 
                    max_hours: Optional[int] = 48) -> Tuple[str, List[WeatherRecord], List[WeatherConditionRecord], List[str]]:
        """
        解析 WNI 氣象檔案內容(包含風浪 + 天氣狀況)
        
        Args:
            content: 氣象檔案內容
            port_timezone: 港口時區(保留參數,目前自動偵測)
            max_hours: 最大時數限制 (None 表示不限制,用於 7 天預報)
            
        Returns:
            Tuple[港口名稱, 風浪記錄列表, 天氣狀況記錄列表, 警告訊息列表]
        """
        def _safe_float(val_str, default=None):
            """安全轉換為浮點數（支援自訂預設值）"""
            clean = val_str.replace('*', '').strip()
            if not clean or clean == '-':
                return default
            try:
                return float(clean)
            except ValueError:
                return default

        # ✅ 移除錯誤的三行程式碼
        lines = content.strip().split('\n')
        warnings = []
        wind_wave_records = []
        weather_records = []
        
        # ========== 解析港口名稱 ==========
        port_name = "Unknown Port"
        for line in lines:
            if "PORT NAME" in line.upper():
                port_name = line.split(":", 1)[1].strip()
                break
        
        # ========== 解析風浪資料 (1. WINDS and WAVES) ==========
        wind_section_start = None
        for i, line in enumerate(lines):
            if self.WIND_BLOCK_KEY in line and "WAVE" in line:
                wind_section_start = i + 2  # 跳過標題行
                break
        
        if wind_section_start is None:
            raise ValueError("找不到 WIND 資料區段 (WIND kts)")
        
        current_year = datetime.now().year
        prev_mmdd = None
        lct_offset = None
        now_utc = datetime.now(timezone.utc)
        cutoff_time = now_utc + timedelta(hours=max_hours) if max_hours else None
        
        for line in lines[wind_section_start:]:
            line = line.strip()
            
            # 跳過空行和分隔線
            if not line or line.startswith('**') or line.startswith('*') or line.startswith('='):
                break
            
            # 檢查是否為資料行
            if not self.LINE_PATTERN.match(line):
                continue
            
            try:
                parts = line.split()
                if len(parts) < 11:
                    warnings.append(f"風浪欄位不足: {line}")
                    continue
                
                # 解析時間
                utc_date = parts[0]
                utc_time = parts[1]
                local_date = parts[2]
                local_time = parts[3]
                
                # 處理跨年
                if prev_mmdd and prev_mmdd > utc_date and prev_mmdd.startswith("12") and utc_date.startswith("01"):
                    current_year += 1
                prev_mmdd = utc_date
                
                # 建立 naive datetime
                dt_utc_naive = datetime.strptime(f"{current_year}{utc_date}{utc_time}", "%Y%m%d%H%M")
                dt_lct_naive = datetime.strptime(f"{current_year}{local_date}{local_time}", "%Y%m%d%H%M")
                
                # 第一筆資料時自動計算 LCT 時區偏移
                if lct_offset is None:
                    time_diff = dt_lct_naive - dt_utc_naive
                    offset_hours = int(time_diff.total_seconds() / 3600)
                    lct_offset = timezone(timedelta(hours=offset_hours))
                
                # 標記時區
                dt_utc = dt_utc_naive.replace(tzinfo=timezone.utc)
                dt_lct = dt_lct_naive.replace(tzinfo=lct_offset)
                
                # 檢查是否超過時間限制 (僅在有限制時)
                if cutoff_time and dt_utc > cutoff_time:
                    warnings.append(f"跳過超過 {max_hours} 小時的風浪數據: {dt_utc.strftime('%Y-%m-%d %H:%M')}")
                    continue
                
                # 建立氣象記錄
                record = WeatherRecord(
                    time=dt_utc,
                    lct_time=dt_lct,
                    wind_direction=parts[4],
                    wind_speed_kts=_safe_float(parts[5], default=0.0),
                    wind_gust_kts=_safe_float(parts[6], default=0.0),
                    wave_direction=parts[7],
                    wave_height=_safe_float(parts[8], default=0.0),
                    wave_max=_safe_float(parts[9], default=0.0),
                    wave_period=_safe_float(parts[10], default=0.0)
                )
                wind_wave_records.append(record)
                
            except Exception as e:
                warnings.append(f"風浪解析失敗 [{line}]: {str(e)}")
                continue
        
        if not wind_wave_records:
            raise ValueError("未成功解析任何風浪資料")
        
        # ========== 解析天氣狀況資料 (2. WEATHER) ==========
        weather_section_start = None
        for i, line in enumerate(lines):
            if self.WEATHER_BLOCK_KEY in line:
                # 找到包含 "deg  mm/h   hPa  m" 的標題行
                for j in range(i+1, min(i+5, len(lines))):
                    if "deg" in lines[j] and "mm/h" in lines[j] and "hPa" in lines[j]:
                        weather_section_start = j + 2  # 跳過標題和欄位名稱
                        break
                break
        
        if weather_section_start:
            current_year_wx = datetime.now().year
            prev_mmdd_wx = None
            
            for line in lines[weather_section_start:]:
                line = line.strip()
                
                # 跳過空行和分隔線
                if not line or line.startswith('**') or line.startswith('*') or line.startswith('='):
                    break
                
                # 檢查是否為資料行
                if not self.LINE_PATTERN.match(line):
                    continue
                
                try:
                    parts = line.split()
                    if len(parts) < 8:  # 至少需要 8 個欄位(時間4 + 資料4)
                        warnings.append(f"天氣欄位不足: {line}")
                        continue
                    
                    # 解析時間
                    utc_date = parts[0]
                    utc_time = parts[1]
                    local_date = parts[2]
                    local_time = parts[3]
                    
                    # 處理跨年
                    if prev_mmdd_wx and prev_mmdd_wx > utc_date and prev_mmdd_wx.startswith("12") and utc_date.startswith("01"):
                        current_year_wx += 1
                    prev_mmdd_wx = utc_date
                    
                    dt_utc_naive = datetime.strptime(f"{current_year_wx}{utc_date}{utc_time}", "%Y%m%d%H%M")
                    dt_lct_naive = datetime.strptime(f"{current_year_wx}{local_date}{local_time}", "%Y%m%d%H%M")
                    
                    dt_utc = dt_utc_naive.replace(tzinfo=timezone.utc)
                    dt_lct = dt_lct_naive.replace(tzinfo=lct_offset if lct_offset else timezone.utc)
                    
                    # 檢查是否超過時間限制 (僅在有限制時)
                    if cutoff_time and dt_utc > cutoff_time:
                        warnings.append(f"跳過超過 {max_hours} 小時的天氣數據: {dt_utc.strftime('%Y-%m-%d %H:%M')}")
                        continue
                    
                    # ✅ 正確位置：在迴圈內部解析天氣資料
                    temp = _safe_float(parts[4], default=None)
                    precip = _safe_float(parts[5], default=0.0)
                    pressure = _safe_float(parts[6], default=None)
                    visibility = parts[7]
                    weather_code = parts[8] if len(parts) > 8 else "N/A"
                    
                    wx_record = WeatherConditionRecord(
                        time=dt_utc,
                        lct_time=dt_lct,
                        temperature=temp,
                        precipitation=precip,
                        pressure=pressure,
                        visibility=visibility,
                        weather_code=weather_code
                    )
                    weather_records.append(wx_record)
                    
                except Exception as e:
                    warnings.append(f"天氣解析失敗 [{line}]: {str(e)}")
                    continue
        else:
            warnings.append("⚠️ 未找到 WEATHER 資料區段")
        
        # 最終檢查記錄數量 (根據預報類型調整)
        expected_wind_records = 56 if max_hours is None or max_hours > 48 else 20  # 7d: ~56筆, 48h: ~16筆
        expected_weather_records = 56 if max_hours is None or max_hours > 48 else 20
        
        if len(wind_wave_records) > expected_wind_records:
            warnings.append(f"⚠️ 風浪記錄數量異常: {len(wind_wave_records)} 筆(預期 ≤ {expected_wind_records} 筆)")
        
        if len(weather_records) > expected_weather_records:
            warnings.append(f"⚠️ 天氣記錄數量異常: {len(weather_records)} 筆(預期 ≤ {expected_weather_records} 筆)")
        
        return port_name, wind_wave_records, weather_records, warnings


    def parse_content_7d(self, content: str, port_timezone: Optional[str] = None) -> Tuple[str, List[WeatherRecord], List[WeatherConditionRecord], List[str]]:
        """
        解析 7 天預報資料 (無時間限制)
        
        Args:
            content: 氣象檔案內容
            port_timezone: 港口時區(保留參數,目前自動偵測)
            
        Returns:
            Tuple[港口名稱, 風浪記錄列表, 天氣狀況記錄列表, 警告訊息列表]
        """
        return self.parse_content(content, port_timezone, max_hours=None)
    
    def parse_content_48h(self, content: str, port_timezone: Optional[str] = None) -> Tuple[str, List[WeatherRecord], List[WeatherConditionRecord], List[str]]:
        """
        解析 48 小時預報資料 (限制 48 小時)
        
        Args:
            content: 氣象檔案內容
            port_timezone: 港口時區(保留參數,目前自動偵測)
            
        Returns:
            Tuple[港口名稱, 風浪記錄列表, 天氣狀況記錄列表, 警告訊息列表]
        """
        return self.parse_content(content, port_timezone, max_hours=48)
    
    def parse_file(self, file_path: str, forecast_type: str = 'auto') -> Tuple[str, List[WeatherRecord], List[WeatherConditionRecord], List[str]]:
        """
        從檔案解析氣象資料
        
        Args:
            file_path: 檔案路徑
            forecast_type: 預報類型 ('48h', '7d', 'auto')
            
        Returns:
            Tuple[港口名稱, 風浪記錄列表, 天氣狀況記錄列表, 警告訊息列表]
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if forecast_type == 'auto':
            forecast_type = self.detect_forecast_type(content)
        
        if forecast_type == '7d':
            return self.parse_content_7d(content)
        else:
            return self.parse_content_48h(content)
    
    @staticmethod
    def filter_high_risk_records(records: List[WeatherRecord], 
                                 wind_kts_threshold: float = HIGH_WIND_SPEED_kts,
                                 wind_bft_threshold: int = HIGH_WIND_SPEED_Bft,
                                 gust_kts_threshold: float = HIGH_GUST_SPEED_kts,
                                 gust_bft_threshold: int = HIGH_GUST_SPEED_Bft,
                                 wave_threshold: float = HIGH_WAVE_SIG) -> List[WeatherRecord]:
        """
        篩選高風險時段(風浪)
        
        Args:
            records: 氣象記錄列表
            wind_kts_threshold: 風速警戒值 (kts)
            wind_bft_threshold: 風速警戒值 (BFT)
            gust_kts_threshold: 陣風警戒值 (kts)
            gust_bft_threshold: 陣風警戒值 (BFT)
            wave_threshold: 浪高警戒值 (m)
            
        Returns:
            高風險記錄列表
        """
        return [
            r for r in records
            if r.wind_speed_kts >= wind_kts_threshold
            or r.wind_speed_bft >= wind_bft_threshold
            or r.wind_gust_kts >= gust_kts_threshold  
            or r.wind_gust_bft >= gust_bft_threshold
            or r.wave_height >= wave_threshold
        ]
    
    @staticmethod
    def get_statistics(records: List[WeatherRecord]) -> Dict[str, Any]:
        """
        計算風浪統計資訊
        
        Args:
            records: 氣象記錄列表
            
        Returns:
            統計資訊字典
        """
        if not records:
            return {}
        
        wind_speeds_kts = [r.wind_speed_kts for r in records]
        wind_speeds_ms  = [r.wind_speed_ms for r in records]
        wind_speeds_bft = [r.wind_speed_bft for r in records]
        wind_gusts_kts  = [r.wind_gust_kts for r in records]
        wind_gusts_ms   = [r.wind_gust_ms for r in records]
        wind_gusts_bft  = [r.wind_gust_bft for r in records]
        wave_heights    = [r.wave_height for r in records]
        
        return {
            'total_records': len(records),
            'time_range': {
                'start': min(r.time for r in records),
                'end': max(r.time for r in records)
            },
            'wind': {
                'min_kts': min(wind_speeds_kts),
                'max_kts': max(wind_speeds_kts),
                'avg_kts': sum(wind_speeds_kts) / len(wind_speeds_kts),
                'min_ms': min(wind_speeds_ms),
                'max_ms': max(wind_speeds_ms),
                'avg_ms': sum(wind_speeds_ms) / len(wind_speeds_ms),
                'min_bft': min(wind_speeds_bft),
                'max_bft': max(wind_speeds_bft),
                'max_gust_kts': max(wind_gusts_kts),
                'max_gust_ms': max(wind_gusts_ms),
                'max_gust_bft': max(wind_gusts_bft)
            },
            'wave': {
                'min': min(wave_heights),
                'max': max(wave_heights),
                'avg': sum(wave_heights) / len(wave_heights),
                'max_wave': max(r.wave_max for r in records)
            }
        }
    
    @staticmethod
    def get_weather_statistics(records: List[WeatherConditionRecord]) -> Dict[str, Any]:
        """
        計算天氣狀況統計資訊
        
        Args:
            records: 天氣狀況記錄列表
            
        Returns:
            統計資訊字典
        """
        if not records:
            return {}
        
        temps = [r.temperature for r in records]
        precips = [r.precipitation for r in records]
        pressures = [r.pressure for r in records]
        
        return {
            'total_records': len(records),
            'time_range': {
                'start': min(r.time for r in records),
                'end': max(r.time for r in records)
            },
            'temperature': {
                'min': min(temps),
                'max': max(temps),
                'avg': sum(temps) / len(temps)
            },
            'precipitation': {
                'total': sum(precips),
                'max': max(precips),
                'rainy_hours': sum(1 for p in precips if p > 0)
            },
            'pressure': {
                'min': min(pressures),
                'max': max(pressures),
                'avg': sum(pressures) / len(pressures)
            },
            'weather_codes': {
                code: sum(1 for r in records if r.weather_code == code)
                for code in set(r.weather_code for r in records)
            }
        }


# ================= 測試範例 =================
if __name__ == "__main__":
    # 測試 48 小時預報
    sample_content_48h = """48 hour GLOBAL PORT FORECAST WEATHERNEWS.INC
PORT NAME: DALIAN
PORT CODE: DLN
COUNTRY  : CHINA
         : 38-56.7N 121-40.5E
ISSUED AT: 20260205 0000 UTC

1. WINDS and WAVES
                    WIND kts        WAVE  m            seconds
UTC       LCT       DIR  SPEED GUST DIR   SIG     MAX  PERIOD 
0205 0000 0205 0800 NNW   21*  31*  NNW    0.4     0.7       2
0205 0100 0205 0900 NNW   23*  34*  NNW    0.5     0.9       2
0205 0200 0205 1000  N    25*  37*   N     0.7     1.1       3
0205 0300 0205 1100  N    27*  41*   N     0.8     1.3       3
0205 0400 0205 1200  N    27*  41*   N     0.8     1.3       3

2. WEATHER
                    deg  mm/h   hPa  m           
UTC       LCT       TEMP PRCP   PRES VIS     Wx  
0205 0000 0205 0800   -1    0   1021   100   FOG 
0205 0100 0205 0900   -2    0   1023 10km<   CLR 
0205 0200 0205 1000   -3    0   1024 10km<   CLR 
0205 0300 0205 1100   -4    0   1026 10km<   CLR 
0205 0400 0205 1200   -4    0   1026 10km<   CLR 
"""
    
    # 測試 7 天預報
    sample_content_7d = """7 day GLOBAL PORT FORECAST WEATHERNEWS.INC
PORT NAME: KAOHSIUNG
PORT CODE: TWKHH
COUNTRY  : TAIWAN
         : 22-36.6N 120-16.2E
ISSUED AT: 20260205 0000 UTC

1. WINDS and WAVES
                    WIND kts        WAVE  m            seconds
UTC       LCT       DIR  SPEED GUST DIR   SIG     MAX  PERIOD 
0205 0000 0205 0800 NE    15   22   NE     0.8     1.3       4
0205 0300 0205 1100 NE    16   24   NE     0.9     1.4       4
0205 0600 0205 1400 NE    17   25   NE     1.0     1.6       4
0205 0900 0205 1700 ENE   18   27   ENE    1.1     1.7       5
0205 1200 0205 2000 ENE   19   28   ENE    1.2     1.9       5

2. WEATHER
                    deg  mm/h   hPa  m           
UTC       LCT       TEMP PRCP   PRES VIS     Wx  
0205 0000 0205 0800   18    0   1018 10km<   CLR 
0205 0300 0205 1100   19    0   1017 10km<   CLR 
0205 0600 0205 1400   22    0   1016 10km<   CLR 
0205 0900 0205 1700   24    0   1015 10km<   CLR 
0205 1200 0205 2000   23    0   1015 10km<   CLR 
"""
    
    parser = WeatherParser()
    
    print("=" * 80)
    print("測試 48 小時預報解析")
    print("=" * 80)
    try:
        port_name, wind_records, weather_records, warnings = parser.parse_content_48h(sample_content_48h)
        
        print(f"🏙️  港口: {port_name}")
        print(f"📊 風浪記錄: {len(wind_records)} 筆")
        print(f"🌡️  天氣記錄: {len(weather_records)} 筆")
        print(f"⚠️  警告: {len(warnings)} 個")
        
        if wind_records:
            print(f"\n時間範圍: {wind_records[0].time.strftime('%Y-%m-%d %H:%M')} ~ {wind_records[-1].time.strftime('%Y-%m-%d %H:%M')}")
            wind_stats = parser.get_statistics(wind_records)
            print(f"風速範圍: {wind_stats['wind']['min_kts']:.1f} - {wind_stats['wind']['max_kts']:.1f} kts")
            print(f"浪高範圍: {wind_stats['wave']['min']:.1f} - {wind_stats['wave']['max']:.1f} m")
        
    except Exception as e:
        print(f"❌ 錯誤: {e}")
    
    print("\n" + "=" * 80)
    print("測試 7 天預報解析")
    print("=" * 80)
    try:
        port_name, wind_records, weather_records, warnings = parser.parse_content_7d(sample_content_7d)
        
        print(f"🏙️  港口: {port_name}")
        print(f"📊 風浪記錄: {len(wind_records)} 筆")
        print(f"🌡️  天氣記錄: {len(weather_records)} 筆")
        print(f"⚠️  警告: {len(warnings)} 個")
        
        if wind_records:
            print(f"\n時間範圍: {wind_records[0].time.strftime('%Y-%m-%d %H:%M')} ~ {wind_records[-1].time.strftime('%Y-%m-%d %H:%M')}")
            wind_stats = parser.get_statistics(wind_records)
            print(f"風速範圍: {wind_stats['wind']['min_kts']:.1f} - {wind_stats['wind']['max_kts']:.1f} kts")
            print(f"浪高範圍: {wind_stats['wave']['min']:.1f} - {wind_stats['wave']['max']:.1f} m")
        
    except Exception as e:
        print(f"❌ 錯誤: {e}")
    
    print("\n" + "=" * 80)
    print("測試自動偵測預報類型")
    print("=" * 80)
    print(f"48h 內容偵測結果: {parser.detect_forecast_type(sample_content_48h)}")
    print(f"7d 內容偵測結果: {parser.detect_forecast_type(sample_content_7d)}")
    
    print("\n✅ 測試完成!")
