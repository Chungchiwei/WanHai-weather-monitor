# constant.py

# 基本風險定義風險閾值
HIGH_WIND_SPEED_kts = 25   # kts
HIGH_WIND_SPEED_Bft = 5    # BFT
HIGH_GUST_SPEED_kts = 35   # kts
HIGH_GUST_SPEED_Bft = 8    # BFT
HIGH_WAVE_SIG = 2.5    # m
VERY_HIGH_WAVE_SIG = 4.0  # m
EXTREME_GUST = 50      # kts

def wind_kts_to_ms(wind_kts: float) -> float:
    """風速轉換:Kts to m/s """
    return wind_kts * 0.514444

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

def wind_dir_deg(wind_direction: str) -> float:
    """風向轉換 方位角 to 度數 """
    compass_map = {
        'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5,
        'E': 90, 'ESE': 112.5, 'SE': 135, 'SSE': 157.5,
        'S': 180, 'SSW': 202.5, 'SW': 225, 'WSW': 247.5,
        'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
    }
    return compass_map.get(wind_direction.upper(), -1)  # 若無法辨識則回傳 -1
