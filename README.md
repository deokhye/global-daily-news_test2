# Hankook Global Daily Brief — 자동화 파이프라인

매일 한국시간(KST) 06:00에 **USD/KRW 환율, 미국 주요 뉴스, 한국타이어 관련 산업/HR 동향**을
자동 수집하여 대시보드(`docs/index.html`)를 갱신하고 **GitHub Pages**로 서빙하는 파이프라인입니다.

---

## 1. 디렉토리 구조

```
hankook-daily-brief/
├── requirements.txt              # Python 의존성
├── README.md                     # 이 문서
├── .gitignore
├── src/
│   ├── collector.py               # ① 데이터 수집 (환율/뉴스/HR 동향/국가 지표)
│   └── build_site.py              # ② Jinja2 렌더링 → docs/index.html 생성
├── template/
│   └── template.html              # Jinja2 템플릿 (기존 대시보드 디자인 그대로)
├── data/
│   └── latest.json                # collector.py 결과 캐시 (자동 생성/갱신)
├── docs/
│   └── index.html                 # 최종 배포 산출물 (GitHub Pages가 이 폴더를 서빙)
└── .github/
    └── workflows/
        └── update.yml              # 매일 KST 06:00 자동 실행 스케줄러
```

**데이터 흐름**: `collector.py` → `data/latest.json` → `build_site.py` → `docs/index.html` → GitHub Pages 배포

---

## 2. 로컬에서 먼저 테스트하기

```bash
# 1) 가상환경 생성 (선택)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2) 의존성 설치
pip install -r requirements.txt

# 3) 데이터 수집
python src/collector.py

# 4) HTML 생성
python src/build_site.py

# 5) 결과 확인 (브라우저로 열기)
open docs/index.html           # macOS
# 또는 Windows: start docs\index.html
```

`data/latest.json`이 생성되고, `docs/index.html`이 실제 데이터로 채워진 최신 대시보드가 됩니다.
일부 API가 실패해도 `data/latest.json`의 이전 값을 캐시로 사용하므로 전체 실행이 중단되지 않습니다.

---

## 3. 사용 데이터 소스

| 항목 | 1순위 소스 | 대체(Fallback) 체인 |
|---|---|---|
| 총 인구 / 연간 GDP | World Bank Open API (키 불필요) | 이전 캐시값 → 기본값 |
| 인플레이션(CPI YoY) / 실업률 | 미국 노동통계국(BLS) API (키 없어도 동작, 있으면 호출 한도 상향) | FRED API (`FRED_API_KEY` 필요) → World Bank 연간 지표 → 이전 캐시값 |
| 연방/테네시 최저임금 | DOL 고시 기준 상수값 (`collector.py`의 `STATUTORY_MIN_WAGE`) | 법정 수치라 API 대신 상수로 관리 |
| USD/KRW 환율(실시간) + 최근 12개월 종가 | Yahoo Finance (`yfinance`, `KRW=X`, 키 불필요) | 이전 캐시값 → 기본값 |
| 현지 주요 뉴스 (TOP NEWS/BUSINESS/WORLD/SOCIETY) | Google News RSS, 당일 기준(`when:1d`) | 이전 캐시값 |
| 산업 동향 (AUTO MARKET/HR & LABOR/ECONOMY/MANAGEMENT) | Google News RSS 키워드 검색, 최근 7일(`when:7d`) | 이전 캐시값 |

> Google News RSS, World Bank API, BLS API는 **별도 키 없이 바로 동작**합니다.
> FRED API는 선택 사항이며, `FRED_API_KEY`를 설정하면 BLS가 실패했을 때 2차 백업으로 사용됩니다.
> 물가/고용 지표는 **BLS → FRED → World Bank → 캐시** 순으로 4단계 폴백을 거치므로
> 특정 기관 API가 일시 장애여도 대시보드가 비지 않습니다.

---

## 4. GitHub 저장소 준비 및 배포

### 4-1. 저장소 생성 & 코드 업로드
```bash
git init
git add .
git commit -m "init: hankook global daily brief pipeline"
git branch -M main
git remote add origin https://github.com/<YOUR_ID>/<YOUR_REPO>.git
git push -u origin main
```

