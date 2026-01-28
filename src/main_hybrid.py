"""
Hybrid Tennis Court Reservation Bot - Main Entry Point.

Usage:
    python -m src.main_hybrid [--test]
    
Options:
    --test: 테스트 모드 (9시 대기 없이 즉시 실행)
"""
import sys
import argparse
from datetime import datetime, timedelta

from .config import get_config
from .browser import create_driver
from .notifier import Logger, SlackNotifier
from .hybrid_reservation import HybridReservationBot, KST


def main():
    parser = argparse.ArgumentParser(description='하이브리드 테니스 코트 예약 봇')
    parser.add_argument('--test', action='store_true', help='테스트 모드 (즉시 실행)')
    args = parser.parse_args()
    
    # 설정 로드
    config = get_config()
    
    # 로거 및 알림 설정
    logger = Logger()
    notifier = SlackNotifier(config, logger)
    
    logger.info("=" * 60)
    logger.info("🚀 하이브리드 테니스 코트 예약 봇")
    logger.info("   Selenium(로그인) + HTTP Requests(예약)")
    logger.info("=" * 60)
    
    if args.test:
        logger.info("⚠️ 테스트 모드: 9시 대기 없이 즉시 실행")
        # 테스트 모드에서는 target_time을 현재 시간으로 설정
        config.reservation.reservation_open_hour = datetime.now(KST).hour
        config.reservation.reservation_open_minute = datetime.now(KST).minute
    
    # 브라우저 드라이버 생성
    driver = None
    try:
        driver = create_driver(config)  # Config 기반으로 GUI/Headless 자동 결정
        
        # 하이브리드 봇 실행
        bot = HybridReservationBot(driver, config, logger, notifier)
        exit_code = bot.run()
        
        return exit_code
        
    except Exception as e:
        logger.info(f"💥 치명적 오류: {e}")
        import traceback
        traceback.print_exc()
        return 1
        
    finally:
        if driver:
            logger.info("🔒 브라우저 종료")
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
