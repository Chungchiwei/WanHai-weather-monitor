# wni_crawler.py
import requests
import sqlite3
import pandas as pd
import os
import json
import pickle
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
import numpy as np
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time

# 忽略 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 設定區 =================
DB_FILE = 'WNI_port_weather.db'
EXCEL_FILE_WANHAI = 'WHL_all_ports_list.xlsx'
COOKIE_FILE = 'aedyn_cookies.pkl'
TIMEOUT = 30
MAX_RETRIES = 3
COOKIE_EXPIRY_HOURS = 24

LOGIN_URL = (
    "https://idp.aedyn.wni.com/auth/realms/aedyn/protocol/openid-connect/auth"
    "?response_type=id_token%20token&scope=openid&client_id=aedyn"
    "&state=cZr_CP7VqEq2p8j6D_a_YrL2ucA"
    "&redirect_uri=https%3A%2F%2Faedyn.weathernews.com%2Fhttpd-auth%2Fredirect_uri"
    "&nonce=cwGprMflnWRdzaLvLMkCMI2az5vjS79XdTW0gtUulwo"
)


class AedynLoginManager:
    """負責自動登入 Aedyn 並取得最新 Cookie 和 JWT Token"""
    
    def __init__(self, username: str, password: str, cookie_file: str = COOKIE_FILE):
        """
        初始化登入管理器
        
        Args:
            username: Aedyn 帳號
            password: Aedyn 密碼
            cookie_file: Cookie 儲存檔案路徑
        """
        self.username = username
        self.password = password
        self.cookie_file = cookie_file
        self.cookies: Dict[str, str] = {}
        self.jwt_token: str = ""
        self.cookie_timestamp: Optional[datetime] = None
        
    def save_cookies(self) -> None:
        """儲存 Cookie 到檔案"""
        try:
            data = {
                'cookies': self.cookies,
                'jwt_token': self.jwt_token,
                'timestamp': datetime.now()
            }
            with open(self.cookie_file, 'wb') as f:
                pickle.dump(data, f)
            print(f"✅ Cookie 已儲存至 {self.cookie_file}")
        except Exception as e:
            print(f"⚠️ Cookie 儲存失敗: {e}")
    
    def load_cookies(self) -> bool:
        """
        從檔案載入 Cookie
        
        Returns:
            bool: 載入成功且未過期返回 True
        """
        if not os.path.exists(self.cookie_file):
            print(f"ℹ️ Cookie 檔案不存在: {self.cookie_file}")
            return False
        
        try:
            with open(self.cookie_file, 'rb') as f:
                data = pickle.load(f)
            
            self.cookies = data.get('cookies', {})
            self.jwt_token = data.get('jwt_token', '')
            self.cookie_timestamp = data.get('timestamp')
            
            # 檢查 Cookie 是否過期
            if self.cookie_timestamp:
                age = datetime.now() - self.cookie_timestamp
                print(f"ℹ️ 上次 Cookie 更新時間: {self.cookie_timestamp.strftime('%Y-%m-%d %H:%M:%S')} ({age.total_seconds() / 3600:.1f} 小時前)")
                
                if age > timedelta(hours=COOKIE_EXPIRY_HOURS):
                    print(f"⚠️ Cookie 已過期(超過 {COOKIE_EXPIRY_HOURS} 小時)")
                    return False
            
            print(f"✅ 已載入 Cookie (數量: {len(self.cookies)})")
            return True
            
        except Exception as e:
            print(f"⚠️ Cookie 載入失敗: {e}")
            return False
    
    def verify_cookies(self) -> bool:
        """
        驗證 Cookie 是否有效
        
        Returns:
            bool: Cookie 有效返回 True
        """
        if not self.cookies:
            return False
        
        try:
            headers = self.get_headers()
            response = requests.get(
                "https://aedyn.weathernews.com/api/account/user",
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response.status_code == 200:
                user_data = response.json()
                user_name = user_data.get('user_disp_name', 'Unknown User')
                print(f"✅ Cookie 有效!使用者: {user_name}")
                return True
            else:
                print(f"❌ Cookie 無效 (HTTP {response.status_code})")
                return False
                
        except Exception as e:
            print(f"❌ Cookie 驗證失敗: {e}")
            return False
        
    def login_and_get_cookies(self, headless: bool = True) -> Dict[str, Any]:
        """
        使用 Selenium 登入 Aedyn 並取得 Cookie 和 JWT Token
        
        Args:
            headless: 是否使用無頭模式(預設 True)
            
        Returns:
            dict: 包含 cookies 和 jwt_token 的字典
        """
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        if headless:
            options.add_argument("--headless=new")

        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, 30)

            print("🔐 正在嘗試登入 Aedyn...")
            driver.get(LOGIN_URL)

            # 等待登入頁面載入
            try:
                user_el = wait.until(EC.visibility_of_element_located((By.ID, "username")))
                pwd_el = wait.until(EC.visibility_of_element_located((By.ID, "password")))

                # 輸入帳密
                user_el.clear()
                user_el.send_keys(self.username)

                pwd_el.clear()
                pwd_el.send_keys(self.password)

                # 送出登入
                pwd_el.send_keys(Keys.ENTER)

                # 等待跳轉到主頁面
                wait.until(lambda d: "aedyn.weathernews.com" in d.current_url and "redirect_uri" not in d.current_url)
                
                print("✅ 登入成功,正在取得 Cookie...")
                
            except TimeoutException:
                # 可能已經登入過了,直接檢查是否在正確頁面
                if "aedyn.weathernews.com" in driver.current_url:
                    print("✅ 檢測到已登入狀態")
                else:
                    raise Exception("登入流程超時")

            # 等待頁面完全載入
            time.sleep(5)
            
            # 從瀏覽器取得所有 Cookie
            selenium_cookies = driver.get_cookies()
            cookie_dict = {cookie['name']: cookie['value'] for cookie in selenium_cookies}
            
            print(f"✅ 已從瀏覽器取得 {len(cookie_dict)} 個 Cookie")
            
            # 訪問 API 端點來觸發並取得 JWT Token
            print("🔍 正在取得 JWT Token...")
            
            # 先訪問主頁確保 session 建立
            driver.get("https://aedyn.weathernews.com/")
            time.sleep(2)
            
            # 訪問 user API 來取得 JWT
            driver.get("https://aedyn.weathernews.com/api/account/user")
            time.sleep(2)
            
            # 再次取得 Cookie(可能有更新)
            selenium_cookies = driver.get_cookies()
            for cookie in selenium_cookies:
                cookie_dict[cookie['name']] = cookie['value']
            
            # 嘗試從 localStorage 取得 JWT Token
            try:
                jwt_token = driver.execute_script("return localStorage.getItem('jwt') || sessionStorage.getItem('jwt');")
                if jwt_token:
                    self.jwt_token = jwt_token
                    print(f"✅ 已取得 JWT Token (長度: {len(jwt_token)})")
            except Exception:
                print("⚠️ 無法從 localStorage 取得 JWT Token")
            
            # 如果 localStorage 沒有,嘗試從 Cookie 中找 jwt
            if not self.jwt_token and 'jwt' in cookie_dict:
                self.jwt_token = cookie_dict['jwt']
                print(f"✅ 已從 Cookie 取得 JWT Token (長度: {len(self.jwt_token)})")
            
            # 使用 requests 驗證 Cookie
            if cookie_dict:
                print("🔍 正在驗證 Cookie 有效性...")
                cookie_string = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": cookie_string,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://aedyn.weathernews.com/"
                }
                
                if self.jwt_token:
                    headers["json_web_token"] = self.jwt_token
                
                try:
                    response = requests.get(
                        "https://aedyn.weathernews.com/api/account/user",
                        headers=headers,
                        timeout=10,
                        verify=False
                    )
                    
                    if response.status_code == 200:
                        user_data = response.json()
                        user_name = user_data.get('user_disp_name', 'Unknown User')
                        print(f"✅ Cookie 驗證成功!使用者: {user_name}")
                    else:
                        print(f"⚠️ Cookie 驗證失敗 (HTTP {response.status_code})")
                        
                except Exception as e:
                    print(f"⚠️ Cookie 驗證時發生錯誤: {e}")
            
            self.cookies = cookie_dict
            self.cookie_timestamp = datetime.now()
            
            # 儲存 Cookie 到檔案
            self.save_cookies()
            
            return {
                'cookies': cookie_dict,
                'jwt_token': self.jwt_token
            }

        except Exception as e:
            print(f"❌ 登入失敗: {repr(e)}")
            if driver:
                screenshot_path = "login_error.png"
                driver.save_screenshot(screenshot_path)
                print(f"已儲存錯誤截圖: {screenshot_path}")
                print(f"當前網址: {driver.current_url}")
            raise

        finally:
            if driver:
                driver.quit()

    def get_cookie_string(self) -> str:
        """
        將 Cookie 字典轉換成 HTTP Header 格式的字串
        
        Returns:
            str: Cookie 字串
        """
        if not self.cookies:
            return ""
        return "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
    
    def get_headers(self) -> Dict[str, str]:
        """
        取得完整的 HTTP Headers(包含 Cookie 和 JWT Token)
        
        Returns:
            dict: HTTP Headers
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh-CN;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
            "Referer": "https://aedyn.weathernews.com/",
            "sec-ch-ua": "\"Google Chrome\";v=\"120\", \"Chromium\";v=\"120\", \"Not_A Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin"
        }
        
        if self.cookies:
            headers["Cookie"] = self.get_cookie_string()
        
        if self.jwt_token:
            headers["json_web_token"] = self.jwt_token
            
        return headers


class WeatherDatabase:
    """氣象資料庫管理類別"""
    
    def __init__(self, db_file: str = DB_FILE):
        """
        初始化資料庫
        
        Args:
            db_file: 資料庫檔案路徑
        """
        self.db_file = db_file
        self.init_database()

    def init_database(self) -> None:
        """建立資料庫表格"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            
            # 原有的 48 小時預報表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    port_name TEXT NOT NULL,
                    wni_port_code TEXT NOT NULL,
                    whl_port_code TEXT,
                    country TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    issued_time TEXT NOT NULL,
                    content TEXT NOT NULL,
                    download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(whl_port_code, issued_time)
                )
            ''')
            
            # ✅ 新增: 7 天預報表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_data_7d (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    port_name TEXT NOT NULL,
                    wni_port_code TEXT NOT NULL,
                    whl_port_code TEXT,
                    country TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    issued_time TEXT NOT NULL,
                    content TEXT NOT NULL,
                    download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(whl_port_code, issued_time)
                )
            ''')
            
            conn.commit()

    def get_latest_content(self, whl_port_code: str) -> Optional[Tuple[str, str, str]]:
        """
        取得指定港口最新的氣象內容 (48小時)
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            Tuple[content, issued_time, port_name] 或 None
        """
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT content, issued_time, port_name FROM weather_data 
                WHERE whl_port_code = ? 
                ORDER BY issued_time DESC 
                LIMIT 1
            ''', (whl_port_code,))
            return cursor.fetchone()

    # ✅ 新增: 取得 7 天預報內容
    def get_latest_content_7d(self, whl_port_code: str) -> Optional[Tuple[str, str, str]]:
        """
        取得指定港口最新的 7 天氣象內容
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            Tuple[content, issued_time, port_name] 或 None
        """
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT content, issued_time, port_name FROM weather_data_7d 
                WHERE whl_port_code = ? 
                ORDER BY issued_time DESC 
                LIMIT 1
            ''', (whl_port_code,))
            return cursor.fetchone()

    def get_latest_time(self, whl_port_code: str) -> Optional[str]:
        """
        取得指定港口最新的發布時間 (48小時)
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            發布時間字串或 None
        """
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT issued_time FROM weather_data WHERE whl_port_code = ? ORDER BY issued_time DESC LIMIT 1',
                (whl_port_code,)
            )
            res = cursor.fetchone()
            return res[0] if res else None

    # ✅ 新增: 取得 7 天預報最新時間
    def get_latest_time_7d(self, whl_port_code: str) -> Optional[str]:
        """
        取得指定港口最新的 7 天預報發布時間
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            發布時間字串或 None
        """
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT issued_time FROM weather_data_7d WHERE whl_port_code = ? ORDER BY issued_time DESC LIMIT 1',
                (whl_port_code,)
            )
            res = cursor.fetchone()
            return res[0] if res else None

    def save_weather(self, wni_port_code: str, whl_port_code: str, port_name: str, 
                    port_id: str, country: str, issued_time: str, content: str) -> bool:
        """
        儲存氣象資料到資料庫 (48小時)
        
        Args:
            wni_port_code: WNI 港口代碼
            whl_port_code: 萬海港口代碼
            port_name: 港口名稱
            port_id: 站點 ID
            country: 國家
            issued_time: 發布時間
            content: 氣象內容
            
        Returns:
            bool: 儲存成功返回 True
        """
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO weather_data 
                    (port_name, wni_port_code, whl_port_code, country, station_id, issued_time, content, download_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (port_name, wni_port_code, whl_port_code, country, port_id, issued_time, content))
                conn.commit()
            return True
        except Exception as e:
            print(f"❌ 資料庫錯誤: {e}")
            return False

    # ✅ 新增: 儲存 7 天預報資料
    def save_weather_7d(self, wni_port_code: str, whl_port_code: str, port_name: str, 
                       port_id: str, country: str, issued_time: str, content: str) -> bool:
        """
        儲存 7 天氣象資料到資料庫
        
        Args:
            wni_port_code: WNI 港口代碼
            whl_port_code: 萬海港口代碼
            port_name: 港口名稱
            port_id: 站點 ID
            country: 國家
            issued_time: 發布時間
            content: 氣象內容
            
        Returns:
            bool: 儲存成功返回 True
        """
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO weather_data_7d 
                    (port_name, wni_port_code, whl_port_code, country, station_id, issued_time, content, download_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (port_name, wni_port_code, whl_port_code, country, port_id, issued_time, content))
                conn.commit()
            return True
        except Exception as e:
            print(f"❌ 資料庫錯誤: {e}")
            return False


