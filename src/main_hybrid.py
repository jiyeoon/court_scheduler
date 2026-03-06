"""
Hybrid Tennis Court Reservation Bot - Main Entry Point.

Usage:
    python -m src.main_hybrid [--test] [--target-time HH:MM[:SS[.mmm]]]
                               [--preferred-hour HOUR] [--weekend-hour HOUR]
    
Options:
    --test: 테스트 모드 (9시 대기 없이 즉시 실행)
    --target-time: 새로고침 목표 시각 강제 지정 (예: 09:00:00.100)
    --preferred-hour: 선호 시작 시간 강제 지정 (0~23)
    --weekend-hour: 주말(토/일)일 때만 적용할 선호 시작 시간 (0~23)
"""
import sys
import argparse
from datetime import datetime, timedelta
from typing import Optional

from .config import get_config
from .browser import create_driver
from .notifier import Logger, SlackNotifier
from .hybrid_reservation import HybridReservationBot, KST


def parse_target_time_kst(time_str: str) -> Optional[datetime]:
    """
    HH:MM[:SS[.mmm]] 형식 시각 문자열을 다음 도래 KST datetime으로 변환합니다.
    """
    parsed = None
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(time_str, fmt)
            break
        except ValueError:
            continue

    if parsed is None:
        return None

    now = datetime.now(KST)
    target = now.replace(
        hour=parsed.hour,
        minute=parsed.minute,
        second=parsed.second,
        microsecond=parsed.microsecond
    )
    return target


def apply_preferred_hour(config, hour: int, logger: Logger, reason: str) -> None:
    """auto_find_latest=False 전략들의 target_hour와 name을 일괄 덮어씁니다."""
    for strategy in config.reservation.strategies:
        if not strategy.auto_find_latest:
            strategy.target_hour = hour
            end_hour = hour + strategy.time_slot_count
            # 전략 이름도 실제 시간대에 맞게 갱신
            court_label = strategy.name.split()[0]  # "실내" or "야외" 등
            strategy.name = f"{court_label} 코트 {hour}-{end_hour}시"
    logger.info(f"🕒 선호 시간대 적용({reason}): {hour}시 시작")


def main():
    parser = argparse.ArgumentParser(description='하이브리드 테니스 코트 예약 봇')
    parser.add_argument('--test', action='store_true', help='테스트 모드 (즉시 실행)')
    parser.add_argument(
        '--target-time',
        type=str,
        help='새로고침 목표 시각 강제 지정 (형식: HH:MM[:SS[.mmm]])'
    )
    parser.add_argument(
        '--preferred-hour',
        type=int,
        help='선호 시작 시간 강제 지정 (0~23)'
    )
    parser.add_argument(
        '--weekend-hour',
        type=int,
        help='주말(토/일)일 때만 적용할 선호 시작 시간 (0~23)'
    )
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
    
    # 선호 시간대 인자 검증
    for arg_name in ("preferred_hour", "weekend_hour"):
        arg_value = getattr(args, arg_name)
        if arg_value is not None and not (0 <= arg_value <= 23):
            logger.info(f"❌ --{arg_name.replace('_', '-')} 값 오류: {arg_value} (0~23만 허용)")
            return 1

    if args.test:
        logger.info("⚠️ 테스트 모드: 9시 대기 없이 즉시 실행")
        # 테스트 모드에서는 target_time을 현재 시간으로 설정
        config.reservation.reservation_open_hour = datetime.now(KST).hour
        config.reservation.reservation_open_minute = datetime.now(KST).minute

    # 선호 시간대 적용 우선순위:
    # 예약 대상일(오늘 기준 +6일)이 주말이면 --weekend-hour 우선, 그 외 --preferred-hour
    now_kst = datetime.now(KST)
    target_reservation_date = now_kst + timedelta(days=6)
    if args.weekend_hour is not None and target_reservation_date.weekday() >= 5:
        apply_preferred_hour(
            config,
            args.weekend_hour,
            logger,
            f"--weekend-hour (target_date={target_reservation_date.strftime('%Y-%m-%d')})"
        )
    elif args.preferred_hour is not None:
        apply_preferred_hour(
            config,
            args.preferred_hour,
            logger,
            f"--preferred-hour (target_date={target_reservation_date.strftime('%Y-%m-%d')})"
        )

    forced_target_time = None
    if args.target_time:
        forced_target_time = parse_target_time_kst(args.target_time)
        if not forced_target_time:
            logger.info(f"❌ 잘못된 --target-time 형식: {args.target_time}")
            logger.info("   예시: 09:00, 09:00:00, 09:00:00.100")
            return 1
        logger.info(f"🎯 강제 목표 시각: {forced_target_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} KST")
    
    # 브라우저 드라이버 생성
    driver = None
    try:
        driver = create_driver(config)  # Config 기반으로 GUI/Headless 자동 결정
        
        # 하이브리드 봇 실행
        bot = HybridReservationBot(driver, config, logger, notifier)
        if forced_target_time is not None:
            bot.forced_refresh_time = forced_target_time
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
