"""
Slack notification and logging module for Court Scheduler.
"""
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import requests

from .config import Config


@dataclass
class ReservationResult:
    """예약 결과 정보를 담는 데이터 클래스"""
    success: bool = False
    date: str = ""  # 예약 날짜 (예: "2025-01-06")
    time_slot: str = ""  # 시간대 (예: "19:00-21:00")
    court_number: int = 0  # 코트 번호
    court_type: str = ""  # 코트 타입 (실내/야외)
    strategy_name: str = ""  # 성공한 전략 이름
    tried_strategies: List[str] = field(default_factory=list)  # 시도한 전략들
    error_message: str = ""  # 에러 메시지
    
    def get_court_type_emoji(self) -> str:
        """코트 타입에 따른 이모지 반환"""
        if "실내" in self.court_type:
            return "🏠"
        return "🌳"
    
    def format_success_message(self) -> str:
        """성공 메시지 포맷팅"""
        emoji = self.get_court_type_emoji()
        return (
            f"📅 *날짜:* {self.date}\n"
            f"⏰ *시간:* {self.time_slot}\n"
            f"{emoji} *코트:* {self.court_number}번 ({self.court_type})\n"
            f"🎯 *전략:* {self.strategy_name}"
        )
    
    def format_failure_message(self) -> str:
        """실패 메시지 포맷팅"""
        tried = " → ".join(self.tried_strategies) if self.tried_strategies else "없음"
        return (
            f"📅 *날짜:* {self.date or '선택 전 실패'}\n"
            f"🔄 *시도한 전략:* {tried}\n"
            f"❌ *실패 원인:* {self.error_message}"
        )


# 한국 시간대
KST = timezone(timedelta(hours=9))


class Logger:
    """Logger with buffer for Slack notifications."""
    
    def __init__(self):
        self.buffer: list[str] = []
    
    def info(self, msg: str) -> None:
        """Log info message with timestamp."""
        timestamp = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        log_str = f"\t[INFO]>> [{timestamp}] : {msg}\n"
        sys.stdout.write(log_str)
        sys.stdout.flush()
        self.buffer.append(log_str)
    
    def get_buffer(self) -> str:
        """Get all buffered logs as string."""
        return ''.join(self.buffer)
    
    def clear_buffer(self) -> None:
        """Clear the log buffer."""
        self.buffer = []


class SlackNotifier:
    """Slack webhook notifier with log buffer support."""
    
    def __init__(self, config: Config, logger: Logger):
        self.webhook_url = config.slack_url
        self.base_url = config.base_url
        self.enabled = bool(self.webhook_url)
        self.logger = logger
    
    def _send_message(self, data: dict) -> bool:
        """
        Send message to Slack webhook.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            self.logger.info("Slack webhook not configured, skipping notification")
            return False
        
        try:
            response = requests.post(
                self.webhook_url,
                json=data,
                timeout=10,
            )
            if response.status_code == 200:
                self.logger.info("Slack 메시지 전송 성공")
                return True
            else:
                self.logger.info(f"Slack 메시지 전송 실패: {response.status_code}, {response.text}")
                return False
        except requests.RequestException as e:
            self.logger.info(f"Slack 메시지 전송 실패: {e}")
            return False
    
    def send_success(self, message: str, result: Optional[ReservationResult] = None) -> bool:
        """Send success notification with reservation details."""
        buffer_str = self.logger.get_buffer()
        
        # 상세 정보가 있으면 포맷팅된 메시지 사용
        if result:
            detail_text = result.format_success_message()
        else:
            detail_text = f"```{message}```"
        
        data = {
            "attachments": [
                {
                    "title": "🎉 테니스 코트 예약 성공!",
                    "title_link": self.base_url,
                    "text": (
                        f"{detail_text}\n\n"
                        f"<{self.base_url}|🔗 예약 확인하기>"
                    ),
                    "color": "#2EB67D",
                    "footer": "Court Scheduler",
                    "ts": int(datetime.now(KST).timestamp())
                }
            ]
        }
        
        # 로그는 별도 첨부파일로 (너무 길면 생략)
        if len(buffer_str) < 3000:
            data["attachments"].append({
                "title": "📋 실행 로그",
                "text": f"```{buffer_str}```",
                "color": "#36a64f"
            })
        
        return self._send_message(data)
    
    def send_failure(self, message: str, result: Optional[ReservationResult] = None) -> bool:
        """Send failure notification with details."""
        buffer_str = self.logger.get_buffer()
        
        # 상세 정보가 있으면 포맷팅된 메시지 사용
        if result:
            detail_text = result.format_failure_message()
        else:
            detail_text = f"❌ *실패 원인:* {message}"
        
        data = {
            "attachments": [
                {
                    "title": "❌ 테니스 코트 예약 실패",
                    "title_link": "https://github.com/actions",
                    "text": detail_text,
                    "color": "#E01E5A",
                    "footer": "Court Scheduler",
                    "ts": int(datetime.now(KST).timestamp())
                }
            ]
        }
        
        # 로그 첨부 (길이 제한)
        log_text = buffer_str[-2500:] if len(buffer_str) > 2500 else buffer_str
        if log_text:
            data["attachments"].append({
                "title": "📋 실행 로그 (최근)",
                "text": f"```{log_text}```",
                "color": "#E01E5A"
            })
        
        return self._send_message(data)
