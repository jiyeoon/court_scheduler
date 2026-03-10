#!/usr/bin/env python3
"""
실내 테니스장 빈코트 모니터 — 2시간 연속 빈 슬롯 Slack 알림

조건:
  • 평일: 19시 이후 2시간 연속 예약 가능
  • 주말: 아무 시간대 2시간 연속 예약 가능

동작:
  • 빈 슬롯 발견 시 → Slack 알림
  • 빈 슬롯 없으면 → 조용히 정상 종료
  • --loop 모드에서는 이전에 알린 슬롯은 중복 알림 안 함

사용법:
  python -m src.monitor_availability               # 1회 실행
  python -m src.monitor_availability --loop         # 10분 간격 반복
  python -m src.monitor_availability --interval 5   # 5분 간격 반복
"""
import os
import sys
import time
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import requests
from dotenv import load_dotenv

# ── 상수 ──────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

SITE_URL = "https://wise.uos.ac.kr/wellness"
LOGIN_URL = "https://wise.uos.ac.kr/FownExtrlLogin/login.do"
LIST_URL = "https://wise.uos.ac.kr/AFF/FownRsvtAply/list.do"
RSV_TIME_URL = "https://wise.uos.ac.kr/AFF/FownRsvtAply/rsvTime.do"

# 실내테니스장 시설 코드
FCLT_DIV_CD = "FOWN001.05"

# 코트 매핑 (API 필드 접두어 → 코트명)
COURT_PREFIX = ["A", "B", "C"]

# 공통 POST 파라미터 (Cleopatra 프레임워크)
COMMON_PARAMS = {
    "_AUTH_MENU_KEY": "FownRsvtAply_7",
    "_AUTH_PGM_ID": "FownRsvtAply",
    "__PRVC_PSBLTY_YN": "N",
    "_AUTH_TASK_AUTHRT_ID": "CCMN_SVC",
    "default.locale": "CCMN101.KOR",
}

DEFAULT_INTERVAL_MIN = 5


# ── 유틸 ──────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def is_weekend(date_str: str) -> bool:
    """YYYYMMDD 형식 날짜가 주말인지 확인."""
    dt = datetime.strptime(date_str, "%Y%m%d")
    return dt.weekday() >= 5  # 5=토, 6=일


def format_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD (요일)."""
    dt = datetime.strptime(date_str, "%Y%m%d")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{dt.strftime('%Y-%m-%d')} ({weekdays[dt.weekday()]})"


def slot_start_hour(cd: str) -> int:
    """CD 코드(예: '19002000')에서 시작 시각(19) 추출."""
    return int(cd[:2])


def slot_key(s: Dict) -> str:
    """슬롯을 고유 식별하는 키 (중복 알림 방지용)."""
    return f"{s['date']}_{s['court']}_{s['start']}_{s['end']}"


# ── 순수 HTTP 로그인 ─────────────────────────────────────────────────
def http_login(login_id: str, login_pw: str) -> Optional[requests.Session]:
    """
    순수 requests 로 로그인하여 세션을 반환.
    Cleopatra 프레임워크 형식으로 POST 전송.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": SITE_URL,
            "Origin": "https://wise.uos.ac.kr",
        }
    )

    try:
        # 1) 초기 쿠키 획득 (WMONID)
        session.get(SITE_URL, timeout=10)

        # 2) Cleopatra 프레임워크 형식 로그인 요청
        login_data = {
            "default.locale": "CCMN101.KOR",
            "@d1#default.locale": "CCMN101.KOR",
            "@d1#strExamNo": "",
            "@d1#strExtrId": login_id,
            "@d1#strSprtBrdt": login_pw,
            "@d#": "@d1#",
            "@d1#": "dmParam",
            "@d1#tp": "dm",
        }

        resp = session.post(LOGIN_URL, data=login_data, timeout=10)
        resp.raise_for_status()

        # 3) 응답 확인
        result = resp.json()
        if "ERRMSGINFO" in result:
            err_msg = result["ERRMSGINFO"].get("ERRMSG", "알 수 없는 오류")
            log(f"❌ 로그인 실패: {err_msg}")
            return None

        # UOSSESSION 쿠키 확인
        if not session.cookies.get("UOSSESSION"):
            log("❌ 로그인 실패: UOSSESSION 쿠키 없음")
            return None

        return session

    except Exception as e:
        log(f"❌ 로그인 오류: {e}")
        return None


