#!/usr/bin/env python3
"""
Main entry point for Olympic Tennis Court Reservation Bot.

Usage:
    python -m src.main
    
Environment Variables:
    LOGIN_ID: KSPO login user ID
    LOGIN_PASSWORD: KSPO login password
    LOGIN_URL: KSPO login page URL
    BASE_URL: KSPO base URL
    SLACK_URL: (Optional) Slack webhook URL for notifications
"""
import sys

from .config import get_config
from .browser import create_driver
from .notifier import Logger, SlackNotifier
from .reservation import ReservationBot


def main() -> int:
    """
    Main function to run the reservation bot.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    logger = Logger()
    driver = None
    
    try:
        logger.info("🚀 테니스 예약 봇 시작")
        
        # 1. Load configuration
        logger.info("설정 로드 중...")
        config = get_config()
        logger.info("✅ 설정 로드 완료")
        
        # 2. Initialize Slack notifier
        notifier = SlackNotifier(config, logger)
        if notifier.enabled:
            logger.info("Slack 알림 활성화됨")
        else:
            logger.info("Slack 알림 비활성화됨 (webhook URL 없음)")
        
        # 3. Initialize browser
        logger.info("Chrome Driver 설정 시작")
        driver = create_driver(config)
        logger.info("✅ Chrome Driver 설정 완료")
        
        # 4. Run reservation bot
        bot = ReservationBot(driver, config, logger, notifier)
        exit_code = bot.run()
        
        if exit_code != 0:
            logger.info("❌ 예약 실패")
        else:
            logger.info("✅ 예약 성공")
        
        return exit_code
        
    except ValueError as e:
        logger.info(f"❌ 설정 오류: {e}")
        return 1
        
    except Exception as e:
        logger.info(f"💥 예외 발생: {e}")
        
        # Send error notification
        try:
            from .config import Config
            config = Config()
            notifier = SlackNotifier(config, logger)
            notifier.send_failure(f"예약 봇 실행 중 예외 발생: {e}")
        except Exception:
            pass
        
        return 1
        
    finally:
        # Close browser
        if driver:
            try:
                driver.quit()
                logger.info("🔚 브라우저 종료")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
