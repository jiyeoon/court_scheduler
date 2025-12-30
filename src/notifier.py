"""
Slack notification and logging module for Court Scheduler.
"""
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from .config import Config


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
    
    def send_success(self, message: str) -> bool:
        """Send success notification with log buffer."""
        buffer_str = self.logger.get_buffer()
        
        data = {
            "attachments": [
                {
                    "title": "🎉 Reservation Success",
                    "title_link": "https://github.com/actions",
                    "text": f"예약에 성공했습니다.\n```{message}```\n<{self.base_url}|예약 확인하기>\n*Log 출력*\n```{buffer_str}```",
                    "color": "#2EB67D"
                }
            ]
        }
        
        return self._send_message(data)
    
    def send_failure(self, message: str) -> bool:
        """Send failure notification with log buffer."""
        buffer_str = self.logger.get_buffer()
        
        data = {
            "attachments": [
                {
                    "title": "❌ Reservation Failed",
                    "title_link": "https://github.com/actions",
                    "text": f"예약에 실패했습니다.\n```{message}```\n*Log 출력*\n```{buffer_str}```",
                    "color": "#E01E5A"
                }
            ]
        }
        
        return self._send_message(data)
