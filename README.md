# 🎾 Court Scheduler

## 🚀 설정

### 1. Repository Fork 또는 Clone

```bash
git clone https://github.com/jiyeoon/court_scheduler.git
cd court_scheduler
```

### 2. GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions에서 다음 시크릿을 추가

| Secret Name | Description | Required |
|-------------|-------------|----------|
| `LOGIN_ID` | 로그인 아이디 | ✅ |
| `LOGIN_PASSWORD` | 로그인 비밀번호 | ✅ |
| `LOGIN_URL` | 로그인 페이지 URL | ✅ |
| `BASE_URL` | 예약 기본 URL | ✅ |
| `SLACK_URL` | Slack Incoming Webhook URL | ❌ |

### 3. Slack Webhook 설정 (선택사항)

1. [Slack API](https://api.slack.com/apps)에서 새 앱 생성
2. Incoming Webhooks 활성화
3. Webhook URL을 GitHub Secrets에 추가

## 📁 프로젝트 구조

```
court_scheduler/
├── .github/
│   └── workflows/
│       └── reserve.yml      # GitHub Actions 워크플로우
├── src/
│   ├── __init__.py
│   ├── main.py              # 메인 실행 스크립트
│   ├── config.py            # 설정 관리
│   ├── browser.py           # Chrome WebDriver 설정
│   ├── reservation.py       # 예약 로직 (셀렉터, OCR 등)
│   └── notifier.py          # Slack 알림 & 로깅
├── requirements.txt
├── env.example
└── README.md
```

## 🖥️ 로컬 실행

### 환경 설정

```bash
# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# Tesseract OCR 설치 (macOS)
brew install tesseract

# 환경변수 설정
cp env.example .env
# .env 파일을 편집하여 인증 정보 입력
```

### 실행

```bash
python -m src.main
```

## 🚂 Railway 배포

이 저장소는 `Dockerfile` + `railway.json`으로 Railway 배포를 지원합니다.

- 기본 실행 명령: `python -m src.main_hybrid --preferred-hour 20 --weekend-hour 10`
- 필요한 환경변수: `LOGIN_ID`, `LOGIN_PASSWORD`, `LOGIN_URL`, `BASE_URL`, `SLACK_URL`

### Railway Cron 설정 (reserve.yml 기준)

`reserve.yml` 스케줄과 동일하게 Railway의 Cron Job을 2개 추가하세요.

- `50 23 * * *` (KST 08:20)
- `19 0 * * *` (KST 09:19)

## ⚠️ 주의사항

- **개인정보**: 로그인 정보는 반드시 GitHub Secrets를 통해 관리하세요

## 📜 License

MIT License

## 🔗 참고 링크

- [Selenium Documentation](https://selenium-python.readthedocs.io/)
- [ddddocr GitHub](https://github.com/sml2h3/ddddocr)
