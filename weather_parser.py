# weather_parser.py
import re
import math
from datetime import datetime, timedelta, time
from typing import List, Tuple, Dict, Optional, Any
from dataclasses import dataclass

# ================= 常數定義 =================
HP_TO_BOLLARD_PULL_PER_100HP = 1.1 
GRAVITY = 9.81

# 風險閾值
HIGH_WIND_SPEED = 30   # kts
HIGH_GUST_SPEED = 40   # kts
HIGH_WAVE_SIG = 2.5    # m
VERY_HIGH_WAVE_SIG = 4.0  # m
EXTREME_GUST = 50      # kts
NIGHT_HOURS = (20, 6)

# 繫泊幾何
BREAST_LINE_ANGLE = 90
SPRING_LINE_ANGLE = 15

# 效率係數（考慮纜繩角度和摩擦）
HEAD_TRANS_EFF = 0.95   # 頭纜橫向效率
HEAD_LONG_EFF = 0.15    # 頭纜縱向效率
SPRING_TRANS_EFF = 0.25 # 倒纜橫向效率
SPRING_LONG_EFF = 0.95  # 倒纜縱向效率


@dataclass
class WeatherRecord:
    """氣象記錄資料結構"""
    time: datetime
    wind_direction: str          # 風向 (例如: NNE)
    wind_speed: float           # 風速 (knots)
    wind_gust: float            # 陣風 (knots)
    wave_direction: str         # 浪向
    wave_height: float          # 顯著浪高 (meters)
    wave_max: float             # 最大浪高 (meters)
    wave_period: float          # 週期 (seconds)
    
    def __post_init__(self):
        """資料驗證與轉換"""
        # 確保數值欄位是浮點數
        self.wind_speed = float(self.wind_speed)
        self.wind_gust = float(self.wind_gust)
        self.wave_height = float(self.wave_height)
        self.wave_max = float(self.wave_max)
        self.wave_period = float(self.wave_period)
        
        # 確保方向是字串
        self.wind_direction = str(self.wind_direction).strip().upper()
        self.wave_direction = str(self.wave_direction).strip().upper()
    
    @property
    def wind_speed_ms(self) -> float:
        """風速轉換為 m/s"""
        return self.wind_speed * 0.514444
    
    @property
    def wind_gust_ms(self) -> float:
        """陣風轉換為 m/s"""
        return self.wind_gust * 0.514444
    
    @property
    def wind_dir_deg(self) -> float:
        """風向轉換為度數"""
        compass_map = {
            'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5,
            'E': 90, 'ESE': 112.5, 'SE': 135, 'SSE': 157.5,
            'S': 180, 'SSW': 202.5, 'SW': 225, 'WSW': 247.5,
            'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
        }
        return compass_map.get(self.wind_direction, 0.0)
    
    @property
    def wave_dir_deg(self) -> float:
        """浪向轉換為度數"""
        compass_map = {
            'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5,
            'E': 90, 'ESE': 112.5, 'SE': 135, 'SSE': 157.5,
            'S': 180, 'SSW': 202.5, 'SW': 225, 'WSW': 247.5,
            'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
        }
        return compass_map.get(self.wave_direction, 0.0)
    
    @property
    def wind_speed_kts(self) -> float:
        """風速 (保持原始 knots)"""
        return self.wind_speed
    
    @property
    def wind_gust_kts(self) -> float:
        """陣風 (保持原始 knots)"""
        return self.wind_gust
    
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
            'wind_direction': self.wind_direction,
            'wind_speed': self.wind_speed,
            'wind_gust': self.wind_gust,
            'wave_direction': self.wave_direction,
            'wave_height': self.wave_height,
            'wave_max': self.wave_max,
            'wave_period': self.wave_period,
            'wind_speed_ms': self.wind_speed_ms,
            'wind_gust_ms': self.wind_gust_ms,
            'wind_dir_deg': self.wind_dir_deg,
            'wave_dir_deg': self.wave_dir_deg
        }
    
    def __repr__(self) -> str:
        """字串表示"""
        return (f"WeatherRecord(time={self.time.strftime('%Y-%m-%d %H:%M')}, "
                f"wind={self.wind_direction} {self.wind_speed:.1f}kts (gust {self.wind_gust:.1f}kts), "
                f"wave={self.wave_direction} {self.wave_height:.1f}m)")


