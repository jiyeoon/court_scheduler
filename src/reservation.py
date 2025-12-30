"""
Tennis court scheduler logic for Tennis Court.
Based on actual site structure analysis.
"""
import io
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    NoAlertPresentException,
)

from .config import Config, INDOOR_COURTS
from .notifier import Logger, SlackNotifier, ReservationResult


# 한국 시간대
KST = timezone(timedelta(hours=9))


class CaptchaSolver:
    """CAPTCHA solver using multiple OCR engines."""
    
    def __init__(self, logger: Logger):
        self.logger = logger
        self._ddddocr = None  # Lazy initialization
        self._easyocr_reader = None
    
    def preload(self) -> None:
        """
        Preload OCR engines to speed up CAPTCHA solving.
        Call this during bot initialization, before reservation opens.
        """
        self.logger.info("🔄 OCR 엔진 사전 로딩 시작...")
        
        # Preload ddddocr (primary engine) - 가장 빠르고 정확함
        try:
            import ddddocr
            self._ddddocr = ddddocr.DdddOcr(show_ad=False)
            self.logger.info("✅ ddddocr 사전 로딩 완료")
        except Exception as e:
            self.logger.info(f"⚠️ ddddocr 사전 로딩 실패: {e}")
        
        # easyocr는 사전 로딩하지 않음 (15초 소요)
        # ddddocr 실패 시에만 lazy loading
        
        self.logger.info("✅ OCR 엔진 사전 로딩 완료")
    
    def solve(self, image: Image.Image) -> str:
        """
        Solve CAPTCHA image using multiple OCR engines.
        
        Args:
            image: PIL Image of the CAPTCHA
            
        Returns:
            4-digit string or empty string if failed
        """
        result = ""
        
        # 1. Try ddddocr first (best for CAPTCHA)
        result = self._try_ddddocr(image)
        if result and len(result) == 4:
            return result
        
        # 2. Fallback to EasyOCR
        result = self._try_easyocr(image)
        if result and len(result) == 4:
            return result
        
        # 3. Final fallback to pytesseract
        result = self._try_pytesseract(image)
        if result and len(result) == 4:
            return result
        
        self.logger.info("❌ 모든 OCR 방법 실패")
        return ""
    
    def _try_ddddocr(self, image: Image.Image) -> str:
        """Try ddddocr for CAPTCHA recognition."""
        try:
            # Use preloaded instance or create new one
            if self._ddddocr is None:
                import ddddocr
                self.logger.info("🤖 ddddocr 초기화 중...")
                self._ddddocr = ddddocr.DdddOcr(show_ad=False)
            
            self.logger.info("🤖 ddddocr로 캡차 인식 중...")
            
            # PIL Image to bytes
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()
            
            result = self._ddddocr.classification(img_bytes)
            self.logger.info(f"🤖 ddddocr 결과: {result}")
            
            # Extract only digits
            result = re.sub(r'[^0-9]', '', result)
            self.logger.info(f"🤖 ddddocr 결과 (숫자만): {result}")
            
            # Handle 3-digit result
            if result and len(result) == 3:
                result = "0" + result
                self.logger.info(f"🔧 3자리 숫자 감지 - 앞에 0 추가: {result}")
            
            if result and len(result) == 4:
                return result
            else:
                self.logger.info(f"⚠️ ddddocr 실패 - {len(result) if result else 0}자리 숫자 (4자리 필요)")
                return ""
                
        except Exception as e:
            self.logger.info(f"❌ ddddocr 오류: {e}")
            return ""
    
    def _try_easyocr(self, image: Image.Image) -> str:
        """Try EasyOCR for CAPTCHA recognition."""
        try:
            import numpy as np
            
            # Use preloaded instance or create new one
            if self._easyocr_reader is None:
                import easyocr
                self.logger.info("🔄 EasyOCR 초기화 중...")
                self._easyocr_reader = easyocr.Reader(['en'], verbose=False)
            
            self.logger.info("🔄 EasyOCR fallback 시작...")
            
            # PIL Image to numpy array
            captcha_array = np.array(image)
            
            results = self._easyocr_reader.readtext(
                captcha_array,
                allowlist='0123456789',
                width_ths=0.7,
                height_ths=0.7,
                paragraph=False,
                batch_size=1
            )
            self.logger.info(f"🔤 EasyOCR 원본 결과: {results}")
            
            if results:
                # Select result with highest confidence
                best_result = max(results, key=lambda x: x[2])
                result = best_result[1]
                confidence = best_result[2]
                self.logger.info(f"🔤 EasyOCR 최고 확신도 결과: {result} (확신도: {confidence:.2f})")
                
                # Extract only digits
                result = re.sub(r'[^0-9]', '', result)
                self.logger.info(f"🔤 EasyOCR 결과 (숫자만): {result}")
                
                # Handle 3-digit result
                if result and len(result) == 3:
                    result = "0" + result
                    self.logger.info(f"🔧 3자리 숫자 감지 - 앞에 0 추가: {result}")
                
                if result and len(result) == 4:
                    return result
            
            self.logger.info(f"⚠️ EasyOCR 실패")
            return ""
            
        except Exception as e:
            self.logger.info(f"❌ EasyOCR 오류: {e}")
            return ""
    
    def _try_pytesseract(self, image: Image.Image) -> str:
        """Try pytesseract for CAPTCHA recognition."""
        try:
            import pytesseract
            
            self.logger.info("🔄 pytesseract fallback 시작...")
            
            configs = [
                r'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789',
                r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789',
                r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789',
                r'--oem 3 --psm 8',
                r'--oem 3 --psm 7'
            ]
            
            for i, config in enumerate(configs):
                try:
                    result = pytesseract.image_to_string(image, config=config).strip()
                    result = re.sub(r'[^0-9]', '', result)
                    self.logger.info(f"🔤 pytesseract 설정 {i+1} 결과 (숫자만): {result}")
                    
                    if result and len(result) == 4:
                        return result
                except Exception:
                    continue
            
            return ""
            
        except Exception as e:
            self.logger.info(f"❌ pytesseract 오류: {e}")
            return ""