class PortWeatherCrawler:
    """港口氣象資料爬蟲"""
    
    def __init__(self, username: str, password: str, excel_path: str = EXCEL_FILE_WANHAI, auto_login: bool = False):
        """
        初始化爬蟲
        
        Args:
            username: Aedyn 帳號
            password: Aedyn 密碼
            excel_path: Excel 檔案路徑
            auto_login: 是否強制重新登入
        """
        self.excel_path = excel_path
        self.db = WeatherDatabase()
        self.session = self._create_session()
        self.port_map: Dict[str, Dict[str, Any]] = {}
        self.ports_data: Dict[str, Dict[str, Any]] = {}
        self.port_list: List[str] = []
        self.login_manager = AedynLoginManager(username, password)
        self.headers: Dict[str, str] = {}
        
        # 載入港口資料
        self._load_port_map()
        
        # 智能登入:先嘗試載入舊 Cookie,如果無效才重新登入
        self._smart_login(force_login=auto_login)

    def _load_port_map(self) -> None:
        """一次性讀取 Excel 並載入所有港口資訊(含經緯度)"""
        if not os.path.exists(self.excel_path):
            print(f"⚠️ 找不到 {self.excel_path},請確認檔案位置。")
            return

        try:
            print("⏳ 正在載入港口資料...")
            df = pd.read_excel(self.excel_path, sheet_name='all_ports_list')
            
            # 清理欄位名稱(去除前後空格)
            df.columns = df.columns.str.strip()
            
            for _, row in df.iterrows():
                code = str(row['Port_Code_5']).strip()
                obj_id = str(row['Station ID (Object_ID)']).strip()
                
                if code and obj_id and obj_id != 'nan':
                    # 處理經緯度:先轉為 float,若為 NaN 則設為 0.0
                    try:
                        lat = float(row.get('Lat', 0.0))
                        lat = 0.0 if np.isnan(lat) else lat
                    except (ValueError, TypeError):
                        lat = 0.0
                        
                    try:
                        lon = float(row.get('Lon', 0.0))
                        lon = 0.0 if np.isnan(lon) else lon
                    except (ValueError, TypeError):
                        lon = 0.0

                    port_info = {
                        'id': obj_id,
                        'name': str(row['Port Name']).strip(),
                        'wni_code': str(row.get('WNI Port Code', code)).strip(),
                        'country': str(row.get('Country', 'N/A')),
                        'latitude': lat,
                        'longitude': lon
                    }
                    
                    self.port_map[code] = port_info
                    
                    # 同時建立 ports_data(與 port_map 相同結構)
                    self.ports_data[code] = {
                        'port_name': port_info['name'],
                        'wni_code': port_info['wni_code'],
                        'country': port_info['country'],
                        'station_id': port_info['id'],
                        'latitude': lat,
                        'longitude': lon
                    }
                    
                    self.port_list.append(code)
            
            print(f"✅ 已載入 {len(self.port_map)} 個港口資料")
            
        except Exception as e:
            print(f"❌ 讀取 Excel 失敗: {e}")
            import traceback
            traceback.print_exc()

    def _smart_login(self, force_login: bool = False) -> None:
        """
        智能登入:只在需要時才登入
        
        Args:
            force_login: 是否強制重新登入
        """
        if force_login:
            print("🔄 強制重新登入...")
            self.refresh_cookies()
            return
        
        print("\n🔍 檢查 Cookie 狀態...")
        
        # 1. 嘗試載入已儲存的 Cookie
        if self.login_manager.load_cookies():
            # 2. 驗證 Cookie 是否有效
            if self.login_manager.verify_cookies():
                print("✅ 使用已儲存的 Cookie")
                self.headers = self.login_manager.get_headers()
                return
            else:
                print("⚠️ Cookie 已失效,需要重新登入")
        
        # 3. Cookie 不存在或已失效,執行登入
        print("🔐 執行登入流程...")
        self.refresh_cookies()

    def _create_session(self) -> requests.Session:
        """
        建立 requests session 並設定重試機制
        
        Returns:
            requests.Session: 設定好的 session
        """
        session = requests.Session()
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def refresh_cookies(self, headless: bool = True) -> bool:
        """
        重新登入並更新 Cookie 和 JWT Token
        
        Args:
            headless: 是否使用無頭模式
            
        Returns:
            bool: 更新成功返回 True
        """
        try:
            print("\n🔄 正在更新 Cookie 和 JWT Token...")
            result = self.login_manager.login_and_get_cookies(headless=headless)
            self.headers = self.login_manager.get_headers()
            
            print("\n📋 取得的 Headers:")
            print(f"   Cookie 數量: {len(result['cookies'])}")
            print(f"   JWT Token: {'✅ 已取得' if result['jwt_token'] else '❌ 未取得'}")
            
            # 顯示部分 Cookie 名稱
            if result['cookies']:
                cookie_names = list(result['cookies'].keys())[:5]
                print(f"   Cookie 範例: {', '.join(cookie_names)}...")
            
            print("✅ Headers 已更新\n")
            return True
        except Exception as e:
            print(f"❌ Cookie 更新失敗: {e}")
            return False

    def get_all_ports_display(self) -> List[str]:
        """
        回傳給 UI 下拉選單用的清單
        
        Returns:
            List[str]: 港口代碼和名稱的列表
        """
        if not self.port_map:
            return []
        return [f"{code} - {info['name']}" for code, info in self.port_map.items()]

    def parse_issued_time(self, content: str) -> str:
        """
        從氣象內容中解析發布時間
        
        Args:
            content: 氣象內容
            
        Returns:
            str: 發布時間字串
        """
        for line in content.splitlines():
            if line.strip().startswith("ISSUED AT:"):
                return line.split(":", 1)[1].strip().replace(" UTC", "").replace(" ", "_")
        return datetime.now().strftime("%Y%m%d%H%M")

    def fetch_port_data(self, whl_port_code: str, retry_login: bool = True) -> Tuple[bool, str]:
        """
        下載指定港口的氣象資料 (48小時)
        
        Args:
            whl_port_code: 港口代碼
            retry_login: 當遇到權限錯誤時是否自動重新登入
            
        Returns:
            Tuple[bool, str]: (成功與否, 訊息)
        """
        if whl_port_code not in self.port_map:
            return False, f"找不到港口代碼: {whl_port_code}"

        p_info = self.port_map[whl_port_code]
        url = f"https://aedyn.weathernews.com/api/business/sea/portstatus/content/48h/{p_info['id']}.txt"
        
        print(f"📡 正在下載 {whl_port_code} ({p_info['name']}) - 48小時預報...")
        
        try:
            response = self.session.get(url, headers=self.headers, verify=False, timeout=TIMEOUT)
            
            if response.status_code == 200:
                content = response.text
                issued_time = self.parse_issued_time(content)
                cached_time = self.db.get_latest_time(whl_port_code)

                if cached_time == issued_time:
                    return True, f"48h 天氣資料已是最新 ({issued_time})"
                
                if self.db.save_weather(
                    p_info['wni_code'], whl_port_code, p_info['name'], 
                    p_info['id'], p_info['country'], issued_time, content
                ):
                    return True, f"48h 更新成功 ({issued_time})"
                else:
                    return False, "資料庫寫入失敗"
                    
            elif response.status_code in [401, 403]:
                # Cookie 過期,嘗試重新登入
                if retry_login:
                    print("⚠️ Cookie 已過期,正在重新登入...")
                    if self.refresh_cookies():
                        # 重新嘗試下載(但不再重試登入,避免無限迴圈)
                        return self.fetch_port_data(whl_port_code, retry_login=False)
                return False, f"權限不足 (HTTP {response.status_code}) - Cookie 已過期"
            else:
                return False, f"下載失敗 (HTTP {response.status_code})"
                
        except requests.exceptions.Timeout:
            return False, f"連線逾時(超過 {TIMEOUT} 秒)"
        except Exception as e:
            return False, f"連線錯誤: {str(e)}"

    # ✅ 新增: 下載 7 天預報資料
    def fetch_port_data_7d(self, whl_port_code: str, retry_login: bool = True) -> Tuple[bool, str]:
        """
        下載指定港口的 7 天氣象資料
        
        Args:
            whl_port_code: 港口代碼
            retry_login: 當遇到權限錯誤時是否自動重新登入
            
        Returns:
            Tuple[bool, str]: (成功與否, 訊息)
        """
        if whl_port_code not in self.port_map:
            return False, f"找不到港口代碼: {whl_port_code}"

        p_info = self.port_map[whl_port_code]
        url = f"https://aedyn.weathernews.com/api/business/sea/portstatus/content/7d/{p_info['id']}.txt"
        
        print(f"📡 正在下載 {whl_port_code} ({p_info['name']}) - 7天預報...")
        
        try:
            response = self.session.get(url, headers=self.headers, verify=False, timeout=TIMEOUT)
            
            if response.status_code == 200:
                content = response.text
                issued_time = self.parse_issued_time(content)
                cached_time = self.db.get_latest_time_7d(whl_port_code)

                if cached_time == issued_time:
                    return True, f"7d 天氣資料已是最新 ({issued_time})"
                
                if self.db.save_weather_7d(
                    p_info['wni_code'], whl_port_code, p_info['name'], 
                    p_info['id'], p_info['country'], issued_time, content
                ):
                    return True, f"7d 更新成功 ({issued_time})"
                else:
                    return False, "資料庫寫入失敗"
                    
            elif response.status_code in [401, 403]:
                # Cookie 過期,嘗試重新登入
                if retry_login:
                    print("⚠️ Cookie 已過期,正在重新登入...")
                    if self.refresh_cookies():
                        # 重新嘗試下載(但不再重試登入,避免無限迴圈)
                        return self.fetch_port_data_7d(whl_port_code, retry_login=False)
                return False, f"權限不足 (HTTP {response.status_code}) - Cookie 已過期"
            else:
                return False, f"下載失敗 (HTTP {response.status_code})"
                
        except requests.exceptions.Timeout:
            return False, f"連線逾時(超過 {TIMEOUT} 秒)"
        except Exception as e:
            return False, f"連線錯誤: {str(e)}"

    def fetch_all_ports(self) -> Dict[str, int]:
        """
        批次下載所有港口資料 (48小時)
        
        Returns:
            Dict[str, int]: 統計結果 {'success': n, 'skip': n, 'fail': n}
        """
        print(f"\n🚀 開始更新全部港口資訊 (48小時),預計更新 {len(self.port_list)} 個港口資料...\n")
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        for i, whl_port_code in enumerate(self.port_list, 1):
            print(f"[{i}/{len(self.port_list)}] ", end="")
            success, message = self.fetch_port_data(whl_port_code)
            
            if success:
                if "已是最新" in message:
                    skip_count += 1
                else:
                    success_count += 1
            else:
                fail_count += 1
                
            print(f"   {message}")
        
        print(f"\n📊 48小時預報下載完成!")
        print(f"   ✅ 成功: {success_count}")
        print(f"   ⏭️  略過: {skip_count}")
        print(f"   ❌ 失敗: {fail_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'fail': fail_count
        }

    # ✅ 新增: 批次下載所有港口 7 天預報
    def fetch_all_ports_7d(self) -> Dict[str, int]:
        """
        批次下載所有港口 7 天預報資料
        
        Returns:
            Dict[str, int]: 統計結果 {'success': n, 'skip': n, 'fail': n}
        """
        print(f"\n🚀 開始更新全部港口資訊 (7天預報),預計更新 {len(self.port_list)} 個港口資料...\n")
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        for i, whl_port_code in enumerate(self.port_list, 1):
            print(f"[{i}/{len(self.port_list)}] ", end="")
            success, message = self.fetch_port_data_7d(whl_port_code)
            
            if success:
                if "已是最新" in message:
                    skip_count += 1
                else:
                    success_count += 1
            else:
                fail_count += 1
                
            print(f"   {message}")
        
        print(f"\n📊 7天預報下載完成!")
        print(f"   ✅ 成功: {success_count}")
        print(f"   ⏭️  略過: {skip_count}")
        print(f"   ❌ 失敗: {fail_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'fail': fail_count
        }

    # ✅ 新增: 同時下載 48h 和 7d 預報
    def fetch_all_ports_both(self) -> Dict[str, Dict[str, int]]:
        """
        批次下載所有港口的 48小時 和 7天 預報資料
        
        Returns:
            Dict[str, Dict[str, int]]: 統計結果 {'48h': {...}, '7d': {...}}
        """
        print(f"\n🚀 開始更新全部港口資訊 (48h + 7d),預計更新 {len(self.port_list)} 個港口資料...\n")
        
        # 48小時預報統計
        stats_48h = {'success': 0, 'skip': 0, 'fail': 0}
        # 7天預報統計
        stats_7d = {'success': 0, 'skip': 0, 'fail': 0}
        
        for i, whl_port_code in enumerate(self.port_list, 1):
            print(f"\n[{i}/{len(self.port_list)}] {whl_port_code}")
            
            # 下載 48小時預報
            success_48h, message_48h = self.fetch_port_data(whl_port_code)
            if success_48h:
                if "已是最新" in message_48h:
                    stats_48h['skip'] += 1
                else:
                    stats_48h['success'] += 1
            else:
                stats_48h['fail'] += 1
            print(f"   48h: {message_48h}")
            
            # 下載 7天預報
            success_7d, message_7d = self.fetch_port_data_7d(whl_port_code)
            if success_7d:
                if "已是最新" in message_7d:
                    stats_7d['skip'] += 1
                else:
                    stats_7d['success'] += 1
            else:
                stats_7d['fail'] += 1
            print(f"   7d:  {message_7d}")
        
        print(f"\n📊 全部下載完成!")
        print(f"\n48小時預報:")
        print(f"   ✅ 成功: {stats_48h['success']}")
        print(f"   ⏭️  略過: {stats_48h['skip']}")
        print(f"   ❌ 失敗: {stats_48h['fail']}")
        
        print(f"\n7天預報:")
        print(f"   ✅ 成功: {stats_7d['success']}")
        print(f"   ⏭️  略過: {stats_7d['skip']}")
        print(f"   ❌ 失敗: {stats_7d['fail']}")
        
        return {
            '48h': stats_48h,
            '7d': stats_7d
        }

    def test_api_connection(self) -> None:
        """測試 API 連線和認證狀態"""
        print("\n🧪 測試 API 連線...")
        
        test_urls = [
            "https://aedyn.weathernews.com/api/account/user",
            "https://aedyn.weathernews.com/"
        ]
        
        for url in test_urls:
            try:
                print(f"\n測試: {url}")
                response = self.session.get(url, headers=self.headers, verify=False, timeout=10)
                print(f"   狀態碼: {response.status_code}")
                
                if response.status_code == 200:
                    content_type = response.headers.get('Content-Type', '')
                    if 'application/json' in content_type:
                        data = response.json()
                        print(f"   回應: {json.dumps(data, indent=2, ensure_ascii=False)[:200]}...")
                    else:
                        print(f"   回應長度: {len(response.text)} bytes")
                    print("   ✅ 連線成功")
                else:
                    print("   ❌ 連線失敗")
                    
            except Exception as e:
                print(f"   ❌ 錯誤: {e}")

    def get_data_from_db(self, whl_port_code: str) -> Optional[Tuple[str, str, str]]:
        """
        從資料庫讀取指定港口的最新氣象內容 (48小時)
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            Tuple[content, issued_time, port_name] 或 None
        """
        return self.db.get_latest_content(whl_port_code)

    # ✅ 新增: 從資料庫讀取 7 天預報
    def get_data_from_db_7d(self, whl_port_code: str) -> Optional[Tuple[str, str, str]]:
        """
        從資料庫讀取指定港口的最新 7 天氣象內容
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            Tuple[content, issued_time, port_name] 或 None
        """
        return self.db.get_latest_content_7d(whl_port_code)

    def get_port_info(self, whl_port_code: str) -> Optional[Dict[str, Any]]:
        """
        取得港口完整資訊
        
        Args:
            whl_port_code: 港口代碼
            
        Returns:
            Dict: 港口資訊字典或 None
        """
        if whl_port_code not in self.port_map:
            print(f"❌ 港口代碼 {whl_port_code} 不在 port_map 中")
            return None
        
        info = self.port_map[whl_port_code]
        
        return {
            'port_name': info['name'],
            'whl_port_code': whl_port_code,
            'wni_port_code': info['wni_code'],
            'country': info['country'],
            'station_id': info['id'],
            'latitude': info.get('latitude', 0.0),
            'longitude': info.get('longitude', 0.0)
        }


