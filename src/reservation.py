"""
Tennis court reservation logic for KSPO Olympic Tennis Court.
Based on actual site structure analysis.
"""
import io
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

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

from .config import Config
from .notifier import Logger, SlackNotifier


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
        """Wait until reservation opens at 09:00 KST."""
        self.logger.info("9시 정각까지 대기 시작...")
        current_time = datetime.now(KST)
        time_diff = (self.target_time - current_time).total_seconds()
        
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
                if current_time >= self.target_time:
                    break
                loop_count += 1
                if loop_count > 20000000:  # Prevent infinite loop
                    self.logger.info("⚠️ 대기 시간이 너무 길어 강제 종료합니다.")
                    break
                time.sleep(0.0001)
            
            self.logger.info("9시 정각 도달!")
        else:
            self.logger.info("이미 9시가 지났습니다. 즉시 실행합니다.")
    
    def refresh_and_wait_for_dates(self) -> bool:
        """Refresh page and wait for available dates."""
        try:
            self.logger.info("🔄 페이지 새로고침")
            self.driver.refresh()
            self.logger.info("✅ 페이지 새로고침 완료")
            
            self.logger.info("📅 예약 가능한 날짜 로딩 대기...")
            WebDriverWait(self.driver, 300).until(
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
            
            date_text = target.text.replace('\n', '/')
            self.logger.info(f"✅ 예약 가능한 날짜 클릭: {date_text}")
            return date_text
            
        except Exception as e:
            self.logger.info(f"❌ 날짜 선택 실패: {e}")
            return None
    
    def select_time_slots_by_hour(self, target_hour: int, count: int) -> bool:
        """
        Select time slots starting from a specific hour.
        
        시간 슬롯 인덱스 규칙:
        - 06시 = index 0 (datetimeType01_0)
        - 07시 = index 1 (datetimeType01_1)
        - ...
        - 19시 = index 13 (datetimeType01_13)
        - 20시 = index 14 (datetimeType01_14)
        - 21시 = index 15 (datetimeType01_15)
        
        Args:
            target_hour: Starting hour (e.g., 19 for 19:00)
            count: Number of slots to select
            
        Returns:
            True if successful, False otherwise
        """
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
            
            for i in range(count):
                slot_index = start_index + i
                slot_hour = target_hour + i
                
                # 인덱스로 직접 접근
                if slot_index >= len(time_slots):
                    self.logger.info(f"❌ {slot_hour}시 슬롯 인덱스({slot_index})가 범위를 벗어남")
                    self._clear_time_selections()
                    return False
                
                try:
                    slot = time_slots[slot_index]
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    status_label = slot.find_element(By.CSS_SELECTOR, 'span.label')
                    
                    if checkbox.is_enabled() and "신청가능" in status_label.text:
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        click_count += 1
                        self.logger.info(f"✅ {slot_hour}시-{slot_hour + 1}시 선택 완료")
                    else:
                        self.logger.info(f"⏳ {slot_hour}시-{slot_hour + 1}시 예약 불가 (마감)")
                        self._clear_time_selections()
                        return False
                        
                except Exception as e:
                    self.logger.info(f"❌ {slot_hour}시 선택 중 오류: {e}")
                    self._clear_time_selections()
                    return False
            
            if click_count < count:
                self.logger.info(f"⚠️ {click_count}개만 선택됨 (목표: {count}개)")
                self._clear_time_selections()
                return False
            
            self.logger.info(f"✅ 시간 선택 완료: {target_hour}시-{target_hour + count}시 ({click_count}개)")
            return True
            
        except Exception as e:
            self.logger.info(f"❌ 시간 선택 실패: {e}")
            return False
    
    def _clear_time_selections(self) -> None:
        """Clear all selected time slots."""
        try:
            time_slots = self.driver.find_elements(By.CSS_SELECTOR, 'ul#time_con li')
            for slot in time_slots:
                try:
                    checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                    if checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                except Exception:
                    continue
        except Exception:
            pass
    
    def select_court_from_list(self, preferred_courts: list) -> Optional[int]:
        """
        Select available court from a specific list.
        
        Args:
            preferred_courts: List of court numbers to try (in priority order)
            
        Returns:
            Selected court number or None if failed
        """
        try:
            self.logger.info("🏟️ 코트 목록 로딩 대기...")
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, 'ul.court_list li')
                )
            )
            
            court_list = self.driver.find_elements(By.CSS_SELECTOR, 'ul.court_list li')
            if court_list:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", court_list[0])
            
            self.logger.info(f"🎾 코트 검색 시작 (대상: {preferred_courts})")
            
            for court_num in preferred_courts:
                try:
                    self.logger.info(f"🔍 코트 {court_num} 확인 중...")
                    
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(
                            (By.ID, f'tennis_court_img_a_1_{court_num}')
                        )
                    )
                    court = self.driver.find_element(By.ID, f'tennis_court_img_a_1_{court_num}')
                    img_element = court.find_element(By.TAG_NAME, 'img')
                    
                    # Check if court is available (not showing 'noreserve' image)
                    if 'btn_tennis_noreserve' not in img_element.get_attribute('src'):
                        self.driver.execute_script("arguments[0].click();", court)
                        self.logger.info(f"✅ 코트 {court_num} 선택됨")
                        
                        # Check for alert (court already reserved)
                        try:
                            time.sleep(0.5)
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
                            self.logger.info("ℹ️ 알림창 없음")
                        
                        return court_num
                    else:
                        self.logger.info(f"⏳ 코트 {court_num} 예약 불가")
                        
                except Exception as e:
                    self.logger.info(f"⚠️ 코트 {court_num} 확인 중 오류: {e}")
                    continue
            
            self.logger.info("❌ 예약 가능한 코트가 없음")
            return None
            
        except Exception as e:
            self.logger.info(f"❌ 코트 선택 실패: {e}")
            return None
    
    def solve_captcha_and_confirm(self) -> bool:
        """Solve CAPTCHA and confirm reservation."""
        try:
            self.logger.info("🔍 캡차 이미지 로딩 대기...")
            WebDriverWait(self.driver, 60).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="layer_captcha_wrap"]/div/img')
                )
            )
            
            captcha_element = self.driver.find_element(
                By.XPATH,
                '//*[@id="layer_captcha_wrap"]/div/img'
            )
            
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
    
    def select_latest_available_time_slots(self, count: int, exclude_hours: set = None) -> Tuple[bool, Optional[int]]:
        """
        Select the latest available consecutive time slots.
        뒤에서부터 탐색하여 연속으로 예약 가능한 시간대를 찾습니다.
        
        Args:
            count: Number of consecutive slots needed
            exclude_hours: Set of start hours to skip (already tried)
            
        Returns:
            Tuple of (success, start_hour)
        """
        if exclude_hours is None:
            exclude_hours = set()
            
        try:
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
                    # 예약 가능한 연속 시간대 발견! 선택 진행
                    self.logger.info(f"✅ {start_hour}시-{start_hour + count}시 예약 가능!")
                    
                    for i in range(count):
                        slot_index = start_index + i
                        slot = time_slots[slot_index]
                        checkbox = slot.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        self.logger.info(f"✅ {start_hour + i}시-{start_hour + i + 1}시 선택 완료")
                    
                    return True, start_hour
            
            self.logger.info("❌ 예약 가능한 연속 시간대를 찾을 수 없음")
            return False, None
            
        except Exception as e:
            self.logger.info(f"❌ 시간 자동 탐색 실패: {e}")
            return False, None
    
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
                # 1. 시간 선택 (이미 시도한 시간대 제외)
                success, found_hour = self.select_latest_available_time_slots(
                    strategy.time_slot_count, 
                    exclude_hours=tried_hours
                )
                if not success:
                    return False, None, "가능한 연속 시간대 없음"
                
                tried_hours.add(found_hour)
                
                # 2. 코트 선택 시도
                selected_court = self.select_court_from_list(strategy.preferred_courts)
                if selected_court:
                    self.logger.info(f"✅ 전략 '{strategy.name}' 성공: {found_hour}시-{found_hour + strategy.time_slot_count}시, 코트 {selected_court}")
                    return True, selected_court, None
                
                # 3. 코트 없으면 시간 선택 취소하고 다음 시간대 시도
                self._clear_time_selections()
                self.logger.info(f"🔄 {found_hour}시-{found_hour + strategy.time_slot_count}시에서 코트 없음, 다음 시간대 시도...")
        else:
            # 지정된 시간대 선택
            if not self.select_time_slots_by_hour(strategy.target_hour, strategy.time_slot_count):
                return False, None, f"{strategy.target_hour}시 시간대 선택 실패"
            
            # 코트 선택
            selected_court = self.select_court_from_list(strategy.preferred_courts)
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
                self.notifier.send_failure("로그인 실패")
                return 1
            
            # 2. Navigate to reservation page
            if not self.navigate_to_reservation_page():
                self.notifier.send_failure("예약 페이지 진입 실패")
                return 1
            
            # 3. Preload OCR engines (while waiting for 09:00)
            self.captcha_solver.preload()
            
            # 4. Wait for 09:00
            self.wait_for_reservation_open()
            
            # 5. Refresh and wait for dates
            if not self.refresh_and_wait_for_dates():
                self.notifier.send_failure("날짜 로딩 실패")
                return 1
            
            # 6. Select latest date
            selected_date = self.select_latest_date()
            if not selected_date:
                self.notifier.send_failure("날짜 선택 실패")
                return 1
            
            # 7. Try each strategy in order
            selected_court = None
            last_error = ""
            
            for strategy in strategies:
                success, court, error = self._try_strategy(strategy, selected_date)
                if success:
                    selected_court = court
                    break
                else:
                    last_error = error
                    self.logger.info(f"⚠️ 전략 '{strategy.name}' 실패: {error}")
                    self.logger.info("🔄 다음 전략 시도...")
            
            if not selected_court:
                self.notifier.send_failure(f"모든 전략 실패. 마지막 오류: {last_error}")
                return 1
            
            self.logger.info("✅ 코트 선택 완료, OCR 처리 시작")
            
            # 8. Solve CAPTCHA and confirm
            if not self.solve_captcha_and_confirm():
                self.notifier.send_failure("캡차 인식 또는 확인 실패")
                return 1
            
            # 9. Verify reservation
            success, message = self.verify_reservation()
            
            if success:
                self.notifier.send_success(message)
                self.logger.info("=" * 50)
                self.logger.info("✅ 예약 성공!")
                self.logger.info("=" * 50)
                return 0
            else:
                self.notifier.send_failure(f"예약 확인 실패: {message}")
                return 1
                
        except Exception as e:
            self.logger.info(f"💥 예외 발생: {e}")
            self.notifier.send_failure(f"예약 발생: {e}")
            return 1
