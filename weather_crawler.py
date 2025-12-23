import requests
import sqlite3
import pandas as pd
import os
import re
import json
import pickle
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
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
EXCEL_FILE_WNI = 'WNI_all_ports_list.xlsx'
EXCEL_FILE_Wanhai = 'WHL_all_ports_list.xlsx'
COOKIE_FILE = 'aedyn_cookies.pkl'  # Cookie 儲存檔案
TIMEOUT = 30
MAX_RETRIES = 3

LOGIN_URL = (
    "https://idp.aedyn.wni.com/auth/realms/aedyn/protocol/openid-connect/auth"
    "?response_type=id_token%20token&scope=openid&client_id=aedyn"
    "&state=cZr_CP7VqEq2p8j6D_a_YrL2ucA"
    "&redirect_uri=https%3A%2F%2Faedyn.weathernews.com%2Fhttpd-auth%2Fredirect_uri"
    "&nonce=cwGprMflnWRdzaLvLMkCMI2az5vjS79XdTW0gtUulwo"
)

# Aedyn 帳號密碼
AEDYN_USERNAME = "harry_chung@wanhai.com"
AEDYN_PASSWORD = "wanhai888"


class AedynLoginManager:
    """負責自動登入 Aedyn 並取得最新 Cookie 和 JWT Token"""
    
    def __init__(self, username: str, password: str, cookie_file: str = COOKIE_FILE):
        self.username = username
        self.password = password
        self.cookie_file = cookie_file
        self.cookies = {}
        self.jwt_token = ""
        self.cookie_timestamp = None
        
    def save_cookies(self):
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
        """從檔案載入 Cookie"""
        if not os.path.exists(self.cookie_file):
            print(f"ℹ️ Cookie 檔案不存在: {self.cookie_file}")
            return False
        
        try:
            with open(self.cookie_file, 'rb') as f:
                data = pickle.load(f)
            
            self.cookies = data.get('cookies', {})
            self.jwt_token = data.get('jwt_token', '')
            self.cookie_timestamp = data.get('timestamp')
            
            # 檢查 Cookie 是否過期（超過 24 小時）
            if self.cookie_timestamp:
                age = datetime.now() - self.cookie_timestamp
                print(f"ℹ️ Cookie 年齡: {age}")
                
                if age > timedelta(hours=24):
                    print("⚠️ Cookie 已過期（超過 24 小時）")
                    return False
            
            print(f"✅ 已載入 Cookie (數量: {len(self.cookies)})")
            return True
            
        except Exception as e:
            print(f"⚠️ Cookie 載入失敗: {e}")
            return False
    
    def verify_cookies(self) -> bool:
        """驗證 Cookie 是否有效"""
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
                print(f"✅ Cookie 有效！使用者: {user_data.get('user_disp_name', 'Unknown')}")
                return True
            else:
                print(f"❌ Cookie 無效 (HTTP {response.status_code})")
                return False
                
        except Exception as e:
            print(f"❌ Cookie 驗證失敗: {e}")
            return False
        
    def login_and_get_cookies(self, headless: bool = True) -> dict:
        """
        使用 Selenium 登入 Aedyn 並取得 Cookie 和 JWT Token
        
        Args:
            headless: 是否使用無頭模式（預設 True）
            
        Returns:
            dict: 包含 cookies 和 jwt_token 的字典
        """
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--headless")  # 無頭模式 (無視窗執行)
        options.add_argument("--disable-dev-shm-usage")
        
        
        if headless:
            options.add_argument("--headless=new")

        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, 30)

            print("🔐 正在登入 Aedyn...")
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
                
                print("✅ 登入成功，正在取得 Cookie...")
                
            except TimeoutException:
                # 可能已經登入過了，直接檢查是否在正確頁面
                if "aedyn.weathernews.com" in driver.current_url:
                    print("✅ 檢測到已登入狀態")
                else:
                    raise Exception("登入流程超時")

            # 等待頁面完全載入
            time.sleep(2)
            
            # 方法 1: 從瀏覽器取得所有 Cookie
            selenium_cookies = driver.get_cookies()
            cookie_dict = {}
            for cookie in selenium_cookies:
                cookie_dict[cookie['name']] = cookie['value']
            
            print(f"✅ 已從瀏覽器取得 {len(cookie_dict)} 個 Cookie")
            
            # 方法 2: 訪問 API 端點來觸發並取得 JWT Token
            print("🔍 正在取得 JWT Token...")
            
            # 先訪問主頁確保 session 建立
            driver.get("https://aedyn.weathernews.com/")
            time.sleep(1)
            
            # 訪問 user API 來取得 JWT
            driver.get("https://aedyn.weathernews.com/api/account/user")
            time.sleep(1)
            
            # 再次取得 Cookie（可能有更新）
            selenium_cookies = driver.get_cookies()
            for cookie in selenium_cookies:
                cookie_dict[cookie['name']] = cookie['value']
            
            # 嘗試從 localStorage 取得 JWT Token
            try:
                jwt_token = driver.execute_script("return localStorage.getItem('jwt') || sessionStorage.getItem('jwt');")
                if jwt_token:
                    self.jwt_token = jwt_token
                    print(f"✅ 已取得 JWT Token (長度: {len(jwt_token)})")
            except:
                print("⚠️ 無法從 localStorage 取得 JWT Token")
            
            # 如果 localStorage 沒有，嘗試從 Cookie 中找 jwt
            if not self.jwt_token and 'jwt' in cookie_dict:
                self.jwt_token = cookie_dict['jwt']
                print(f"✅ 已從 Cookie 取得 JWT Token (長度: {len(self.jwt_token)})")
            
            # 方法 3: 使用 requests 訪問 API 來驗證和取得完整資訊
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
                        print(f"✅ Cookie 驗證成功！使用者: {user_data.get('user_disp_name', 'Unknown')}")
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
                driver.save_screenshot("login_error.png")
                print("已儲存錯誤截圖: login_error.png")
                print(f"當前網址: {driver.current_url}")
            raise

        finally:
            if driver:
                driver.quit()

    def get_cookie_string(self) -> str:
        """將 Cookie 字典轉換成 HTTP Header 格式的字串"""
        if not self.cookies:
            return ""
        return "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
    
    def get_headers(self) -> dict:
        """取得完整的 HTTP Headers（包含 Cookie 和 JWT Token）"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh-CN;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
            "Referer": "https://aedyn.weathernews.com/",
            "sec-ch-ua": "\"Google Chrome\";v=\"143\", \"Chromium\";v=\"143\", \"Not A(Brand\";v=\"24\"",
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
    def __init__(self, db_file=DB_FILE):
        self.db_file = db_file
        self.init_database()

    def init_database(self):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
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
            conn.commit()

    def get_latest_content(self, whl_port_code):
        """取得指定港口最新的氣象內容 (回傳: content, issued_time, port_name)"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT content, issued_time, port_name FROM weather_data 
                WHERE whl_port_code = ? 
                ORDER BY issued_time DESC 
                LIMIT 1
            ''', (whl_port_code,))
            return cursor.fetchone()

    def get_latest_time(self, whl_port_code):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT issued_time FROM weather_data WHERE whl_port_code = ? ORDER BY issued_time DESC LIMIT 1', (whl_port_code,))
            res = cursor.fetchone()
            return res[0] if res else None

    def save_weather(self, wni_port_code, whl_port_code, port_name, port_id, country, issued_time, content):
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
            print(f"DB Error: {e}")
            return False


class PortWeatherCrawler:
    def __init__(self, excel_path=EXCEL_FILE_Wanhai, auto_login=False):
        self.excel_path = excel_path
        self.db = WeatherDatabase()
        self.session = self._create_session()
        self.port_map = {}
        self.port_list = []
        self.login_manager = AedynLoginManager(AEDYN_USERNAME, AEDYN_PASSWORD)
        self.headers = {}
        
        # 載入港口資料
        self._load_port_map()
        
        # 智能登入：先嘗試載入舊 Cookie，如果無效才重新登入
        self._smart_login(force_login=auto_login)

    def _smart_login(self, force_login=False):
        """智能登入：只在需要時才登入"""
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
                print("⚠️ Cookie 已失效，需要重新登入")
        
        # 3. Cookie 不存在或已失效，執行登入
        print("🔐 執行登入流程...")
        self.refresh_cookies()

    def _create_session(self):
        session = requests.Session()
        retry = Retry(total=MAX_RETRIES, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def refresh_cookies(self, headless=True):
        """重新登入並更新 Cookie 和 JWT Token"""
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

    def _load_port_map(self):
        """一次性讀取 Excel 並載入所有欄位資訊（含經緯度）"""
        if not os.path.exists(self.excel_path):
            print(f"⚠️ 找不到 {self.excel_path}，請確認檔案位置。")
            return

        try:
            print("⏳ 正在載入港口資料...")
            df = pd.read_excel(self.excel_path, sheet_name='all_ports_list')
            
            # 清理欄位名稱（去除前後空格）
            df.columns = df.columns.str.strip()
            
            for _, row in df.iterrows():
                code = str(row['Port_Code_5']).strip()
                obj_id = str(row['Station ID (Object_ID)']).strip()
                
                if code and obj_id and obj_id != 'nan':
                    # 處理經緯度：先轉為 float，若為 NaN 則設為 0.0
                    try:
                        lat = float(row.get('Lat', 0.0))
                        if np.isnan(lat): lat = 0.0
                    except:
                        lat = 0.0
                        
                    try:
                        lon = float(row.get('Lon', 0.0))
                        if np.isnan(lon): lon = 0.0
                    except:
                        lon = 0.0

                    self.port_map[code] = {
                        'id': obj_id,
                        'name': str(row['Port Name']).strip(),
                        'wni_code': str(row.get('WNI Port Code', code)).strip(),
                        'country': str(row.get('Country', 'N/A')),
                        'latitude': lat,
                        'longitude': lon
                    }
                    self.port_list.append(code)
            
            print(f"✅ 已載入 {len(self.port_map)} 個港口資料")
            
        except Exception as e:
            print(f"❌ 讀取 Excel 失敗: {e}")
            import traceback
            traceback.print_exc()

    def get_all_ports_display(self):
        """回傳給 UI 下拉選單用的清單"""
        if not self.port_map:
            return []
        return [f"{code} - {info['name']}" for code, info in self.port_map.items()]

    def parse_issued_time(self, content):
        for line in content.splitlines():
            if line.strip().startswith("ISSUED AT:"):
                return line.split(":")[1].strip().replace(" UTC", "").replace(" ", "_")
        return datetime.now().strftime("%Y%m%d%H%M")

    def fetch_port_data(self, whl_port_code, retry_login=True):
        """
        執行下載邏輯
        
        Args:
            whl_port_code: 港口代碼
            retry_login: 當遇到權限錯誤時是否自動重新登入
        """
        if whl_port_code not in self.port_map:
            return False, f"找不到港口代碼: {whl_port_code}"

        p_info = self.port_map[whl_port_code]
        url = f"https://aedyn.weathernews.com/api/business/sea/portstatus/content/48h/{p_info['id']}.txt"
        
        print(f"📡 正在下載 {whl_port_code} ({p_info['name']})...")
        
        try:
            response = self.session.get(url, headers=self.headers, verify=False, timeout=TIMEOUT)
            
            if response.status_code == 200:
                content = response.text
                issued_time = self.parse_issued_time(content)
                cached_time = self.db.get_latest_time(whl_port_code)

                if cached_time == issued_time:
                    return True, f"資料已是最新 ({issued_time})"
                
                if self.db.save_weather(p_info['wni_code'], whl_port_code, p_info['name'], p_info['id'], p_info['country'], issued_time, content):
                    return True, f"更新成功 ({issued_time})"
                else:
                    return False, "資料庫寫入失敗"
                    
            elif response.status_code in [401, 403]:
                # Cookie 過期，嘗試重新登入
                if retry_login:
                    print("⚠️ Cookie 已過期，正在重新登入...")
                    if self.refresh_cookies():
                        # 重新嘗試下載（但不再重試登入，避免無限迴圈）
                        return self.fetch_port_data(whl_port_code, retry_login=False)
                return False, f"權限不足 (HTTP {response.status_code}) - Cookie 已過期"
            else:
                return False, f"下載失敗 (HTTP {response.status_code})"
                
        except Exception as e:
            return False, f"連線錯誤: {str(e)}"

    def fetch_all_ports(self):
        """批次下載所有港口資料"""
        print(f"\n🚀 開始批次下載 {len(self.port_list)} 個港口資料...\n")
        
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
        
        print(f"\n📊 下載完成！")
        print(f"   ✅ 成功: {success_count}")
        print(f"   ⏭️  略過: {skip_count}")
        print(f"   ❌ 失敗: {fail_count}")

    def test_api_connection(self):
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
                    if 'application/json' in response.headers.get('Content-Type', ''):
                        data = response.json()
                        print(f"   回應: {json.dumps(data, indent=2, ensure_ascii=False)[:200]}...")
                    else:
                        print(f"   回應長度: {len(response.text)} bytes")
                    print("   ✅ 連線成功")
                else:
                    print(f"   ❌ 連線失敗")
                    
            except Exception as e:
                print(f"   ❌ 錯誤: {e}")

    def get_data_from_db(self, whl_port_code):
        """從資料庫讀取內容 (content, issued_time, port_name)"""
        return self.db.get_latest_content(whl_port_code)

    def get_port_info(self, whl_port_code: str) -> dict:
        """取得港口完整資訊"""
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
    # 初始化爬蟲（會自動檢查 Cookie，只在需要時才登入）
    print("="*60)
    print("初始化爬蟲系統")
    print("="*60)
    crawler = PortWeatherCrawler(auto_login=False)  # auto_login=False 表示智能登入
    
    # 測試 API 連線
    crawler.test_api_connection()
    
    # 範例 1: 下載單一港口
    print("\n" + "="*60)
    print("範例 1: 下載單一港口資料")
    print("="*60)
    success, message = crawler.fetch_port_data("TWKHH")
    print(f"結果: {message}")
    
    # 範例 2: 從資料庫讀取資料
    print("\n" + "="*60)
    print("範例 2: 從資料庫讀取資料")
    print("="*60)
    data = crawler.get_data_from_db("TWKHH")
    if data:
        content, issued_time, port_name = data
        print(f"港口: {port_name}")
        print(f"發布時間: {issued_time}")
        print(f"內容預覽: {content[:200]}...")
    
    # 範例 3: 批次下載所有港口（可選）
    # print("\n" + "="*60)
    # print("範例 3: 批次下載所有港口")
    # print("="*60)
    # crawler.fetch_all_ports()
    
    # 範例 4: 強制更新 Cookie（當需要時）
    # crawler.refresh_cookies(headless=False)  # headless=False 可以看到瀏覽器操作