# ================= 使用範例 =================
if __name__ == "__main__":
    # 從環境變數或設定檔讀取帳號密碼(更安全的做法)
    AEDYN_USERNAME = os.getenv('AEDYN_USERNAME', 'your_username@example.com')
    AEDYN_PASSWORD = os.getenv('AEDYN_PASSWORD', 'your_password')
    
    # 初始化爬蟲(會自動檢查 Cookie,只在需要時才登入)
    print("=" * 60)
    print("初始化爬蟲系統")
    print("=" * 60)
    
    crawler = PortWeatherCrawler(
        username=AEDYN_USERNAME,
        password=AEDYN_PASSWORD,
        auto_login=False  # False 表示智能登入
    )
    
    # 測試 API 連線
    crawler.test_api_connection()
    
    # 範例 1: 下載單一港口 48小時預報
    print("\n" + "=" * 60)
    print("範例 1: 下載單一港口 48小時預報")
    print("=" * 60)
    success, message = crawler.fetch_port_data("TWKHH")
    print(f"結果: {message}")
    
    # ✅ 範例 2: 下載單一港口 7天預報
    print("\n" + "=" * 60)
    print("範例 2: 下載單一港口 7天預報")
    print("=" * 60)
    success, message = crawler.fetch_port_data_7d("TWKHH")
    print(f"結果: {message}")
    
    # 範例 3: 從資料庫讀取 48小時預報
    print("\n" + "=" * 60)
    print("範例 3: 從資料庫讀取 48小時預報")
    print("=" * 60)
    data = crawler.get_data_from_db("TWKHH")
    if data:
        content, issued_time, port_name = data
        print(f"港口: {port_name}")
        print(f"發布時間: {issued_time}")
        print(f"內容預覽: {content[:200]}...")
    else:
        print("❌ 找不到資料")
    
    # ✅ 範例 4: 從資料庫讀取 7天預報
    print("\n" + "=" * 60)
    print("範例 4: 從資料庫讀取 7天預報")
    print("=" * 60)
    data_7d = crawler.get_data_from_db_7d("TWKHH")
    if data_7d:
        content, issued_time, port_name = data_7d
        print(f"港口: {port_name}")
        print(f"發布時間: {issued_time}")
        print(f"內容預覽: {content[:200]}...")
    else:
        print("❌ 找不到資料")
    
    # 範例 5: 取得港口資訊
    print("\n" + "=" * 60)
    print("範例 5: 取得港口資訊")
    print("=" * 60)
    port_info = crawler.get_port_info("TWKHH")
    if port_info:
        print(json.dumps(port_info, indent=2, ensure_ascii=False))
    
    # ✅ 範例 6: 批次下載所有港口 7天預報
    print("\n" + "=" * 60)
    print("範例 6: 批次下載所有港口 7天預報 (取消註解以執行)")
    print("=" * 60)
    # stats_7d = crawler.fetch_all_ports_7d()
    
    # ✅ 範例 7: 同時下載所有港口的 48h 和 7d 預報
    print("\n" + "=" * 60)
    print("範例 7: 同時下載所有港口的 48h 和 7d 預報 (取消註解以執行)")
    print("=" * 60)
    # stats_both = crawler.fetch_all_ports_both()
