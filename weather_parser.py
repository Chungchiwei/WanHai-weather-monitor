# weather_parser.py
import re
from datetime import datetime, timezone, timedelta  # ✅ 加上 timedelta
from typing import List, Tuple, Dict, Any, Optional  # ✅ 確保有 Optional
from dataclasses import dataclass
from constant import kts_to_bft, wind_dir_deg, HIGH_WIND_SPEED_kts, HIGH_WIND_SPEED_Bft, HIGH_GUST_SPEED_kts, HIGH_GUST_SPEED_Bft, HIGH_WAVE_SIG
import re
@dataclass
class WeatherRecord:
    """氣象記錄資料結構"""
    time: datetime          # UTC 時間
    lct_time: datetime      # 新增：LCT 當地時間
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


class WeatherParser:    
    """WNI 氣象資料解析器 (Enhanced Robustness)"""
    
    LINE_PATTERN = re.compile(r'^\s*\d{4}\s+\d{4}\s+\d{4}\s+\d{4}')
    WIND_BLOCK_KEY = "WIND kts"

    def parse_content(self, content: str, port_timezone: Optional[str] = None) -> Tuple[str, List[WeatherRecord], List[str]]:
        """
        解析 WNI 氣象檔案內容（限制 48 小時）
        """
        def _safe_float(val_str):
            clean = val_str.replace('*', '')
            return float(clean) if clean else 0.0
        
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
        lct_offset = None
        
        # 🔥 新增：計算 48 小時截止時間
        now_utc = datetime.now(timezone.utc)
        cutoff_time = now_utc + timedelta(hours=48)
        
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
                
                # 🔥 新增：檢查是否超過 48 小時
                if dt_utc > cutoff_time:
                    warnings.append(f"跳過超過 48 小時的數據: {dt_utc.strftime('%Y-%m-%d %H:%M')}")
                    continue  # 跳過這筆記錄
                
                # 建立氣象記錄
                record = WeatherRecord(
                    time=dt_utc,
                    lct_time=dt_lct,
                    wind_direction=parts[4],
                    wind_speed_kts=_safe_float(parts[5]),
                    wind_gust_kts=_safe_float(parts[6]),
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
        
        # 🔥 新增：最終檢查記錄數量
        if len(records) > 20:
            warnings.append(f"⚠️ 記錄數量異常: {len(records)} 筆（預期 ≤ 16 筆）")
        
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
                                 wind_kts_threshold: float = HIGH_WIND_SPEED_kts,
                                 wind_bft_threshold: int = HIGH_WIND_SPEED_Bft,
                                 gust_kts_threshold: float = HIGH_GUST_SPEED_kts,
                                 gust_bft_threshold: int = HIGH_GUST_SPEED_Bft,
                                 wave_threshold: float = HIGH_WAVE_SIG) -> List[WeatherRecord]:
        """
        篩選高風險時段
        
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
        計算氣象統計資訊
        
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
                print(f"   風速: {record.wind_speed_kts:.1f} kts = {record.wind_speed_ms:.1f} m/s = BFT {record.wind_speed_bft}")
                print(f"   陣風: {record.wind_gust_kts:.1f} kts = {record.wind_gust_ms:.1f} m/s = BFT {record.wind_gust_bft}")
        
        if warnings:
            print("\n警告訊息:")
            for warning in warnings:
                print(f"  - {warning}")
        
        # 統計資訊
        stats = parser.get_statistics(records)
        print("\n統計資訊:")
        print(f"  總記錄數: {stats['total_records']}")
        print(f"  時間範圍: {stats['time_range']['start']} ~ {stats['time_range']['end']}")
        print(f"  風速範圍: {stats['wind']['min_kts']:.1f} - {stats['wind']['max_kts']:.1f} kts")
        print(f"  平均風速: {stats['wind']['avg_kts']:.1f} kts")
        print(f"  最大陣風: {stats['wind']['max_gust_kts']:.1f} kts (Bf {stats['wind']['max_gust_bft']})")
        print(f"  浪高範圍: {stats['wave']['min']:.1f} - {stats['wave']['max']:.1f} m")
        print(f"  平均浪高: {stats['wave']['avg']:.1f} m")
        print(f"  最大浪高: {stats['wave']['max_wave']:.1f} m")
        
        # 測試高風險篩選
        high_risk = parser.filter_high_risk_records(records)
        print(f"\n高風險時段: {len(high_risk)} 筆")
        for record in high_risk:
            print(f"  - {record}")
        
    except Exception as e:
        print(f"錯誤: {e}")
        import traceback
        traceback.print_exc()
# weather_parser.py