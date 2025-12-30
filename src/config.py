"""
Configuration management for Court Scheduler.
"""
import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()


# 코트 분류
INDOOR_COURTS = [5, 6, 7, 8]  # 실내 코트 (21시까지만 운영)
OUTDOOR_COURTS = [19, 18, 2, 13, 17, 16, 15, 14, 12, 11, 10, 9, 4, 3]  # 야외 코트
ALL_COURTS = INDOOR_COURTS + OUTDOOR_COURTS  # 모든 코트


@dataclass
class ReservationStrategy:
    """예약 전략 정의"""
    name: str
    target_hour: int  # 시작 시간 (예: 19 = 19시), -1이면 자동 탐색 (가장 늦은 시간)
    time_slot_count: int  # 선택할 시간 슬롯 개수
    preferred_courts: List[int]  # 시도할 코트 목록
    auto_find_latest: bool = False  # True면 가능한 가장 늦은 연속 시간대 자동 탐색


# 예약 전략 목록 (우선순위 순)
RESERVATION_STRATEGIES = [
    # 1순위: 실내 코트 + 19시-21시
    ReservationStrategy(
        name="실내 코트 19-21시",
        target_hour=19,
        time_slot_count=2,
        preferred_courts=INDOOR_COURTS,
    ),
    # 2순위: 야외 코트 + 20시-22시
    ReservationStrategy(
        name="야외 코트 20-22시",
        target_hour=20,
        time_slot_count=2,
        preferred_courts=OUTDOOR_COURTS,
    ),
    # 3순위: 아무 코트나 + 가능한 늦은 연속 2시간
    ReservationStrategy(
        name="아무 코트 가능한 늦은 시간",
        target_hour=-1,  # -1 = 자동 탐색
        time_slot_count=2,
        preferred_courts=ALL_COURTS,
        auto_find_latest=True,
    ),
]


@dataclass
class ReservationConfig:
    """Reservation preferences configuration."""
    
    # 예약 전략 목록
    strategies: List[ReservationStrategy] = field(
        default_factory=lambda: RESERVATION_STRATEGIES
    )
    
    # 예약 오픈 시간 (KST)
    reservation_open_hour: int = 9
    reservation_open_minute: int = 0


@dataclass
class Config:
    """Main configuration class."""
    
    # 인증 정보 (환경변수에서 로드)
    login_id: str = ""
    login_password: str = ""
    login_url: str = ""
    base_url: str = ""
    
    # Slack 알림
    slack_url: str = ""
    
    # 예약 설정
    reservation: ReservationConfig = field(default_factory=ReservationConfig)
    
    # 브라우저 설정
    headless: bool = True
    implicit_wait: int = 10
    page_load_timeout: int = 60
    
    def __post_init__(self):
        """Load credentials from environment variables."""
        self.login_id = os.getenv("LOGIN_ID", "")
        self.login_password = os.getenv("LOGIN_PASSWORD", "")
        self.login_url = os.getenv("LOGIN_URL", "")
        self.base_url = os.getenv("BASE_URL", "")
        self.slack_url = os.getenv("SLACK_URL", "")
        
        # GitHub Actions에서는 headless 강제
        if os.getenv("GITHUB_ACTIONS"):
            self.headless = True
    
    def validate(self) -> bool:
        """Validate required configuration."""
        if not self.login_id:
            raise ValueError("LOGIN_ID environment variable is required")
        if not self.login_password:
            raise ValueError("LOGIN_PASSWORD environment variable is required")
        if not self.login_url:
            raise ValueError("LOGIN_URL environment variable is required")
        if not self.base_url:
            raise ValueError("BASE_URL environment variable is required")
        return True


def get_config() -> Config:
    """Get configuration instance."""
    config = Config()
    config.validate()
    return config
