"""
Hybrid Tennis Court Reservation Bot.
Combines Selenium (for login/WebGate) with direct HTTP requests (for fast reservation).

API Flow:
1. Selenium: Login → WebGate → Extract cookies
2. Requests: Calendar API → Time API → Captcha API → Basket API
"""
import io
import re
import time
import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any, Tuple

import requests
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException, StaleElementReferenceException

from .config import Config, INDOOR_COURTS
from .notifier import Logger, SlackNotifier, ReservationResult
from .reservation import CaptchaSolver, KST


class HybridReservationBot:
    """
    하이브리드 예약 봇: Selenium + HTTP Requests 조합.
    
    Selenium으로 로그인/WebGate 통과 후, 
    빠른 HTTP 요청으로 예약을 진행합니다.
    """
    
    # API Endpoints (relative to base_url)
    API_CALENDAR = "./tennis_mcalendar_list.do"
    API_TIME_LIST = "./tennis_mtime_list.do"
    API_CAPTCHA = "../captcha.do"
    API_BASKET_INSERT = "./tennis_basket_ins.do"
    API_BASKET_LIST = "./tennis_mbasket_list.do"
    
    def __init__(
        self,
        driver: webdriver.Chrome,
        config: Config,
        logger: Logger,
        notifier: SlackNotifier
    ):
        self.driver = driver
        self.config = config
        self.logger = logger
        self.notifier = notifier
        self.captcha_solver = CaptchaSolver(logger)
        
        # requests 세션 (쿠키 공유용)
        self.session: Optional[requests.Session] = None
        
        # 서버 시간 오프셋
        self.server_time_offset: float = 0.0
        
        # 예약 오픈 시간
        self.target_time = datetime.now(KST).replace(
            hour=config.reservation.reservation_open_hour,
            minute=config.reservation.reservation_open_minute,
            second=0,
            microsecond=0
        )
        
        # 선택된 정보 저장
        self.selected_date_str = ""
        self.selected_time_str = ""
        
        # Base URL for API calls
        self.api_base_url = self.config.base_url.rstrip('/')
        if not self.api_base_url.endswith('/online/tennis'):
            # Ensure we have the correct base path
            if '/online/tennis' in self.api_base_url:
                self.api_base_url = self.api_base_url.split('/online/tennis')[0] + '/online/tennis'
            else:
                self.api_base_url = self.api_base_url + '/online/tennis'
    
    # =========================================================================
    # STEP 1: Selenium - 로그인 및 WebGate 통과
    # =========================================================================
    
    def login(self) -> bool:
        """Selenium으로 로그인합니다."""
        self.logger.info(f"🔐 로그인 페이지로 이동: {self.config.login_url}")
        
        try:
            self.driver.get(self.config.login_url)
            time.sleep(2)
            
            self.logger.info("📝 로그인 정보 입력 중")
            login_id_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, 'login_id'))
            )
            login_id_input.send_keys(self.config.login_id)
            self.driver.find_element(By.NAME, 'login_pwd').send_keys(self.config.login_password)
            
            self.logger.info("🔘 로그인 버튼 클릭")
            button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="content"]/div/div/div/button'))
            )
            self.driver.execute_script("arguments[0].scrollIntoView(true);", button)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", button)
            
            # Handle "already logged in" alert
            try:
                self.driver.switch_to.alert.accept()
                self.logger.info("ℹ️ 이미 로그인 되어있었습니다.")
            except NoAlertPresentException:
                pass
            
            # Wait for login completion
            self.logger.info("🔄 로그인 완료 대기 중...")
            for i in range(30):
                time.sleep(1)
                current_url = self.driver.current_url
                if "/sso/usr/login" not in current_url and "SSOService" not in current_url:
                    self.logger.info(f"✅ 로그인 완료 (URL: {current_url})")
                    return True
            
            self.logger.info("⚠️ 로그인 시간 초과")
            return False
            
        except Exception as e:
            self.logger.info(f"❌ 로그인 실패: {e}")
            return False
    
    def navigate_to_reservation_page(self) -> bool:
        """예약 페이지로 이동하고 WebGate를 통과합니다."""
        try:
            self.logger.info("🏠 메인 홈페이지 로딩 대기")
            WebDriverWait(self.driver, 60).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            self.logger.info("🎾 예약하기 버튼 클릭")
            link = WebDriverWait(self.driver, 60).until(
                EC.element_to_be_clickable((By.LINK_TEXT, "일일입장 예약신청"))
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
            time.sleep(3)
            self.driver.execute_script("arguments[0].click();", link)
            
            # WebGate 대기열 통과 대기 (적극적 폴링)
            self.logger.info("⏳ WebGate 대기열 통과 대기 중...")
            
            start_wait = time.time()
            max_wait_seconds = 120
            poll_interval = 0.1  # 100ms 간격으로 빠르게 확인
            last_status_log = 0
            
            while time.time() - start_wait < max_wait_seconds:
                try:
                    # 예약 페이지 도착 확인 (tab_by_date 존재)
                    elements = self.driver.find_elements(By.ID, 'tab_by_date')
                    if elements:
                        elapsed = time.time() - start_wait
                        self.logger.info(f"✅ 예약 페이지 진입 완료! (WebGate 통과: {elapsed:.1f}초)")
                        break
                    
                    # WebGate 대기 상태 확인 및 로그 (5초마다)
                    elapsed = time.time() - start_wait
                    if elapsed - last_status_log >= 5:
                        # WebGate 상태 정보 추출 시도
                        try:
                            # WebGate는 보통 iframe이나 특정 div에 대기 정보 표시
                            queue_info = self.driver.execute_script("""
                                // WebGate 대기열 정보 추출 시도
                                var info = {};
                                
                                // 방법 1: WebGate 전역 변수
                                if (typeof WebGate !== 'undefined' && WebGate.queue) {
                                    info.position = WebGate.queue.position;
                                    info.total = WebGate.queue.total;
                                }
                                
                                // 방법 2: 대기 페이지 텍스트 파싱
                                var waitText = document.body.innerText;
                                var match = waitText.match(/(\d+)\s*번째|(\d+)\s*명/);
                                if (match) {
                                    info.text = match[0];
                                }
                                
                                // 방법 3: 진행률 바
                                var progress = document.querySelector('.progress-bar, .queue-progress, [class*="progress"]');
                                if (progress) {
                                    info.progress = progress.style.width || progress.getAttribute('aria-valuenow');
                                }
                                
                                return info;
                            """)
                            
                            if queue_info:
                                status_parts = []
                                if queue_info.get('position'):
                                    status_parts.append(f"순번: {queue_info['position']}")
                                if queue_info.get('text'):
                                    status_parts.append(queue_info['text'])
                                if queue_info.get('progress'):
                                    status_parts.append(f"진행률: {queue_info['progress']}")
                                
                                if status_parts:
                                    self.logger.info(f"   ⏳ 대기 중... ({elapsed:.0f}초) - {', '.join(status_parts)}")
                                else:
                                    self.logger.info(f"   ⏳ 대기 중... ({elapsed:.0f}초)")
                            else:
                                self.logger.info(f"   ⏳ 대기 중... ({elapsed:.0f}초)")
                        except:
                            self.logger.info(f"   ⏳ 대기 중... ({elapsed:.0f}초)")
                        
                        last_status_log = elapsed
                    
                    time.sleep(poll_interval)
                    
                except Exception:
                    time.sleep(poll_interval)
            else:
                raise TimeoutError("WebGate 대기열 타임아웃 (120초)")
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 예약 페이지 진입 실패: {e}")
            return False
    
    # =========================================================================
    # STEP 2: 쿠키 추출 및 requests 세션 생성
    # =========================================================================
    
    def extract_cookies_to_session(self) -> bool:
        """Selenium 쿠키를 requests 세션으로 복사합니다."""
        try:
            self.logger.info("🍪 Selenium 쿠키 추출 중...")
            
            # 새 requests 세션 생성
            self.session = requests.Session()
            
            # Selenium 쿠키 가져오기
            selenium_cookies = self.driver.get_cookies()
            
            # requests 세션에 쿠키 추가
            for cookie in selenium_cookies:
                self.session.cookies.set(
                    cookie['name'],
                    cookie['value'],
                    domain=cookie.get('domain', ''),
                    path=cookie.get('path', '/')
                )
            
            # 공통 헤더 설정
            self.session.headers.update({
                'User-Agent': self.driver.execute_script("return navigator.userAgent"),
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': self.config.base_url,
            })
            
            self.logger.info(f"✅ {len(selenium_cookies)}개 쿠키 추출 완료")
            
            # 쿠키 목록 로깅 (디버그용)
            cookie_names = [c['name'] for c in selenium_cookies]
            self.logger.info(f"   └ 쿠키: {', '.join(cookie_names[:10])}...")
            
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 쿠키 추출 실패: {e}")
            return False
    
    # =========================================================================
    # STEP 3: 서버 시간 측정
    # =========================================================================
    
    def measure_server_time_offset(self) -> float:
        """서버 시간과 로컬 시간의 차이를 측정합니다."""
        try:
            self.logger.info("🕐 서버 시간 측정 중 (5회, 보수적 최솟값 사용)...")
            
            offsets = []
            for i in range(5):
                local_before = datetime.now(timezone.utc)
                response = requests.head(self.config.base_url, timeout=5)
                local_after = datetime.now(timezone.utc)
                
                local_mid = local_before + (local_after - local_before) / 2
                
                date_header = response.headers.get('Date')
                if date_header:
                    server_time = parsedate_to_datetime(date_header)
                    offset = (server_time - local_mid).total_seconds()
                    offsets.append(offset)
                    self.logger.info(f"   측정 {i+1}: offset={offset:.3f}초")
                
                time.sleep(0.05)
            
            if offsets:
                min_offset = min(offsets)
                self.server_time_offset = min_offset
                self.logger.info(f"📊 최솟값: {min_offset:.3f}초")
                return min_offset
            
            return 0.0
            
        except Exception as e:
            self.logger.info(f"⚠️ 서버 시간 측정 오류: {e}")
            return 0.0
    
    # =========================================================================
    # STEP 4: HTTP API 호출 (하이브리드 핵심!)
    # =========================================================================
    
    def _build_url(self, endpoint: str) -> str:
        """API endpoint URL을 생성합니다."""
        # ./로 시작하면 제거
        if endpoint.startswith('./'):
            endpoint = endpoint[2:]
        elif endpoint.startswith('../'):
            # ../captcha.do → /online/captcha.do
            endpoint = endpoint[3:]
            return f"{self.api_base_url.rsplit('/tennis', 1)[0]}/{endpoint}"
        
        return f"{self.api_base_url}/{endpoint}"
    
    def api_get_calendar(self, search_date: str = None, court_no: int = 0) -> Optional[Dict]:
        """
        캘린더 API를 호출하여 예약 가능한 날짜와 xDay(암호화된 날짜)를 가져옵니다.
        
        Args:
            search_date: 검색 기준 날짜 (YYYYMMDD 형식, 없으면 오늘)
            court_no: 코트 번호 (기본 0)
            
        Returns:
            API 응답 데이터 또는 None
        """
        if not self.session:
            self.logger.info("❌ 세션이 없습니다. 먼저 extract_cookies_to_session()을 호출하세요.")
            return None
        
        if search_date is None:
            search_date = datetime.now(KST).strftime('%Y%m%d')
        
        url = self._build_url(self.API_CALENDAR)
        params = {
            'search_gubun': 'date',
            'search_date': search_date,
            'court_no': court_no
        }
        
        try:
            self.logger.info(f"📅 캘린더 API 호출: {url}")
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('ss_check', 0) > 0 and data.get('calendar_list'):
                calendar_list = data['calendar_list']
                self.logger.info(f"✅ 캘린더 조회 성공: {len(calendar_list)}개 날짜")
                
                # 예약 가능한 날짜 출력
                available_dates = [d for d in calendar_list if d.get('checkDay') == 'Y']
                self.logger.info(f"   └ 예약 가능 날짜: {len(available_dates)}개")
                for d in available_dates[-3:]:  # 마지막 3개만
                    self.logger.info(f"      • {d.get('dDay')} (xDay: {d.get('xDay', '')[:15]}...)")
                
                return data
            else:
                self.logger.info(f"⚠️ 캘린더 데이터 없음: {data}")
                return None
                
        except Exception as e:
            self.logger.info(f"❌ 캘린더 API 오류: {e}")
            return None
    
    def api_get_time_list(self, date: str, xdate: str, court_no: int = 0) -> Optional[Dict]:
        """
        시간 목록 API를 호출합니다.
        
        Args:
            date: 날짜 (YYYYMMDD 형식)
            xdate: 암호화된 날짜 (xDay)
            court_no: 코트 번호 (기본 0 = 전체)
            
        Returns:
            API 응답 데이터 또는 None
        """
        if not self.session:
            return None
        
        url = self._build_url(self.API_TIME_LIST)
        params = {
            'search_date': date,
            'search_gubun': 'date',
            'search_xdate': xdate,
        }
        if court_no > 0:
            params['court_no'] = court_no
        
        try:
            self.logger.info(f"⏰ 시간 목록 API 호출: date={date}, xdate={xdate[:10]}...")
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('ss_check', 0) > 0 and data.get('time_list'):
                time_list = data['time_list']
                available_slots = []
                for t in time_list:
                    tot = int(t.get('totCnt', 0))
                    end = int(t.get('endCnt', 0))
                    prog = int(t.get('progCnt', 0))
                    others = int(t.get('othersCnt', 0))
                    avail = tot - end - prog - others
                    if avail > 0:
                        available_slots.append(f"{t.get('startT')}~{t.get('endT')}(잔여:{avail})")
                
                self.logger.info(f"✅ 시간 조회 성공: {len(time_list)}개 슬롯, {len(available_slots)}개 가능")
                
                # 가용 시간대 출력
                if available_slots:
                    self.logger.info(f"   └ 가용 시간: {', '.join(available_slots[:5])}")
                    if len(available_slots) > 5:
                        self.logger.info(f"   └ ... 외 {len(available_slots)-5}개 더")
                else:
                    self.logger.info(f"   └ 가용 시간 없음!")
                    # 디버그: 첫 3개 슬롯 정보
                    for i, t in enumerate(time_list[:3]):
                        self.logger.info(f"      {t.get('startT')}~{t.get('endT')}: tot={t.get('totCnt')}, end={t.get('endCnt')}, prog={t.get('progCnt')}, others={t.get('othersCnt')}")
                
                return data
            else:
                self.logger.info(f"⚠️ 시간 데이터 없음")
                return None
                
        except Exception as e:
            self.logger.info(f"❌ 시간 API 오류: {e}")
            return None
    
    def api_get_captcha(self) -> Optional[Image.Image]:
        """
        캡차 이미지를 가져옵니다.
        HTTP API로는 캡차가 작동하지 않아서 Selenium으로 가져옵니다.
        
        Returns:
            PIL Image 또는 None
        """
        try:
            self.logger.info(f"🔐 캡차 이미지 가져오기 (Selenium)")
            
            # 캡차 wrap 존재 확인
            try:
                captcha_wrap = self.driver.find_element(By.ID, 'layer_captcha_wrap')
                self.logger.info(f"   └ 캡차 wrap 발견: {captcha_wrap.is_displayed()}")
            except Exception as e:
                self.logger.info(f"   └ 캡차 wrap 없음: {e}")
            
            # 캡차 이미지 요소 찾기
            self.logger.info(f"   └ 캡차 이미지 요소 대기 중...")
            captcha_element = WebDriverWait(self.driver, 10).until(
                EC.visibility_of_element_located(
                    (By.XPATH, '//*[@id="layer_captcha_wrap"]/div/img')
                )
            )
            self.logger.info(f"   └ 캡차 이미지 요소 발견!")
            
            # 이미지가 완전히 로드될 때까지 대기
            for i in range(10):
                try:
                    size = captcha_element.size
                    if size['width'] > 0 and size['height'] > 0:
                        self.logger.info(f"   └ 이미지 크기: {size}")
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            
            time.sleep(0.3)  # 안전을 위한 추가 대기
            
            # 스크린샷으로 캡차 이미지 가져오기
            captcha_image = Image.open(io.BytesIO(captcha_element.screenshot_as_png))
            self.logger.info(f"✅ 캡차 이미지 캡처 완료: {captcha_image.size}")
            return captcha_image
            
        except Exception as e:
            self.logger.info(f"❌ 캡차 이미지 가져오기 실패: {e}")
            import traceback
            self.logger.info(f"   └ 상세: {traceback.format_exc()[:200]}")
            return None
    
    def api_add_to_basket(
        self,
        xdate: str,
        date: str,
        court_no: int,
        start_times: List[str],
        end_times: List[str],
        captcha: str
    ) -> Tuple[bool, str, int]:
        """
        장바구니에 예약을 추가합니다 (핵심 예약 API!).
        
        Args:
            xdate: 암호화된 날짜 (search_date)
            date: 일반 날짜 (search_date_a, YYYYMMDD)
            court_no: 코트 번호
            start_times: 시작 시간 배열 (예: ["19:00", "20:00"])
            end_times: 종료 시간 배열 (예: ["20:00", "21:00"])
            captcha: 캡차 입력값
            
        Returns:
            Tuple of (success, message, validity_no)
        """
        if not self.session:
            return False, "세션 없음", -1
        
        url = self._build_url(self.API_BASKET_INSERT)
        
        payload = {
            "search_date": xdate,  # 암호화된 날짜!
            "search_date_a": date,  # 일반 날짜
            "captcha": captcha,
            "reservations": [{
                "court_no": str(court_no),
                "start_t_array": start_times,
                "end_t_array": end_times
            }]
        }
        
        try:
            self.logger.info(f"🛒 장바구니 API 호출: 코트 {court_no}, {start_times[0]}~{end_times[-1]}")
            self.logger.info(f"   └ payload: {json.dumps(payload, ensure_ascii=False)[:200]}...")
            
            response = self.session.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json; charset=utf-8'},
                timeout=15
            )
            response.raise_for_status()
            
            data = response.json()
            ss_check = data.get('ss_check', 0)
            validity_no = data.get('validity_no', -1)
            
            self.logger.info(f"📦 응답: ss_check={ss_check}, validity_no={validity_no}")
            
            if ss_check > 0 and validity_no == 0:
                self.logger.info("✅ 장바구니 추가 성공!")
                return True, "성공", validity_no
            elif ss_check > 0 and validity_no > 0:
                # 에러 코드별 메시지
                error_messages = {
                    1: "이미 결제대기에 존재",
                    2: "예약 가능 시간(2시간) 초과",
                    3: "예약 가능 시간(2시간) 초과",
                    4: "예약 가능 시간(2시간) 초과",
                    5: "다른 사용자가 예약 진행중",
                    6: "이미 예약 완료됨",
                    7: "예약 가능 기간 아님",
                    8: "마감/정산 시간 (23:50~00:10)",
                    9: "다른 사용자가 예약 진행중",
                }
                msg = error_messages.get(validity_no, f"알 수 없는 오류 (code: {validity_no})")
                self.logger.info(f"⚠️ 장바구니 추가 실패: {msg}")
                return False, msg, validity_no
            elif ss_check == 0:
                return False, "장바구니 이동 실패", validity_no
            elif ss_check == -1:
                return False, "로그인 필요", validity_no
            else:
                return False, f"알 수 없는 응답: {data}", validity_no
                
        except requests.exceptions.RequestException as e:
            self.logger.info(f"❌ 장바구니 API 네트워크 오류: {e}")
            return False, str(e), -1
        except Exception as e:
            self.logger.info(f"❌ 장바구니 API 오류: {e}")
            return False, str(e), -1
    
    # =========================================================================
    # STEP 5: 예약 대기 및 실행
    # =========================================================================
    
    def wait_for_reservation_open(self) -> None:
        """로컬 시간 9:00:00.100에 페이지를 새로고침합니다."""
        # 로컬 시간 9:00:00.100에 새로고침 (100ms 마진)
        target_time = self.target_time.replace(microsecond=100000)  # 0.100초 = 100,000 마이크로초
        
        self.logger.info(f"⏰ 로컬 시간 9시 대기 (목표: {target_time.strftime('%H:%M:%S.%f')[:-3]})")
        
        current_time = datetime.now(KST)
        time_diff = (target_time - current_time).total_seconds()
        
        if time_diff > 0:
            if time_diff > 10:
                sleep_time = time_diff - 10
                self.logger.info(f"💤 목표 시각까지 {sleep_time:.1f}초 대기...")
                time.sleep(sleep_time)
            
            self.logger.info("🎯 마지막 10초 정밀 대기 시작...")
            while datetime.now(KST) < target_time:
                time.sleep(0.0001)
            
            actual_time = datetime.now(KST)
            self.logger.info(f"🚀 목표 시각 도달! 새로고침 시작!")
            self.logger.info(f"   실제 로컬 시각: {actual_time.strftime('%H:%M:%S.%f')[:-3]}")
        else:
            self.logger.info("이미 목표 시각이 지났습니다. 즉시 실행합니다.")
        
        # 페이지 새로고침
        self.logger.info("🔄 페이지 새로고침")
        self.driver.refresh()
        self.logger.info("✅ 페이지 새로고침 완료")
        
        # 예약 페이지 로딩 대기 (날짜 링크 표시될 때까지)
        self.logger.info("📅 예약 가능한 날짜 로딩 대기...")
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//tbody//a[starts-with(@href, 'javascript:fn_tennis_time_list')]")
                )
            )
            self.logger.info("✅ 날짜 로딩 완료")
        except Exception as e:
            self.logger.info(f"⚠️ 날짜 로딩 대기 중 오류: {e}")
    
    def find_available_slots(
        self,
        time_list: List[Dict],
        slot_count: int,
        preferred_courts: List[int]
    ) -> List[Dict]:
        """
        가능한 시간대와 코트 조합을 찾습니다.
        
        Args:
            time_list: 시간 API 응답의 time_list
            slot_count: 필요한 연속 슬롯 수
            preferred_courts: 선호 코트 목록
            
        Returns:
            가능한 조합 리스트 [{hour, start_times, end_times, court}]
        """
        available_slots = []
        
        # 시간대별로 그룹화 및 가용성 확인
        for i in range(len(time_list) - slot_count + 1):
            slots = time_list[i:i + slot_count]
            
            # 모든 슬롯이 사용 가능한지 확인
            all_available = all(
                slot.get('useYn') == 'Y' and 
                (int(slot.get('totCnt', 0)) - int(slot.get('endCnt', 0)) - int(slot.get('progCnt', 0))) > 0
                for slot in slots
            )
            
            if all_available:
                start_times = [slot.get('startT', '') for slot in slots]
                end_times = [slot.get('endT', '') for slot in slots]
                hour = int(start_times[0].split(':')[0]) if start_times[0] else 0
                
                # 각 선호 코트에 대해 가용성 확인
                for court in preferred_courts:
                    # 해당 코트가 모든 슬롯에서 가능한지 확인
                    court_available = True
                    for slot in slots:
                        court_no = slot.get('courtNo')
                        if court_no and int(court_no) != court:
                            continue
                        # 코트별 상세 확인이 필요한 경우 추가 API 호출 필요
                    
                    if court_available:
                        available_slots.append({
                            'hour': hour,
                            'start_times': start_times,
                            'end_times': end_times,
                            'court': court
                        })
        
        # 늦은 시간대부터 정렬
        available_slots.sort(key=lambda x: x['hour'], reverse=True)
        
        return available_slots
    
    def select_date_with_selenium(self, target_date: str) -> bool:
        """
        Selenium으로 날짜를 선택합니다.
        
        Args:
            target_date: 날짜 (YYYYMMDD 형식)
            
        Returns:
            성공 여부
        """
        try:
            self.logger.info(f"📅 Selenium으로 날짜 선택: {target_date}")
            
            # WebGate 통과 후 페이지 안정화 대기
            self.logger.info("   └ 페이지 안정화 대기...")
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(0.5)  # 추가 안정화 시간
            
            # Stale element 방지: 최대 3회 재시도
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # 예약 가능한 날짜 링크 찾기 (매번 새로 찾기!)
                    clickable_dates = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_all_elements_located((
                            By.XPATH,
                            "//tbody//a[starts-with(@href, 'javascript:fn_tennis_time_list')]"
                        ))
                    )
                    
                    self.logger.info(f"   └ 클릭 가능한 날짜: {len(clickable_dates)}개")
                    
                    if not clickable_dates:
                        self.logger.info("❌ 클릭 가능한 날짜가 없음")
                        return False
                    
                    # 마지막 날짜 클릭 (가장 나중 날짜)
                    target = clickable_dates[-1]
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", target)
                    time.sleep(0.1)
                    self.driver.execute_script("arguments[0].click();", target)
                    
                    self.logger.info(f"✅ 날짜 클릭 완료")
                    break  # 성공하면 루프 탈출
                    
                except StaleElementReferenceException:
                    if attempt < max_retries - 1:
                        self.logger.info(f"   └ Element stale, 재시도 {attempt + 1}/{max_retries}")
                        time.sleep(0.3)
                    else:
                        raise  # 마지막 시도에서도 실패하면 예외 발생
            
            # 시간 슬롯 로딩 대기
            self.logger.info(f"   └ 시간 슬롯 로딩 대기 중...")
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'ul#time_con li'))
            )
            time.sleep(0.5)
            
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 날짜 선택 실패: {e}")
            return False
    
    def get_available_courts_selenium(self, preferred_courts: List[int]) -> List[int]:
        """
        Selenium으로 현재 가용한 코트 목록을 확인합니다.
        시간 선택 후 호출해야 합니다.
        
        Args:
            preferred_courts: 확인할 코트 번호 목록
            
        Returns:
            가용한 코트 번호 목록
        """
        available = []
        
        # implicit wait 일시적으로 비활성화 (빠른 확인)
        original_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        
        try:
            for court_num in preferred_courts:
                try:
                    court_id = f'tennis_court_img_a_1_{court_num}'
                    courts = self.driver.find_elements(By.ID, court_id)
                    if not courts:
                        continue
                    court = courts[0]
                    img_elements = court.find_elements(By.TAG_NAME, 'img')
                    if not img_elements:
                        continue
                    # btn_tennis_noreserve = 예약 불가 이미지
                    if 'btn_tennis_noreserve' not in img_elements[0].get_attribute('src'):
                        available.append(court_num)
                except Exception:
                    continue
        finally:
            self.driver.implicitly_wait(original_wait)
        
        return available
    
    def select_time_with_selenium(
        self,
        start_hour: int,
        slot_count: int,
        preferred_courts: List[int]
    ) -> Tuple[bool, List[int]]:
        """
        Selenium으로 시간을 선택하고 가용 코트를 확인합니다.
        
        Args:
            start_hour: 시작 시간 (예: 19)
            slot_count: 슬롯 개수
            preferred_courts: 확인할 코트 목록
            
        Returns:
            Tuple of (성공 여부, 가용 코트 목록)
        """
        try:
            # 시간 슬롯 선택
            base_hour = 6  # 06시 = index 0
            
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            
            if len(time_slots) == 0:
                self.logger.info(f"❌ 시간 슬롯이 없음!")
                return False, []
            
            # 현재 선택된 슬롯 확인 (변경이 필요한지 체크)
            target_indices = set(range(start_hour - base_hour, start_hour - base_hour + slot_count))
            currently_selected = set()
            
            for idx, slot in enumerate(time_slots):
                try:
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    if checkbox.is_selected():
                        currently_selected.add(idx)
                except Exception:
                    pass
            
            # 이미 원하는 시간이 선택되어 있으면 스킵
            if currently_selected == target_indices:
                self.logger.info(f"   └ {start_hour}시~{start_hour+slot_count}시 이미 선택됨")
            else:
                # 다른 시간이 선택되어 있으면 먼저 해제
                for idx in currently_selected - target_indices:
                    try:
                        slot = time_slots[idx]
                        checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        try:
                            alert = self.driver.switch_to.alert
                            alert.accept()
                        except NoAlertPresentException:
                            pass
                    except Exception:
                        pass
                
                # 원하는 시간 선택
                for i in range(slot_count):
                    slot_index = start_hour - base_hour + i
                    
                    if slot_index >= len(time_slots):
                        self.logger.info(f"❌ 시간 슬롯 인덱스 범위 초과")
                        return False, []
                    
                    slot = time_slots[slot_index]
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    
                    if not checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        time.sleep(0.15)
            
            # 코트 상태 업데이트 대기
            time.sleep(0.3)
            
            # 가용 코트 확인
            available_courts = self.get_available_courts_selenium(preferred_courts)
            self.logger.info(f"⏰ {start_hour}시~{start_hour+slot_count}시 → 가용 코트: {available_courts if available_courts else '없음'}")
            
            return True, available_courts
            
        except Exception as e:
            self.logger.info(f"❌ Selenium 시간 선택 실패: {e}")
            return False, []
    
    def select_court_with_selenium(self, court_no: int) -> bool:
        """
        Selenium으로 코트를 선택합니다.
        
        Args:
            court_no: 코트 번호
            
        Returns:
            성공 여부
        """
        try:
            self.logger.info(f"🎾 코트 {court_no} 선택 중...")
            
            court_id = f'tennis_court_img_a_1_{court_no}'
            court = self.driver.find_element(By.ID, court_id)
            self.driver.execute_script("arguments[0].click();", court)
            
            # Alert 처리
            try:
                time.sleep(0.3)
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                if "예약이 완료된 코트입니다" in alert_text or "예약이 불가" in alert_text:
                    alert.accept()
                    self.logger.info(f"❌ 코트 {court_no} 예약 불가: {alert_text}")
                    return False
                alert.accept()
            except NoAlertPresentException:
                pass
            
            self.logger.info(f"✅ 코트 {court_no} 선택 완료!")
            
            # 캡차 표시 대기
            time.sleep(0.5)
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 코트 선택 실패: {e}")
            return False
    
    def select_time_and_court_with_selenium(
        self,
        start_hour: int,
        slot_count: int,
        court_no: int
    ) -> bool:
        """
        Selenium으로 시간과 코트를 선택합니다 (캡차 표시를 위해).
        [DEPRECATED] select_time_with_selenium + select_court_with_selenium 사용 권장
        """
        success, available_courts = self.select_time_with_selenium(start_hour, slot_count, [court_no])
        if not success:
            return False
        
        if court_no not in available_courts:
            self.logger.info(f"❌ 코트 {court_no}은 가용 코트 목록에 없음: {available_courts}")
            return False
        
        return self.select_court_with_selenium(court_no)
    
    def clear_selenium_selections(self) -> None:
        """Selenium에서 선택된 시간 슬롯을 초기화합니다."""
        try:
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            for slot in time_slots:
                try:
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    if checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        try:
                            alert = self.driver.switch_to.alert
                            alert.accept()
                        except NoAlertPresentException:
                            pass
                except Exception:
                    continue
        except Exception:
            pass
    
    def try_reservation_with_api(
        self,
        date: str,
        xdate: str,
        court_no: int,
        start_times: List[str],
        end_times: List[str],
        max_captcha_retries: int = 3
    ) -> Tuple[bool, str]:
        """
        하이브리드 방식으로 예약을 시도합니다.
        (시간/코트는 이미 선택된 상태여야 함!)
        - Selenium: 캡차 이미지 가져오기
        - API: 장바구니 추가 (빠른 처리)
        
        Returns:
            Tuple of (success, message)
        """
        for attempt in range(1, max_captcha_retries + 1):
            self.logger.info(f"🔄 예약 시도 {attempt}/{max_captcha_retries}")
            
            # 1. 캡차 가져오기 (Selenium)
            captcha_image = self.api_get_captcha()
            if not captcha_image:
                self.logger.info("❌ 캡차 이미지 가져오기 실패")
                if attempt < max_captcha_retries:
                    self._refresh_captcha_selenium()
                continue
            
            # 2. 캡차 풀기
            captcha_result = self.captcha_solver.solve(captcha_image)
            if not captcha_result:
                self.logger.info("❌ 캡차 인식 실패")
                if attempt < max_captcha_retries:
                    self._refresh_captcha_selenium()
                continue
            
            self.logger.info(f"🔐 캡차 인식 결과: {captcha_result}")
            
            # 3. 장바구니 추가 (API)
            success, message, validity_no = self.api_add_to_basket(
                xdate=xdate,
                date=date,
                court_no=court_no,
                start_times=start_times,
                end_times=end_times,
                captcha=captcha_result
            )
            
            if success:
                self.clear_selenium_selections()
                return True, message
            
            # 캡차 오류면 재시도
            if "captcha" in message.lower() or "자동입력" in message:
                self.logger.info(f"⚠️ 캡차 오류, 재시도...")
                if attempt < max_captcha_retries:
                    self._refresh_captcha_selenium()
                continue
            
            # 다른 오류면 중단
            self.clear_selenium_selections()
            return False, message
        
        self.clear_selenium_selections()
        return False, f"캡차 {max_captcha_retries}회 시도 실패"
    
    def _refresh_captcha_selenium(self) -> None:
        """Selenium으로 캡차를 새로고침합니다."""
        try:
            refresh_btn = self.driver.find_element(
                By.XPATH, '//*[@id="layer_captcha_wrap"]//input[@value="새로고침"]'
            )
            refresh_btn.click()
            time.sleep(0.5)
        except Exception:
            pass
    
    # =========================================================================
    # 메인 실행 로직
    # =========================================================================
    
    def run(self) -> int:
        """
        하이브리드 예약 프로세스를 실행합니다.
        
        Returns:
            0 for success, 1 for failure
        """
        self.logger.info("=" * 60)
        self.logger.info("🚀 하이브리드 예약 봇 시작")
        self.logger.info("   Selenium(로그인) + HTTP Requests(예약)")
        self.logger.info("=" * 60)
        
        result = ReservationResult()
        strategies = self.config.reservation.strategies
        
        try:
            # ====== PHASE 1: Selenium으로 로그인 및 WebGate 통과 ======
            self.logger.info("\n📌 PHASE 1: Selenium 로그인")
            
            if not self.login():
                result.error_message = "로그인 실패"
                self.notifier.send_failure("로그인 실패", result)
                return 1
            
            # OCR 엔진 사전 로딩
            self.captcha_solver.preload()
            
            # ====== PHASE 2: 예약 페이지 진입 (WebGate 통과) ======
            self.logger.info("\n📌 PHASE 2: 예약 페이지 진입 (WebGate 통과)")
            if not self.navigate_to_reservation_page():
                result.error_message = "예약 페이지 진입 실패"
                self.notifier.send_failure("예약 페이지 진입 실패", result)
                return 1
            
            # ====== PHASE 3: 9시까지 대기 + 새로고침 ======
            self.logger.info("\n📌 PHASE 3: 예약 오픈 대기 + 새로고침")
            self.wait_for_reservation_open()
            
            # ====== PHASE 4: 쿠키 추출 ======
            self.logger.info("\n📌 PHASE 4: 쿠키 추출")
            
            if not self.extract_cookies_to_session():
                result.error_message = "쿠키 추출 실패"
                self.notifier.send_failure("쿠키 추출 실패", result)
                return 1
            
            # ====== PHASE 5: HTTP API로 빠른 예약 ======
            self.logger.info("\n📌 PHASE 5: HTTP API 예약 시작")
            
            # 5.1 캘린더 API로 날짜 및 xDay 획득
            start_time = time.time()
            calendar_data = self.api_get_calendar()
            
            if not calendar_data or not calendar_data.get('calendar_list'):
                result.error_message = "캘린더 조회 실패"
                self.notifier.send_failure("캘린더 조회 실패", result)
                return 1
            
            # 가장 마지막 날짜 선택
            calendar_list = calendar_data['calendar_list']
            latest_date_info = None
            
            for date_info in reversed(calendar_list):
                if date_info.get('checkDay') == 'Y':
                    latest_date_info = date_info
                    break
            
            if not latest_date_info:
                result.error_message = "예약 가능한 날짜 없음"
                self.notifier.send_failure("예약 가능한 날짜 없음", result)
                return 1
            
            target_date = latest_date_info.get('dDay', '')  # YYYY-MM-DD 또는 YYYYMMDD
            target_xdate = latest_date_info.get('xDay', '')  # 암호화된 날짜
            
            # 날짜 형식 통일 (YYYYMMDD)
            target_date_normalized = target_date.replace('-', '')
            
            self.logger.info(f"📅 선택된 날짜: {target_date}")
            self.logger.info(f"🔐 암호화 날짜: {target_xdate[:20]}...")
            
            result.date = target_date
            
            # 5.2 Selenium으로 날짜 선택 (시간 슬롯 표시를 위해)
            self.logger.info("\n📌 PHASE 5.5: Selenium 날짜 선택")
            if not self.select_date_with_selenium(target_date_normalized):
                result.error_message = "Selenium 날짜 선택 실패"
                self.notifier.send_failure("Selenium 날짜 선택 실패", result)
                return 1
            
            # 5.3 각 전략별로 예약 시도
            for strategy in strategies:
                self.logger.info(f"\n🎯 전략 시도: {strategy.name}")
                
                # 시간 목록 조회
                time_data = self.api_get_time_list(target_date_normalized, target_xdate)
                
                if not time_data or not time_data.get('time_list'):
                    self.logger.info(f"⚠️ 시간 조회 실패, 다음 전략...")
                    continue
                
                # 가능한 슬롯 찾기
                time_list = time_data['time_list']
                
                # 가용 시간대 찾기 
                available_time_slots = []
                
                # target_hour가 지정된 경우: 해당 시간대만 확인
                # auto_find_latest=True인 경우: 가장 늦은 시간부터 역순으로 탐색
                if strategy.auto_find_latest:
                    # 3순위: 가장 늦은 시간부터 역순 탐색
                    self.logger.info(f"   └ 가장 늦은 연속 시간대 탐색 모드")
                    search_range = range(len(time_list) - strategy.time_slot_count, -1, -1)
                else:
                    # 1, 2순위: 특정 target_hour부터 시작
                    self.logger.info(f"   └ 특정 시간대({strategy.target_hour}시) 탐색 모드")
                    search_range = range(len(time_list) - strategy.time_slot_count + 1)
                
                for i in search_range:
                    slots = time_list[i:i + strategy.time_slot_count]
                    
                    # 첫 슬롯의 시작 시간 확인
                    first_slot_start = slots[0].get('startT', '')
                    if first_slot_start:
                        slot_start_hour = int(first_slot_start.split(':')[0])
                        
                        # target_hour가 지정된 경우, 해당 시간이 아니면 스킵
                        if not strategy.auto_find_latest and slot_start_hour != strategy.target_hour:
                            continue
                    
                    # 모든 슬롯의 가용 수량 확인
                    all_available = True
                    for slot in slots:
                        tot = int(slot.get('totCnt', 0))
                        end = int(slot.get('endCnt', 0))
                        prog = int(slot.get('progCnt', 0))
                        others = int(slot.get('othersCnt', 0))
                        avail = tot - end - prog - others
                        if avail <= 0:
                            all_available = False
                            break
                    
                    if all_available:
                        start_times = [slot.get('startT', '') for slot in slots]
                        end_times = [slot.get('endT', '') for slot in slots]
                        if all(start_times) and all(end_times):
                            available_time_slots.append({
                                'start_times': start_times,
                                'end_times': end_times
                            })
                            
                            # target_hour가 지정된 경우 정확히 하나만 찾으면 됨
                            if not strategy.auto_find_latest:
                                break
                
                if not available_time_slots:
                    self.logger.info(f"⚠️ 연속 {strategy.time_slot_count}시간 가용 시간대 없음, 다음 전략...")
                    continue
                
                self.logger.info(f"✅ 연속 {strategy.time_slot_count}시간 가용 시간대: {len(available_time_slots)}개 발견")
                for ts in available_time_slots[:3]:
                    self.logger.info(f"   └ {ts['start_times'][0]}~{ts['end_times'][-1]}")
                
                # 각 시간대마다 시도
                for time_slot in available_time_slots:
                    start_times = time_slot['start_times']
                    end_times = time_slot['end_times']
                    start_hour = int(start_times[0].split(':')[0])
                    
                    # 1. Selenium으로 시간 선택 + 가용 코트 확인
                    success, available_courts = self.select_time_with_selenium(
                        start_hour, 
                        strategy.time_slot_count,
                        strategy.preferred_courts
                    )
                    
                    if not success:
                        self.logger.info(f"⚠️ 시간 선택 실패, 다음 시간대...")
                        continue
                    
                    if not available_courts:
                        self.logger.info(f"⚠️ 가용 코트 없음, 다음 시간대...")
                        continue
                    
                    # 2. 가용 코트 중에서 순서대로 시도
                    for court_no in available_courts:
                        self.logger.info(f"🎾 코트 {court_no} 선택 시도...")
                        
                        # 코트 선택
                        if not self.select_court_with_selenium(court_no):
                            self.logger.info(f"⚠️ 코트 {court_no} 선택 실패, 다음 코트...")
                            continue
                        
                        # 3. 캡차 + API로 예약
                        success, message = self.try_reservation_with_api(
                            date=target_date_normalized,
                            xdate=target_xdate,
                            court_no=court_no,
                            start_times=start_times,
                            end_times=end_times
                        )
                        
                        if success:
                            elapsed = time.time() - start_time
                            result.success = True
                            result.court_number = court_no
                            result.time_slot = f"{start_times[0]}~{end_times[-1]}"
                            result.strategy_name = strategy.name
                            result.court_type = "실내 코트" if court_no in INDOOR_COURTS else "야외 코트"
                            
                            self.logger.info("=" * 60)
                            self.logger.info("🎉 예약 성공!")
                            self.logger.info(f"📅 날짜: {result.date}")
                            self.logger.info(f"⏰ 시간: {result.time_slot}")
                            self.logger.info(f"🎾 코트: {result.court_number}번 ({result.court_type})")
                            self.logger.info(f"⚡ 소요 시간: {elapsed:.2f}초")
                            self.logger.info("=" * 60)
                            
                            self.notifier.send_success(f"예약 완료 ({elapsed:.2f}초)", result)
                            return 0
                        
                        # 실패 시 다음 코트 시도
                        self.logger.info(f"⚠️ {message}, 다음 코트 시도...")
            
            # 모든 전략 실패
            result.error_message = "모든 전략 실패"
            self.notifier.send_failure("모든 전략 실패", result)
            return 1
            
        except Exception as e:
            self.logger.info(f"💥 예외 발생: {e}")
            import traceback
            traceback.print_exc()
            result.error_message = f"예외 발생: {e}"
            self.notifier.send_failure(f"예외 발생: {e}", result)
            return 1


def run_hybrid_bot(driver: webdriver.Chrome, config: Config, logger: Logger, notifier: SlackNotifier) -> int:
    """하이브리드 봇 실행 헬퍼 함수."""
    bot = HybridReservationBot(driver, config, logger, notifier)
    return bot.run()
