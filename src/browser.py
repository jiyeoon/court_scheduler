"""
Chrome WebDriver configuration for Court Scheduler.
"""
import os
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from .config import Config


def is_display_available() -> bool:
    """Check if display is available (macOS/Linux)."""
    # GitHub Actions에서는 항상 headless
    if os.getenv("GITHUB_ACTIONS"):
        return False
    
    try:
        # macOS: Check WindowServer process
        result = subprocess.run(
            ['pgrep', '-f', 'WindowServer'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def create_driver(config: Config) -> webdriver.Chrome:
    """
    Create and configure Chrome WebDriver.
    
    Args:
        config: Application configuration
        
    Returns:
        Configured Chrome WebDriver instance
    """
    options = Options()
    
    # 🚀 페이지 로드 전략: eager = DOM만 로드되면 진행 (이미지/CSS 기다리지 않음)
    options.page_load_strategy = 'eager'
    
    # 디스플레이 상태에 따라 headless 모드 결정
    # 로컬 환경(디스플레이 있음)에서는 GUI 모드로 실행
    if is_display_available():
        # GUI 모드
        print("[Browser] 🖥️ GUI 모드로 실행 (디스플레이 감지됨)")
        options.add_argument("--window-size=1920,1080")
    else:
        # Headless 모드 (GitHub Actions 또는 디스플레이 없음)
        print("[Browser] 🔧 Headless 모드로 실행")
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        # User-Agent 설정 (headless 감지 방지)
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        )
    
    # WebDriver 생성
    driver = webdriver.Chrome(options=options)
    
    # 타임아웃 설정
    driver.implicitly_wait(config.implicit_wait)
    driver.set_page_load_timeout(config.page_load_timeout)
    
    return driver