class ReservationBot:
    """Tennis court reservation bot for KSPO Olympic Tennis Court."""
    
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
        self.target_time = datetime.now(KST).replace(
            hour=config.reservation.reservation_open_hour,
            minute=config.reservation.reservation_open_minute,
            second=0,
            microsecond=0
        )
        # 선택된 날짜/시간 정보 저장
        self.selected_date_str = ""
        self.selected_time_str = ""
    
    def login(self) -> bool:
        """Login to KSPO tennis reservation system."""
        self.logger.info(f"🔐 로그인 페이지로 이동, url: {self.config.login_url}")
        
        try:
            self.driver.get(self.config.login_url)
            
            # 페이지 로딩 대기
            time.sleep(2)
            
            self.logger.info("📝 로그인 정보 입력 중")
            # 로그인 폼 요소 대기
            login_id_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, 'login_id'))
            )
            login_id_input.send_keys(self.config.login_id)
            self.driver.find_element(By.NAME, 'login_pwd').send_keys(self.config.login_password)
            
            self.logger.info("🔘 로그인 버튼 클릭")
            button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="content"]/div/div/div/button'))
            )
            # Scroll and click
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
            max_wait = 30
            for i in range(max_wait):
                time.sleep(1)
                current_url = self.driver.current_url
                if "/sso/usr/login" not in current_url and "SSOService" not in current_url:
                    self.logger.info(f"✅ 로그인 완료 (URL: {current_url})")
                    return True
                if i == max_wait - 1:
                    self.logger.info(f"⚠️ 로그인 시간 초과. 현재 URL: {current_url}")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 로그인 실패: {e}")
            return False
    
    def navigate_to_reservation_page(self) -> bool:
        """Navigate to reservation page."""
        try:
            self.logger.info("🏠 메인 홈페이지 로딩 대기")
            WebDriverWait(self.driver, 60).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self.logger.info(f"현재 URL: {self.driver.current_url}")
            
            self.logger.info("🎾 예약하기 버튼 클릭")
            link = WebDriverWait(self.driver, 60).until(
                EC.element_to_be_clickable((By.LINK_TEXT, "일일입장 예약신청"))
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
            time.sleep(3)  # Wait for JS binding
            self.driver.execute_script("arguments[0].click();", link)
            self.logger.info("✅ 예약 페이지 진입 완료")
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 예약 페이지 진입 실패: {e}")
            self._debug_page_info()
            return False
    
    def wait_for_reservation_open(self) -> None:
        """Wait until 50ms before reservation opens at 09:00 KST.

        50ms 전에 새로고침을 시작하면 서버 응답이 9시 정각에 도착합니다.
        (네트워크 RTT 약 50-100ms 고려)
        GitHub Actions RTT가 약 1200ms이므로 절반인 600ms 선행
        """
        PRE_REFRESH_MS = 600  # 선행 시간 (밀리초)
        adjusted_target = self.target_time - timedelta(milliseconds=PRE_REFRESH_MS)
        
        self.logger.info(f"9시 {PRE_REFRESH_MS}ms 전까지 대기 시작...")
        current_time = datetime.now(KST)
        time_diff = (adjusted_target - current_time).total_seconds()
        
        if time_diff > 0:
            # Wait until 10 seconds before
            if time_diff > 10:
                sleep_time = time_diff - 10
                self.logger.info(f"9시 정각까지 {sleep_time:.1f}초 대기...")
                time.sleep(sleep_time)
            
            # Precise wait for last 10 seconds
            self.logger.info("🎯 마지막 10초 정밀 대기 시작...")
            loop_count = 0
            while True:
                current_time = datetime.now(KST)
                if current_time >= adjusted_target:
                    break
                loop_count += 1
                if loop_count > 20000000:  # Prevent infinite loop
                    self.logger.info("⚠️ 대기 시간이 너무 길어 강제 종료합니다.")
                    break
                time.sleep(0.0001)
            
            self.logger.info(f"🚀 9시 {PRE_REFRESH_MS}ms 전 도달! 새로고침 시작!")
        else:
            self.logger.info("이미 9시가 지났습니다. 즉시 실행합니다.")
    
    def refresh_and_wait_for_dates(self) -> bool:
        """Refresh page and wait for available dates."""
        try:
            self.logger.info("🔄 페이지 새로고침")
            self.driver.refresh()
            self.logger.info("✅ 페이지 새로고침 완료")
            
            self.logger.info("📅 예약 가능한 날짜 로딩 대기...")
            WebDriverWait(self.driver, 1000).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//tbody//a[starts-with(@href, 'javascript:fn_tennis_time_list')]")
                )
            )
            self.logger.info("✅ 예약 가능한 날짜 확인 완료")
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 페이지 새로고침 또는 날짜 로딩 실패: {e}")
            return False
    
    def select_latest_date(self) -> Optional[str]:
        """Select the latest available date."""
        try:
            self.logger.info("📅 예약 가능한 날짜 검색 중...")
            clickable_dates = self.driver.find_elements(
                By.XPATH,
                "//tbody//a[starts-with(@href, 'javascript:fn_tennis_time_list')]"
            )
            
            if not clickable_dates:
                self.logger.info("❌ 클릭 가능한 날짜가 없음")
                return None
            
            # Select the last (latest) date
            target = clickable_dates[-1]
            self.driver.execute_script("arguments[0].scrollIntoView(true);", target)
            time.sleep(0.1)
            self.driver.execute_script("arguments[0].click();", target)
            
            # href에서 날짜 추출: javascript:fn_tennis_time_list('2025', '01', '05')
            href = target.get_attribute('href')
            date_match = re.search(r"fn_tennis_time_list\('(\d+)',\s*'(\d+)',\s*'(\d+)'\)", href)
            if date_match:
                year, month, day = date_match.groups()
                date_text = f"{year}-{month}-{day}"
            else:
                # fallback: 텍스트의 첫 줄만 사용
                date_text = target.text.split('\n')[0] if target.text else "날짜 불명"
            
            full_text = target.text.replace('\n', '/')
            self.logger.info(f"✅ 예약 가능한 날짜 클릭: {date_text} ({full_text})")
            return date_text
            
        except Exception as e:
            self.logger.info(f"❌ 날짜 선택 실패: {e}")
            return None
    
    def select_time_slots_by_hour(self, target_hour: int, count: int, preferred_courts: list = None) -> Tuple[bool, List[int]]:
        """
        Select time slots starting from a specific hour.
        각 시간 선택 후 가용 코트를 확인하고 교집합을 반환합니다.
        
        시간 슬롯 인덱스 규칙:
        - 06시 = index 0, 19시 = index 13, 21시 = index 15
        
        Args:
            target_hour: Starting hour (e.g., 19 for 19:00)
            count: Number of slots to select
            preferred_courts: List of court numbers to check for availability
            
        Returns:
            Tuple of (success, common_available_courts)
        """
        if preferred_courts is None:
            preferred_courts = []
            
        try:
            # 시간 → 인덱스 변환 (06시 = 0, 19시 = 13, 21시 = 15)
            base_hour = 6
            start_index = target_hour - base_hour
            
            self.logger.info(f"⏰ {target_hour}시-{target_hour + count}시 시간대 선택 중...")
            self.logger.info(f"🔍 선택할 인덱스: {[start_index + i for i in range(count)]}")
            
            # 시간 슬롯 로딩 대기
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'ul#time_con li'))
            )
            time.sleep(0.5)  # 추가 대기
            
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            self.logger.info(f"📋 총 {len(time_slots)}개의 시간 슬롯 발견")
            
            click_count = 0
            common_courts = set(preferred_courts) if preferred_courts else set()
            
            for i in range(count):
                slot_index = start_index + i
                slot_hour = target_hour + i
                
                # 인덱스로 직접 접근
                if slot_index >= len(time_slots):
                    self.logger.info(f"❌ {slot_hour}시 슬롯 인덱스({slot_index})가 범위를 벗어남")
                    self._clear_time_selections()
                    return False, []
                
                try:
                    slot = time_slots[slot_index]
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    status_label = slot.find_element(By.CSS_SELECTOR, 'span.label')
                    
                    if checkbox.is_enabled() and "신청가능" in status_label.text:
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        click_count += 1
                        self.logger.info(f"✅ {slot_hour}시-{slot_hour + 1}시 선택 완료")
                        
                        # 첫 번째 슬롯에서 날짜 정보 추출 (label 텍스트: "1월 5일 (15:00 ~ 16:00)")
                        if i == 0:
                            try:
                                label_elem = slot.find_element(By.CSS_SELECTOR, 'label')
                                label_text = label_elem.text
                                # "1월 5일" 부분 추출
                                date_match = re.search(r'(\d+월\s*\d+일)', label_text)
                                if date_match:
                                    self.selected_date_str = date_match.group(1)
                                    self.logger.info(f"   └ 날짜 정보: {self.selected_date_str}")
                            except Exception:
                                pass
                        
                        # 각 시간 선택 후 가용 코트 확인
                        if preferred_courts:
                            time.sleep(0.3)  # 코트 상태 업데이트 대기
                            available = self.get_available_courts(preferred_courts)
                            self.logger.info(f"   └ {slot_hour}시 가용 코트: {available}")
                            
                            if i == 0:
                                common_courts = set(available)
                            else:
                                common_courts = common_courts.intersection(set(available))
                    else:
                        self.logger.info(f"⏳ {slot_hour}시-{slot_hour + 1}시 예약 불가 (마감)")
                        self._clear_time_selections()
                        return False, []
                        
                except Exception as e:
                    self.logger.info(f"❌ {slot_hour}시 선택 중 오류: {e}")
                    self._clear_time_selections()
                    return False, []
            
            if click_count < count:
                self.logger.info(f"⚠️ {click_count}개만 선택됨 (목표: {count}개)")
                self._clear_time_selections()
                return False, []
            
            # 교집합을 우선순위 순서로 정렬
            common_courts_ordered = [c for c in preferred_courts if c in common_courts] if preferred_courts else []
            
            self.logger.info(f"✅ 시간 선택 완료: {target_hour}시-{target_hour + count}시")
            if preferred_courts:
                self.logger.info(f"✅ 교집합 코트 (모든 시간 가능): {common_courts_ordered}")
            
            return True, common_courts_ordered
            
        except Exception as e:
            self.logger.info(f"❌ 시간 선택 실패: {e}")
            # 예외 발생 시에도 alert 처리
            try:
                alert = self.driver.switch_to.alert
                alert.accept()
            except NoAlertPresentException:
                pass
            return False, []
    
    def _clear_time_selections(self) -> None:
        """Clear all selected time slots."""
        try:
            self.logger.info("🔄 시간 선택 초기화 중...")
            
            # 먼저 alert가 있으면 처리
            try:
                alert = self.driver.switch_to.alert
                self.logger.info(f"ℹ️ 사전 Alert 처리: {alert.text}")
                alert.accept()
            except NoAlertPresentException:
                pass
            
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            cleared_count = 0
            for slot in time_slots:
                try:
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    if checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        cleared_count += 1
                        # 체크 해제 시 alert 발생할 수 있음
                        try:
                            alert = self.driver.switch_to.alert
                            self.logger.info(f"ℹ️ 체크 해제 Alert 처리: {alert.text}")
                            alert.accept()
                        except NoAlertPresentException:
                            pass
                except Exception:
                    continue
            
            if cleared_count > 0:
                self.logger.info(f"✅ {cleared_count}개 시간 슬롯 선택 해제 완료")
        except Exception as e:
            self.logger.info(f"⚠️ 시간 선택 초기화 중 오류: {e}")
    
    def get_available_courts(self, preferred_courts: list) -> List[int]:
        """
        Get list of available courts.
        시간 선택 후 현재 상태에서 예약 가능한 코트 목록을 반환합니다.
        (시간을 2개 선택하면 코트 이미지 상태가 자동으로 두 시간 모두 가용 여부를 반영함)
        
        Args:
            preferred_courts: List of court numbers to check
            
        Returns:
            List of available court numbers
        """
        available = []
        
        # 빠른 확인을 위해 implicit wait 일시적으로 비활성화
        original_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        
        try:
            for court_num in preferred_courts:
                try:
                    court_id = f'tennis_court_img_a_1_{court_num}'
                    # find_elements는 없으면 빈 리스트 반환 (대기 없음)
                    courts = self.driver.find_elements(By.ID, court_id)
                    if not courts:
                        continue
                    court = courts[0]
                    img_elements = court.find_elements(By.TAG_NAME, 'img')
                    if not img_elements:
                        continue
                    if 'btn_tennis_noreserve' not in img_elements[0].get_attribute('src'):
                        available.append(court_num)
                except Exception:
                    continue
        finally:
            # implicit wait 복구
            self.driver.implicitly_wait(original_wait)
        
        return available
    
    def select_court_from_common(self, common_courts: list) -> Optional[int]:
        """
        Select court from pre-calculated common (intersection) courts.
        이미 교집합으로 계산된 코트 목록에서 순서대로 선택을 시도합니다.
        
        Args:
            common_courts: List of court numbers (already filtered by intersection)
            
        Returns:
            Selected court number or None if failed
        """
        if not common_courts:
            self.logger.info("❌ 선택 가능한 코트 없음")
            return None
            
        self.logger.info(f"🎾 코트 선택 시도 (대상: {common_courts})")
        
        for court_num in common_courts:
            try:
                self.logger.info(f"🔍 코트 {court_num} 선택 시도...")
                
                court_id = f'tennis_court_img_a_1_{court_num}'
                court = self.driver.find_element(By.ID, court_id)
                self.driver.execute_script("arguments[0].click();", court)
                self.logger.info(f"✅ 코트 {court_num} 클릭됨")
                
                # Check for alert (court already reserved)
                try:
                    time.sleep(0.3)
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    self.logger.info(f"⚠️ 알림창 감지: {alert_text}")
                    
                    if "예약이 완료된 코트입니다" in alert_text:
                        alert.accept()
                        self.logger.info(f"❌ 코트 {court_num} 이미 예약 완료 - 다음 코트 시도")
                        continue
                    else:
                        alert.accept()
                        self.logger.info(f"✅ 알림창 처리 완료: {alert_text}")
                        
                except NoAlertPresentException:
                    pass
                
                self.logger.info(f"✅ 코트 {court_num} 선택 완료!")
                return court_num
                    
            except Exception as e:
                self.logger.info(f"⚠️ 코트 {court_num} 확인 중 오류: {e}")
                continue
        
        self.logger.info("❌ 예약 가능한 코트가 없음")
        return None
    
    def solve_captcha_and_confirm(self) -> bool:
        """Solve CAPTCHA and confirm reservation."""
        try:
            self.logger.info("🔍 캡차 이미지 로딩 대기...")
            
            # 캡차 이미지가 표시될 때까지 대기 (visibility, not just presence)
            captcha_element = WebDriverWait(self.driver, 60).until(
                EC.visibility_of_element_located(
                    (By.XPATH, '//*[@id="layer_captcha_wrap"]/div/img')
                )
            )
            
            # 이미지가 완전히 로드될 때까지 추가 대기 (width > 0 확인)
            for _ in range(10):
                try:
                    size = captcha_element.size
                    if size['width'] > 0 and size['height'] > 0:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            
            # 스크린샷 전 안전을 위한 짧은 대기
            time.sleep(0.3)
            
            # Get CAPTCHA image as PIL Image
            captcha_image = Image.open(io.BytesIO(captcha_element.screenshot_as_png))
            
            # Solve CAPTCHA
            captcha_result = self.captcha_solver.solve(captcha_image)
            
            if not captcha_result:
                self.logger.info("❌ 캡차 인식 실패")
                return False
            
            # Enter CAPTCHA and confirm
            self.driver.find_element(By.ID, 'captcha').send_keys(captcha_result)
            self.driver.find_element(By.ID, 'date_confirm').click()
            self.logger.info("✅ 캡차 입력 완료")
            
            # Wait for payment alert
            self.logger.info("💳 결제대기 알림창 대기 중...")
            WebDriverWait(self.driver, 10).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            self.logger.info(f"💳 결제대기 알림창 감지: {alert_text}")
            alert.accept()
            self.logger.info("✅ 결제대기 알림창 확인 완료")
            
            return True
            
        except Exception as e:
            self.logger.info(f"❌ OCR 처리 중 오류 발생: {e}")
            return False
    
    def verify_reservation(self) -> Tuple[bool, str]:
        """Verify reservation success and get details."""
        try:
            self.logger.info("📋 예약 확인 알림 처리")
            
            # Check for additional alerts (usually means failure)
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                self.logger.info(f"❌ 추가 알림창 감지: {alert_text}")
                self.logger.info("⚠️ 추가 알림창이 있으면 보통 예약이 실패한 것입니다!")
                alert.accept()
                return False, alert_text
            except NoAlertPresentException:
                self.logger.info("ℹ️ 추가 알림창 없음 - 예약 진행 중")
            
            # Verify cart contents
            self.logger.info("🛒 장바구니 담기 확인 중...")
            time.sleep(2)
            
            basket = self.driver.find_element(By.XPATH, '//*[@id="aplictn_info"]/ul')
            items = basket.find_elements(By.TAG_NAME, 'li')
            
            content = []
            for item in items:
                content.append(item.text.split('\n')[-1])
            
            message = '\n'.join(content)
            self.logger.info("🎉 장바구니 담기 성공!")
            self.logger.info(f"📝 예약 내용: {message}")
            
            return True, message
            
        except Exception as e:
            self.logger.info(f"⚠️ 장바구니 확인 실패: {e}")
            return False, str(e)
    
    def _debug_page_info(self) -> None:
        """Collect debug information when error occurs."""
        try:
            current_url = self.driver.current_url
            page_title = self.driver.title
            self.logger.info(f"📍 현재 URL: {current_url}")
            self.logger.info(f"📄 페이지 제목: {page_title}")
            
            # Save screenshot
            screenshot_path = "/tmp/error_screenshot.png"
            self.driver.save_screenshot(screenshot_path)
            self.logger.info(f"📸 에러 스크린샷 저장: {screenshot_path}")
            
            # Find all links
            all_links = self.driver.find_elements(By.TAG_NAME, "a")
            self.logger.info(f"🔍 페이지의 링크 개수: {len(all_links)}")
            
            for i, link in enumerate(all_links[:20]):
                try:
                    link_text = link.text
                    if link_text and ("예약" in link_text or "입장" in link_text):
                        self.logger.info(f"  링크 {i+1}: {link_text}")
                except Exception:
                    pass
                    
        except Exception as e:
            self.logger.info(f"⚠️ 디버깅 정보 수집 실패: {e}")
    
    def _dismiss_alert_if_present(self) -> None:
        """Dismiss any alert that might be present."""
        try:
            alert = self.driver.switch_to.alert
            self.logger.info(f"ℹ️ Alert 자동 처리: {alert.text}")
            alert.accept()
        except NoAlertPresentException:
            pass
    
    def select_latest_available_time_slots(self, count: int, preferred_courts: list = None, exclude_hours: set = None) -> Tuple[bool, Optional[int], List[int]]:
        """
        Select the latest available consecutive time slots.
        뒤에서부터 탐색하여 연속으로 예약 가능한 시간대를 찾고, 가용 코트 교집합을 반환합니다.
        
        Args:
            count: Number of consecutive slots needed
            preferred_courts: List of court numbers to check for availability
            exclude_hours: Set of start hours to skip (already tried)
            
        Returns:
            Tuple of (success, start_hour, common_available_courts)
        """
        if exclude_hours is None:
            exclude_hours = set()
        if preferred_courts is None:
            preferred_courts = []
            
        try:
            # 시작 전 alert 처리
            self._dismiss_alert_if_present()
            
            if exclude_hours:
                self.logger.info(f"⏰ 다음 연속 {count}시간 탐색 중... (제외: {sorted(exclude_hours, reverse=True)}시)")
            else:
                self.logger.info(f"⏰ 가능한 가장 늦은 연속 {count}시간 탐색 중...")
            
            # 시간 슬롯 로딩 대기
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'ul#time_con li'))
            )
            time.sleep(0.5)
            
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            total_slots = len(time_slots)
            self.logger.info(f"📋 총 {total_slots}개의 시간 슬롯 발견")
            
            base_hour = 6  # 06시 = index 0
            
            # 뒤에서부터 탐색 (가장 늦은 시간부터)
            for start_index in range(total_slots - count, -1, -1):
                start_hour = base_hour + start_index
                
                # 이미 시도한 시간대는 건너뛰기
                if start_hour in exclude_hours:
                    continue
                    
                self.logger.info(f"🔍 {start_hour}시-{start_hour + count}시 확인 중...")
                
                # 연속된 슬롯이 모두 예약 가능한지 확인
                all_available = True
                for i in range(count):
                    slot_index = start_index + i
                    slot = time_slots[slot_index]
                    
                    try:
                        checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                        status_label = slot.find_element(By.CSS_SELECTOR, 'span.label')
                        
                        if not (checkbox.is_enabled() and "신청가능" in status_label.text):
                            all_available = False
                            break
                    except Exception:
                        all_available = False
                        break
                
                if all_available:
                    # 예약 가능한 연속 시간대 발견! 선택 진행하면서 가용 코트 확인
                    self.logger.info(f"✅ {start_hour}시-{start_hour + count}시 예약 가능!")
                    
                    common_courts = set(preferred_courts) if preferred_courts else set()
                    
                    for i in range(count):
                        slot_index = start_index + i
                        slot_hour = start_hour + i
                        slot = time_slots[slot_index]
                        checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        self.logger.info(f"✅ {slot_hour}시-{slot_hour + 1}시 선택 완료")
                        # 클릭 후 alert 처리
                        self._dismiss_alert_if_present()
                        
                        # 첫 번째 슬롯에서 날짜 정보 추출
                        if i == 0:
                            try:
                                label_elem = slot.find_element(By.CSS_SELECTOR, 'label')
                                label_text = label_elem.text
                                date_match = re.search(r'(\d+월\s*\d+일)', label_text)
                                if date_match:
                                    self.selected_date_str = date_match.group(1)
                                    self.logger.info(f"   └ 날짜 정보: {self.selected_date_str}")
                            except Exception:
                                pass
                        
                        # 각 시간 선택 후 가용 코트 확인
                        if preferred_courts:
                            time.sleep(0.3)  # 코트 상태 업데이트 대기
                            available = self.get_available_courts(preferred_courts)
                            self.logger.info(f"   └ {slot_hour}시 가용 코트: {available}")
                            
                            if i == 0:
                                common_courts = set(available)
                            else:
                                common_courts = common_courts.intersection(set(available))
                    
                    # 교집합을 우선순위 순서로 정렬
                    common_courts_ordered = [c for c in preferred_courts if c in common_courts] if preferred_courts else []
                    
                    if preferred_courts:
                        self.logger.info(f"✅ 교집합 코트 (모든 시간 가능): {common_courts_ordered}")
                    
                    return True, start_hour, common_courts_ordered
            
            self.logger.info("❌ 예약 가능한 연속 시간대를 찾을 수 없음")
            return False, None, []
            
        except Exception as e:
            self.logger.info(f"❌ 시간 자동 탐색 실패: {e}")
            # 예외 발생 시에도 alert 처리
            self._dismiss_alert_if_present()
            return False, None, []
    
    def _try_strategy(self, strategy, selected_date: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Try a single reservation strategy.
        
        Args:
            strategy: ReservationStrategy to try
            selected_date: Already selected date
            
        Returns:
            Tuple of (success, court_number, error_message)
        """
        self.logger.info(f"🎯 전략 시도: {strategy.name}")
        
        if strategy.auto_find_latest:
            # 자동 탐색: 가능한 시간대를 뒤에서부터 반복 시도
            tried_hours = set()
            
            while True:
                # 1. 시간 선택 + 가용 코트 교집합 확인 (이미 시도한 시간대 제외)
                success, found_hour, common_courts = self.select_latest_available_time_slots(
                    strategy.time_slot_count,
                    preferred_courts=strategy.preferred_courts,
                    exclude_hours=tried_hours
                )
                if not success:
                    return False, None, "가능한 연속 시간대 없음"
                
                tried_hours.add(found_hour)
                
                # 2. 교집합 코트가 없으면 다음 시간대 시도
                if not common_courts:
                    self._clear_time_selections()
                    self.logger.info(f"🔄 {found_hour}시-{found_hour + strategy.time_slot_count}시에서 교집합 코트 없음, 다음 시간대 시도...")
                    continue
                
                # 3. 교집합 코트에서 선택 시도
                selected_court = self.select_court_from_common(common_courts)
                if selected_court:
                    self.logger.info(f"✅ 전략 '{strategy.name}' 성공: {found_hour}시-{found_hour + strategy.time_slot_count}시, 코트 {selected_court}")
                    return True, selected_court, None
                
                # 4. 코트 선택 실패시 시간 선택 취소하고 다음 시간대 시도
                self._clear_time_selections()
                self.logger.info(f"🔄 {found_hour}시-{found_hour + strategy.time_slot_count}시에서 코트 선택 실패, 다음 시간대 시도...")
        else:
            # 지정된 시간대 선택 + 가용 코트 교집합 확인
            success, common_courts = self.select_time_slots_by_hour(
                strategy.target_hour, 
                strategy.time_slot_count,
                preferred_courts=strategy.preferred_courts
            )
            if not success:
                return False, None, f"{strategy.target_hour}시 시간대 선택 실패"
            
            # 교집합 코트가 없으면 실패
            if not common_courts:
                self._clear_time_selections()
                return False, None, f"{strategy.target_hour}시 시간대에서 교집합 코트 없음"
            
            # 교집합 코트에서 선택 시도
            selected_court = self.select_court_from_common(common_courts)
            if not selected_court:
                self._clear_time_selections()
                return False, None, f"코트 선택 실패 (대상: {strategy.preferred_courts})"
            
            self.logger.info(f"✅ 전략 '{strategy.name}' 성공: 코트 {selected_court}")
            return True, selected_court, None
    
    def run(self) -> int:
        """
        Run the full reservation process with multiple strategies.
        
        Returns:
            0 for success, 1 for failure
        """
        self.logger.info("🎾 Court Scheduler Started")
        
        # 예약 결과 추적
        result = ReservationResult()
        
        strategies = self.config.reservation.strategies
        self.logger.info(f"📋 예약 전략 목록:")
        for i, s in enumerate(strategies, 1):
            if s.auto_find_latest:
                time_desc = f"가능한 늦은 연속 {s.time_slot_count}시간"
            else:
                time_desc = f"{s.target_hour}시-{s.target_hour + s.time_slot_count}시"
            self.logger.info(f"✔️ {i}순위: {s.name} ({time_desc}, 코트: {len(s.preferred_courts)}개)")
        
        try:
            # 1. Login
            if not self.login():
                result.error_message = "로그인 실패"
                self.notifier.send_failure("로그인 실패", result)
                return 1
            
            # 2. Preload OCR engines (로그인 직후 바로 시작 - 페이지 진입/대기 중 로딩)
            self.captcha_solver.preload()
            
            # 3. Navigate to reservation page
            if not self.navigate_to_reservation_page():
                result.error_message = "예약 페이지 진입 실패"
                self.notifier.send_failure("예약 페이지 진입 실패", result)
                return 1
            
            # 4. Wait for 09:00
            self.wait_for_reservation_open()
            
            # 5. Refresh and wait for dates
            if not self.refresh_and_wait_for_dates():
                result.error_message = "날짜 로딩 실패"
                self.notifier.send_failure("날짜 로딩 실패", result)
                return 1
            
            # 6. Select latest date
            selected_date = self.select_latest_date()
            if not selected_date:
                result.error_message = "날짜 선택 실패"
                self.notifier.send_failure("날짜 선택 실패", result)
                return 1
            
            result.date = selected_date
            
            # 7. Try each strategy in order
            selected_court = None
            selected_time_slot = ""
            successful_strategy = None
            last_error = ""
            
            for strategy in strategies:
                result.tried_strategies.append(strategy.name)
                success, court, error = self._try_strategy(strategy, selected_date)
                if success:
                    selected_court = court
                    successful_strategy = strategy
                    # 시간대 정보 생성
                    if strategy.auto_find_latest:
                        selected_time_slot = "자동 탐색된 시간"
                    else:
                        selected_time_slot = f"{strategy.target_hour}:00-{strategy.target_hour + strategy.time_slot_count}:00"
                    break
                else:
                    last_error = error
                    self.logger.info(f"⚠️ 전략 '{strategy.name}' 실패: {error}")
                    self.logger.info("🔄 다음 전략 시도...")
            
            if not selected_court:
                result.error_message = f"모든 전략 실패. 마지막 오류: {last_error}"
                self.notifier.send_failure(f"모든 전략 실패. 마지막 오류: {last_error}", result)
                return 1
            
            # 결과 정보 업데이트
            result.court_number = selected_court
            result.time_slot = selected_time_slot
            result.strategy_name = successful_strategy.name
            result.court_type = "실내 코트" if selected_court in INDOOR_COURTS else "야외 코트"
            # 시간 슬롯 label에서 추출한 정확한 날짜 정보로 업데이트
            if self.selected_date_str:
                result.date = self.selected_date_str
            
            self.logger.info("✅ 코트 선택 완료, OCR 처리 시작")
            
            # 8. Solve CAPTCHA and confirm
            if not self.solve_captcha_and_confirm():
                result.error_message = "캡차 인식 또는 확인 실패"
                self.notifier.send_failure("캡차 인식 또는 확인 실패", result)
                return 1
            
            # 9. Verify reservation
            success, message = self.verify_reservation()
            
            if success:
                result.success = True
                self.notifier.send_success(message, result)
                self.logger.info("=" * 50)
                self.logger.info("✅ 예약 성공!")
                self.logger.info(f"📅 날짜: {result.date}")
                self.logger.info(f"⏰ 시간: {result.time_slot}")
                self.logger.info(f"🎾 코트: {result.court_number}번 ({result.court_type})")
                self.logger.info("=" * 50)
                return 0
            else:
                result.error_message = f"예약 확인 실패: {message}"
                self.notifier.send_failure(f"예약 확인 실패: {message}", result)
                return 1
                
        except Exception as e:
            self.logger.info(f"💥 예외 발생: {e}")
            result.error_message = f"예외 발생: {e}"
            self.notifier.send_failure(f"예외 발생: {e}", result)
            return 1