### 4-2. GitHub Pages 활성화
1. 저장소 **Settings → Pages**
2. **Source**를 **GitHub Actions**로 선택 (docs 폴더를 별도로 지정할 필요 없음 — 워크플로우가 `upload-pages-artifact`로 `docs/`를 업로드합니다)

### 4-3. (선택) Secrets 등록 — 공식 통계 API 호출 한도를 늘리고 싶은 경우
1. BLS: https://www.bls.gov/developers/ 에서 무료 등록키 발급 (미등록 시에도 동작하나 일일 호출 한도가 낮음)
2. FRED: https://fred.stlouisfed.org/docs/api/api_key.html 에서 무료 API 키 발급 (BLS 장애 시 백업으로 사용)
3. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - `BLS_API_KEY` : 발급받은 BLS 등록키
   - `FRED_API_KEY` : 발급받은 FRED API 키

두 키 모두 등록하지 않아도 파이프라인은 정상 동작합니다(BLS 미등록 키 한도 → World Bank → 캐시 순으로 자동 폴백).

### 4-4. 워크플로우 동작 확인
- **Actions** 탭에서 `Daily Global Brief Update` 워크플로우 확인
- 최초 1회는 **Run workflow** 버튼으로 수동 실행 → `docs/index.html`이 생성되고 Pages에 배포됩니다
- 이후 매일 **KST 06:00 (UTC 21:00)** 자동 실행됩니다
- 배포된 주소는 `https://<YOUR_ID>.github.io/<YOUR_REPO>/` 형태입니다 (Actions 로그의 Pages 배포 단계에서 정확한 URL 확인 가능)

> ⚠️ GitHub Actions의 `schedule` cron은 정확히 정시에 실행되지 않을 수 있으며(수 분~십수 분 지연 가능),
> 저장소에 60일 이상 커밋이 없으면 스케줄이 자동 비활성화됩니다. 이 경우 Actions 탭에서 워크플로우를
> 다시 활성화(Enable workflow)하거나 수동 실행 후 재개하면 됩니다.

---

## 5. 커스터마이징 가이드

- **키워드/쿼리 변경**: `src/collector.py`의 `HEADLINE_SOURCES`(현지 뉴스 4개 카테고리), `INDUSTRY_SOURCES`(산업 동향 4개 분야) 리스트를 수정
- **카드 개수/레이아웃 변경**: `template/template.html`의 해당 섹션(`{% for %}` 루프) 수정
- **디자인/색상 변경**: `template.html`의 Tailwind 클래스만 수정하면 되며, 데이터 바인딩 변수명은 그대로 유지
- **실행 시각 변경**: `.github/workflows/update.yml`의 `cron: "0 21 * * *"` 값을 UTC 기준으로 수정
  (예: KST 09:00 실행 → UTC 00:00 → `"0 0 * * *"`)

---

## 6. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| Actions에서 `git push` 실패(권한 오류) | 저장소 Settings → Actions → General → Workflow permissions를 **Read and write permissions**로 변경 |
| 뉴스가 계속 캐시값만 나옴 | Google News RSS가 일시적으로 요청을 제한했을 수 있음 — 다음 실행에서 자동 복구, 재시도 간격을 늘리려면 `collector.py`에 `time.sleep()` 추가 |
| Pages가 404 | Settings → Pages에서 Source가 **GitHub Actions**로 되어 있는지, 워크플로우의 `deploy-pages` 단계가 성공했는지 확인 |
| 환율이 이상하게 나옴 | `KRW=X` 티커는 Yahoo Finance 정책에 따라 간헐적으로 응답이 지연될 수 있음 — 재실행 시 자동 복구, 지속되면 캐시값으로 표시됨 |
| 물가/고용 지표가 갱신되지 않음 | BLS API 일일 호출 한도(미등록 25회/등록 500회) 초과 가능성 — `BLS_API_KEY` 등록 또는 `FRED_API_KEY` 백업 등록 권장 |

---

## 7. 요약 실행 순서 (Cheat Sheet)

```bash
pip install -r requirements.txt
python src/collector.py
python src/build_site.py
```
→ GitHub에 push → Actions 자동 스케줄 등록 → 매일 KST 06:00 `docs/index.html` 자동 갱신 & Pages 배포
