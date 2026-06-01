# 노션 주식 모니터링

노션 표에 적어둔 **종목코드 / 수량 / 평단가**를 읽어서,
파이썬이 **현재가**를 가져와 **평가금액·수익률·손익**을 계산하고 노션 컬럼을 갱신한다.

- 국내주식(6자리 숫자코드 `005930`) → FinanceDataReader
- 해외주식(영문 티커 `AAPL`) → yfinance
- 종목코드 형태로 국내/해외 **자동 판별**

## 폴더 구조

```
notion/
├── .github/
│   └── workflows/
│       └── update.yml   # GitHub Actions 자동 실행 워크플로
├── update_stocks.py     # 메인 스크립트 (코드 직접 입력)
├── requirements.txt     # 의존성
├── .env.example         # 환경설정 예시 (복사해서 .env 로)
├── .gitignore
└── README.md
```

## 노션 DB ① 보유종목

| 컬럼 이름 | 타입 | 입력 주체 |
|---|---|---|
| `종목명` | 제목(Title) | 내가 입력 |
| `종목코드` | 텍스트(Text) | 내가 입력 (국내 `005930` / 해외 `AAPL`) |
| `수량` | 숫자(Number) | 내가 입력 |
| `평단가` | 숫자(Number) | 내가 입력 (해외는 USD) |
| `통화` | 선택(Select) | 파이썬 갱신 (KRW/USD 자동 판별) |
| `현재가` | 숫자(Number) | 파이썬 갱신 (원래 통화 기준) |
| `전일대비` | 숫자(Number) | 파이썬 갱신 (%) |
| `평가금액` | 숫자(Number) | 파이썬 갱신 (**원화 환산**) |
| `수익률` | 숫자(Number) | 파이썬 갱신 (%) |
| `손익` | 숫자(Number) | 파이썬 갱신 (**원화 환산**) |
| `상태` | 선택(Select) | 파이썬 갱신 (📈 수익 / 📉 손실) |
| `갱신시각` | 텍스트(Text) | 파이썬 갱신 |

## 노션 DB ② 자산기록 (총자산 추이 그래프용)

매 실행마다 그날의 총합을 한 행으로 누적(하루 1행, 같은 날이면 갱신).

| 컬럼 이름 | 타입 |
|---|---|
| `기록` | 제목(Title) |
| `일자` | 날짜(Date) |
| `총평가금액` | 숫자(Number) |
| `총매입금액` | 숫자(Number) |
| `총손익` | 숫자(Number) |
| `총수익률` | 숫자(Number) |

> **그래프:** 이 DB를 풀페이지로 열고 `/차트` 블록 추가 → X축 `일자`, Y축 `총평가금액`(합계), 선(Line) 차트.
> 매일 실행되며 점이 쌓여 자산 곡선이 그려진다.
> 해외주식은 `USD/KRW` 환율로 원화 환산해 합산하므로 국내+해외 총자산을 한 숫자로 본다.

## 설정

1. https://www.notion.so/my-integrations 에서 통합 생성 → 시크릿 복사
2. **보유종목 DB와 자산기록 DB 둘 다** `···` → 연결(Connections) → 만든 통합 추가
3. `.env.example` 을 `.env` 로 복사하고 토큰/DB ID 2개 입력

```bash
cp .env.example .env
```

## 로컬 설치 & 테스트

```bash
cd ~/Desktop/notion
python3 -m venv notion
source notion/bin/activate
pip install -r requirements.txt
python3 update_stocks.py
```

> ⚠️ Python 3.14에서 `yfinance`/`finance-datareader` 설치가 실패하면 3.12로 가상환경을 만드세요.
> 로컬은 `.env`, GitHub Actions는 Secrets에서 토큰을 읽는다 (코드는 동일 — `load_dotenv()`는 .env 없으면 그냥 넘어감).

## 자동화 — GitHub Actions

1. 이 폴더를 **GitHub private 저장소**에 푸시 (`.env`는 `.gitignore`로 제외되니 안전)
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 등록:
   - `NOTION_TOKEN`
   - `NOTION_DATABASE_ID`
   - `NOTION_HISTORY_DATABASE_ID`
3. `.github/workflows/update.yml` 이 자동 인식됨. **Actions 탭 → Run workflow** 로 수동 실행해 한번 테스트.

스케줄은 UTC 기준이다. 한국 장중(09:00~15:30 KST) = UTC 00:00~06:30 → `.github/workflows/update.yml` 의 cron 참고.

> GitHub Actions 스케줄은 5~15분 지연되거나 가끔 건너뛸 수 있어 "근사 주기"로 생각하면 된다.