# ── API 호출 ─────────────────────────────────────────────────────────
def get_end_date(session: requests.Session, base_date: str) -> Optional[str]:
    """list.do 를 호출하여 마지막 예약 가능 날짜(END_YMD)를 가져온다."""
    params = {
        **COMMON_PARAMS,
        "@d1#strFcltDivCd": FCLT_DIV_CD,
        "@d1#strRsvtYmd": base_date,
        "@d#": "@d1#",
        "@d1#": "dmReqKey",
        "@d1#tp": "dm",
    }
    try:
        resp = session.post(LIST_URL, data=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("dmResSearch", {}).get("END_YMD")
    except Exception as e:
        log(f"❌ list.do 호출 실패: {e}")
        return None


def get_time_slots(session: requests.Session, date_str: str) -> List[Dict]:
    """rsvTime.do 를 호출하여 해당 날짜의 시간별 예약 상태를 반환."""
    params = {
        **COMMON_PARAMS,
        "@d1#strFcltDivCd": FCLT_DIV_CD,
        "@d1#strToday": date_str,
        "@d#": "@d1#",
        "@d1#": "dmReqKeyRsv",
        "@d1#tp": "dm",
    }
    try:
        resp = session.post(RSV_TIME_URL, data=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("dsRevTime", [])
    except Exception as e:
        log(f"  ❌ rsvTime.do 호출 실패 ({date_str}): {e}")
        return []


# ── 가용성 분석 ──────────────────────────────────────────────────────
def find_consecutive_slots(
    time_slots: List[Dict],
    date_str: str,
    min_hours: int = 2,
) -> List[Dict]:
    """
    2시간 연속 예약 가능한 슬롯 조합을 찾는다.

    Returns:
        [{'date': ..., 'court': ..., 'start': 19, 'end': 21, ...}, ...]
    """
    weekend = is_weekend(date_str)
    results = []

    for court in COURT_PREFIX:
        sts_yn_key = f"{court}_STS_YN"

        # 시간 순 정렬 (CD 기준)
        sorted_slots = sorted(time_slots, key=lambda s: s["CD"])

        # 해당 코트의 예약 가능 슬롯만 추출
        available = []
        for slot in sorted_slots:
            is_avail = slot.get(sts_yn_key) != "불" and slot.get(sts_yn_key) is not None
            sts_key = f"{court}_STS"
            if not is_avail:
                sts_val = slot.get(sts_key)
                if sts_val is None or sts_val == "":
                    is_avail = True
                else:
                    is_avail = False

            hour = slot_start_hour(slot["CD"])

            # 평일: 19시 이후만
            if not weekend and hour < 19:
                continue

            if is_avail:
                available.append(slot)

        # 연속 슬롯 찾기
        for i in range(len(available)):
            start_hour = slot_start_hour(available[i]["CD"])
            consecutive = [available[i]]

            for j in range(i + 1, len(available)):
                next_hour = slot_start_hour(available[j]["CD"])
                prev_end = slot_start_hour(consecutive[-1]["CD"]) + 1
                if next_hour == prev_end:
                    consecutive.append(available[j])
                else:
                    break

                if len(consecutive) >= min_hours:
                    end_hour = slot_start_hour(consecutive[-1]["CD"]) + 1
                    results.append(
                        {
                            "date": date_str,
                            "date_fmt": format_date(date_str),
                            "court": f"{court}코트",
                            "start": start_hour,
                            "end": end_hour,
                            "is_weekend": weekend,
                        }
                    )
                    break  # 이 시작점에서 가장 짧은 연속 슬롯만 기록

    return results


# ── Slack 알림 ────────────────────────────────────────────────────────
def send_slack_notification(webhook_url: str, slots: List[Dict]) -> bool:
    """가용 슬롯 정보를 Slack 으로 전송."""
    if not webhook_url:
        log("⚠️ SLACK_URL 이 설정되지 않아 알림을 보내지 않습니다.")
        return False

    lines = []
    for s in slots:
        day_type = "🗓️ 주말" if s["is_weekend"] else "🌙 평일 저녁"
        lines.append(
            f"• {s['date_fmt']}  |  {s['court']}  |  "
            f"{s['start']:02d}:00 ~ {s['end']:02d}:00  ({day_type})"
        )

    text = "\n".join(lines)

    data = {
        "attachments": [
            {
                "title": "🎾 실내 테니스장 빈코트 알림!",
                "title_link": SITE_URL,
                "text": (
                    f"2시간 연속 예약 가능한 슬롯이 발견되었습니다:\n\n"
                    f"```\n{text}\n```\n\n"
                    f"<{SITE_URL}|🔗 예약하러 가기>"
                ),
                "color": "#2EB67D",
                "footer": "Tennis Court Monitor",
                "ts": int(datetime.now(KST).timestamp()),
            }
        ]
    }

    try:
        resp = requests.post(webhook_url, json=data, timeout=10)
        if resp.status_code == 200:
            log("✅ Slack 알림 전송 완료")
            return True
        else:
            log(f"❌ Slack 알림 전송 실패: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Slack 알림 전송 오류: {e}")
        return False


# ── 메인 로직 ────────────────────────────────────────────────────────
def check_all_dates(session: requests.Session) -> List[Dict]:
    """모든 예약 가능 날짜를 조회하고 2시간 연속 빈 슬롯을 찾는다."""
    today = datetime.now(KST)
    today_str = today.strftime("%Y%m%d")

    # 마지막 예약 가능 날짜 조회
    end_date = get_end_date(session, today_str)
    if not end_date:
        log("❌ 예약 가능 날짜를 가져올 수 없습니다.")
        return []

    # 날짜 범위 생성 (내일부터 end_date 까지)
    start = (today + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    dates = []
    current = start
    while current <= end_dt:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    if not dates:
        return []

    all_found = []
    for date_str in dates:
        time_slots = get_time_slots(session, date_str)
        if not time_slots:
            continue

        found = find_consecutive_slots(time_slots, date_str)
        if found:
            all_found.extend(found)

        # API 부하 방지
        time.sleep(0.3)

    return all_found


def run_once(
    login_id: str,
    login_pw: str,
    slack_url: str,
    notified: Set[str],
) -> Set[str]:
    """
    1회 실행: 로그인 → 조회 → (새 슬롯만) 알림.
    반환: 이번 실행에서 발견된 슬롯 키 set (다음 실행의 중복 방지용).
    """
    session = http_login(login_id, login_pw)
    if not session:
        return notified  # 로그인 실패 시 기존 set 유지

    found_slots = check_all_dates(session)
    current_keys = {slot_key(s) for s in found_slots}

    # 새로 발견된 슬롯만 필터
    new_slots = [s for s in found_slots if slot_key(s) not in notified]

    if new_slots:
        for s in new_slots:
            day_type = "주말" if s["is_weekend"] else "평일저녁"
            log(
                f"  🟢 {s['date_fmt']}  {s['court']}  "
                f"{s['start']:02d}~{s['end']:02d}시  ({day_type})"
            )
        log(f"🎉 새 빈 슬롯 {len(new_slots)}건 발견 → Slack 알림!")
        send_slack_notification(slack_url, new_slots)

    return current_keys  # 현재 발견된 슬롯으로 교체 (사라진 슬롯은 다시 알림 가능)


def main():
    parser = argparse.ArgumentParser(
        description="실내 테니스장 빈코트 모니터 (2시간 연속 슬롯 Slack 알림)"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help=f"반복 실행 모드 (기본 {DEFAULT_INTERVAL_MIN}분 간격)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MIN,
        help=f"반복 간격 (분, 기본 {DEFAULT_INTERVAL_MIN})",
    )
    args = parser.parse_args()

    load_dotenv()
    login_id = os.getenv("LOGIN_ID", "")
    login_pw = os.getenv("LOGIN_PASSWORD", "")
    slack_url = os.getenv("SLACK_URL", "")

    if not login_id or not login_pw:
        log("❌ LOGIN_ID / LOGIN_PASSWORD 환경변수가 필요합니다.")
        sys.exit(1)

    if args.loop:
        log(f"🔁 반복 모드 시작 (간격: {args.interval}분)")
        notified: Set[str] = set()
        while True:
            try:
                notified = run_once(login_id, login_pw, slack_url, notified)
                log(f"💤 [{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] 완료 — {args.interval}분 후 다시 실행")
            except Exception as e:
                log(f"❌ 실행 중 오류: {e}")
            time.sleep(args.interval * 60)
    else:
        # 1회 실행: 발견 시 알림, 없으면 조용히 종료
        run_once(login_id, login_pw, slack_url, set())


if __name__ == "__main__":
    main()