class WeatherParser:    
    """WNI 氣象資料解析器 (Enhanced Robustness)"""
    
    LINE_PATTERN = re.compile(r'^\d{4}\s+\d{4}\s+\d{4}\s+\d{4}')
    WIND_BLOCK_KEY = "WIND kts"

    def parse_content(self, content: str) -> Tuple[str, List[WeatherRecord], List[str]]:
        """
        解析 WNI 氣象檔案內容
        
        Args:
            content: WNI 氣象檔案的文字內容
            
        Returns:
            Tuple[港口名稱, 氣象記錄列表, 警告訊息列表]
        """
        lines = content.strip().split('\n')
        warnings = []
        records = []
        
        # 解析港口名稱
        port_name = "Unknown Port"
        for line in lines:
            if "PORT NAME" in line.upper():
                port_name = line.split(":", 1)[1].strip()
                break
        
        # 找到風浪資料區段
        wind_section_start = None
        for i, line in enumerate(lines):
            if self.WIND_BLOCK_KEY in line and "WAVE" in line:
                wind_section_start = i + 2
                break
        
        if wind_section_start is None:
            raise ValueError("找不到 WIND 資料區段 (WIND kts)")
        
        current_year = datetime.now().year
        prev_mmdd = None
        
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
                    warnings.append(f"欄位不足: {line}")
                    continue
                
                # 解析日期時間
                lct_date = parts[2]
                lct_time = parts[3]
                
                # 處理跨年
                if prev_mmdd and prev_mmdd > lct_date and prev_mmdd.startswith("12") and lct_date.startswith("01"):
                    current_year += 1
                prev_mmdd = lct_date
                
                dt = datetime.strptime(f"{current_year}{lct_date}{lct_time}", "%Y%m%d%H%M")
                
                def _safe_float(val_str):
                    """安全轉換為浮點數（處理 * 符號）"""
                    clean = val_str.replace('*', '')
                    return float(clean) if clean else 0.0

                # 建立氣象記錄
                record = WeatherRecord(
                    time=dt,
                    wind_direction=parts[4],
                    wind_speed=_safe_float(parts[5]),
                    wind_gust=_safe_float(parts[6]),
                    wave_direction=parts[7],
                    wave_height=_safe_float(parts[8]),
                    wave_max=_safe_float(parts[9]),
                    wave_period=_safe_float(parts[10])
                )
                records.append(record)
                
            except Exception as e:
                warnings.append(f"解析失敗 [{line}]: {str(e)}")
                continue
        
        if not records:
            raise ValueError("未成功解析任何氣象資料")
        
        return port_name, records, warnings
    
    def parse_file(self, file_path: str) -> Tuple[str, List[WeatherRecord], List[str]]:
        """
        從檔案解析氣象資料
        
        Args:
            file_path: 檔案路徑
            
        Returns:
            Tuple[港口名稱, 氣象記錄列表, 警告訊息列表]
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return self.parse_content(content)
    
    @staticmethod
    def filter_high_risk_records(records: List[WeatherRecord], 
                                 wind_threshold: float = HIGH_WIND_SPEED,
                                 gust_threshold: float = HIGH_GUST_SPEED,
                                 wave_threshold: float = HIGH_WAVE_SIG) -> List[WeatherRecord]:
        """
        篩選高風險時段
        
        Args:
            records: 氣象記錄列表
            wind_threshold: 風速閾值 (kts)
            gust_threshold: 陣風閾值 (kts)
            wave_threshold: 浪高閾值 (m)
            
        Returns:
            高風險記錄列表
        """
        return [
            r for r in records
            if r.wind_speed >= wind_threshold 
            or r.wind_gust >= gust_threshold 
            or r.wave_height >= wave_threshold
        ]
    
    @staticmethod
    def get_statistics(records: List[WeatherRecord]) -> Dict[str, Any]:
        """
        計算氣象統計資訊
        
        Args:
            records: 氣象記錄列表
            
        Returns:
            統計資訊字典
        """
        if not records:
            return {}
        
        wind_speeds = [r.wind_speed for r in records]
        wind_gusts = [r.wind_gust for r in records]
        wave_heights = [r.wave_height for r in records]
        
        return {
            'total_records': len(records),
            'time_range': {
                'start': min(r.time for r in records),
                'end': max(r.time for r in records)
            },
            'wind': {
                'min': min(wind_speeds),
                'max': max(wind_speeds),
                'avg': sum(wind_speeds) / len(wind_speeds),
                'max_gust': max(wind_gusts)
            },
            'wave': {
                'min': min(wave_heights),
                'max': max(wave_heights),
                'avg': sum(wave_heights) / len(wave_heights),
                'max_wave': max(r.wave_max for r in records)
            }
        }


# ================= 測試範例 =================
if __name__ == "__main__":
    # 測試範例
    sample_content = """
PORT NAME: KAOHSIUNG

                UTC           LCT           WIND kts                 WAVE
DATE  TIME  DATE  TIME  DIR  SPD  GST  DIR   SIG   MAX  PER
1223  0000  1223  0800  NNE   15   20  NNE   1.5   2.0  6.0
1223  0600  1223  1400  NE    18   25  NE    1.8   2.5  6.5
1223  1200  1223  2000  ENE   22   30  ENE   2.2   3.0  7.0
"""
    
    parser = WeatherParser()
    try:
        port_name, records, warnings = parser.parse_content(sample_content)
        
        print(f"港口: {port_name}")
        print(f"記錄數: {len(records)}")
        print(f"警告數: {len(warnings)}")
        
        if records:
            print("\n前 3 筆記錄:")
            for i, record in enumerate(records[:3], 1):
                print(f"{i}. {record}")
        
        if warnings:
            print("\n警告訊息:")
            for warning in warnings:
                print(f"  - {warning}")
        
        # 統計資訊
        stats = parser.get_statistics(records)
        print("\n統計資訊:")
        print(f"  風速範圍: {stats['wind']['min']:.1f} - {stats['wind']['max']:.1f} kts")
        print(f"  最大陣風: {stats['wind']['max_gust']:.1f} kts")
        print(f"  浪高範圍: {stats['wave']['min']:.1f} - {stats['wave']['max']:.1f} m")
        
    except Exception as e:
        print(f"錯誤: {e}")
        import traceback
        traceback.print_exc()
