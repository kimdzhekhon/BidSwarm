# BidSwarm

조달청 공공입찰 낙찰가 예측 엔진 — 군집 에이전트 시뮬레이션으로 경쟁 입찰자를 모델링하고 최적 입찰가를 산출합니다.

> [MiroFish](https://github.com/666ghj/MiroFish) 백엔드 엔진 기반 포크. 프론트엔드·소셜미디어 시뮬레이션 제거, 공공입찰 전용 플랫폼으로 교체.

---

## 작동 원리

```
[1] 공고 입력
    조달청 공고문(PDF/텍스트)을 업로드하면
    LLM이 예정 가격·카테고리·핵심 조건을 자동 추출합니다.

[2] 경쟁사 에이전트 생성
    Zep 지식 그래프에서 추출한 기업 엔티티를 바탕으로
    각 업체의 입찰 전략·가격 범위·활동 패턴을 가진 AI 에이전트를 생성합니다.

[3] 입찰 시뮬레이션
    에이전트들이 라운드마다 공고를 분석하고,
    경쟁사 동향을 관찰하며, LLM으로 입찰 전략을 결정합니다.
    가능한 액션: SUBMIT_BID / REVISE_BID / WITHDRAW_BID /
                OBSERVE_COMPETITORS / ANALYZE_NOTICE / DO_NOTHING

[4] 낙찰 예측 리포트
    시뮬레이션 결과를 분석해 예상 낙찰가 범위,
    최적 입찰가(예정가 대비 %), 낙찰 확률을 출력합니다.
```

---

## 요구사항

- Python 3.11+
- LLM API 키 (OpenAI 또는 호환 API)
- [Zep Cloud](https://app.getzep.com/) API 키 (무료 플랜 사용 가능)

---

## 설치

```bash
# 1. 레포 클론
git clone https://github.com/kimdzhekhon/BidSwarm.git
cd BidSwarm

# 2. 환경 변수 설정
cp .env.example .env
```

`.env` 파일을 열고 아래 값을 채웁니다:

```env
LLM_API_KEY=sk-...          # OpenAI 또는 호환 API 키
LLM_BASE_URL=https://api.openai.com/v1   # 기본값, 다른 프로바이더면 변경
LLM_MODEL_NAME=gpt-4o-mini  # 사용할 모델명

ZEP_API_KEY=z_...           # Zep Cloud API 키
```

```bash
# 3. 패키지 설치
cd backend
pip install uv
uv sync

# 4. 서버 실행
python run.py
```

서버가 뜨면 `http://localhost:5001/health` 에서 `{"status":"ok"}` 확인.

---

## 사용 방법

### A. Flask API 서버 경유 (권장)

#### 1단계 — 프로젝트 생성

```bash
curl -X POST http://localhost:5001/api/graph/ontology/generate \
  -F "files=@공고문.pdf" \
  -F "simulation_requirement=조달청 IT서비스 입찰 경쟁사 분석"
```

#### 2단계 — 지식 그래프 구축

```bash
curl -X POST http://localhost:5001/api/graph/build \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<project_id>"}'
```

#### 3단계 — 시뮬레이션 생성 및 준비

```bash
# 시뮬레이션 생성
curl -X POST http://localhost:5001/api/simulation/create \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<project_id>", "graph_id": "<graph_id>"}'

# 에이전트 준비 (입찰 업체 프로필 자동 생성)
curl -X POST http://localhost:5001/api/simulation/prepare \
  -H "Content-Type: application/json" \
  -d '{"simulation_id": "<simulation_id>"}'
```

#### 4단계 — 시뮬레이션 실행

```bash
curl -X POST http://localhost:5001/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "simulation_id": "<simulation_id>",
    "platform": "bid",
    "max_rounds": 20
  }'
```

#### 5단계 — 진행 상태 확인

```bash
# 실시간 진행률
curl http://localhost:5001/api/simulation/<simulation_id>/run-status

# 응답 예시
{
  "status": "running",
  "round_num": 12,
  "total_rounds": 20,
  "progress_pct": 60.0,
  "active_bid_count": 4
}
```

#### 6단계 — 낙찰 예측 리포트

```bash
curl -X POST http://localhost:5001/api/report \
  -H "Content-Type: application/json" \
  -d '{"simulation_id": "<simulation_id>"}'
```

리포트 응답 예시:

```json
{
  "winner": { "agent_name": "스마트IT솔루션", "price": 87500000 },
  "stats": {
    "min_price": 82000000,
    "max_price": 96000000,
    "avg_price": 89200000,
    "bid_count": 5,
    "budget": 100000000,
    "winning_ratio": 87.5
  },
  "optimal_bids": [
    {
      "company_name": "내 회사",
      "recommended_ratio": 86,
      "win_probability": 68,
      "strategy_advice": "예정가 85~88% 범위가 낙찰 확률 최대 구간입니다."
    }
  ]
}
```

---

### B. 스크립트 직접 실행 (빠른 테스트)

API 서버 없이 시뮬레이션만 바로 돌릴 수 있습니다.

#### simulation_config.json 예시

```json
{
  "simulation_id": "test_001",
  "notice": {
    "notice_id": 1,
    "title": "2025년 행정안전부 클라우드 전환 용역",
    "budget": 500000000,
    "category": "IT서비스",
    "description": "정부 레거시 시스템의 클라우드 전환 및 운영 관리"
  },
  "agent_configs": [
    {
      "agent_id": 0,
      "company_name": "한국IT솔루션",
      "industry": "IT서비스",
      "company_size": "중소기업",
      "win_rate": 30,
      "strategy": "공격적 저가 전략",
      "strengths": "가격 경쟁력",
      "budget_capacity": "예정가 78% 이상 수익 가능",
      "min_ratio": 75,
      "max_ratio": 88,
      "active_hours": [9,10,11,13,14,15,16],
      "activity_level": 0.9
    },
    {
      "agent_id": 1,
      "company_name": "대한클라우드",
      "industry": "IT서비스",
      "company_size": "중견기업",
      "win_rate": 20,
      "strategy": "기술력 기반 적정가 입찰",
      "strengths": "클라우드 전문성, 레퍼런스",
      "budget_capacity": "예정가 85% 이상 수익 가능",
      "min_ratio": 83,
      "max_ratio": 95,
      "active_hours": [9,10,11,14,15,16,17],
      "activity_level": 0.7
    }
  ],
  "simulation": {
    "total_rounds": 15
  }
}
```

#### 실행

```bash
cd backend/scripts
python run_bid_simulation.py --config /path/to/simulation_config.json

# 라운드 수 제한
python run_bid_simulation.py --config simulation_config.json --max-rounds 10
```

#### 출력 파일

시뮬레이션이 완료되면 config 파일과 같은 디렉터리에 생성됩니다:

```
simulation_dir/
├── bid_simulation.db   # SQLite — 모든 입찰 기록
├── actions.jsonl       # 라운드별 에이전트 행동 로그
└── run_state.json      # 최종 결과 요약 (낙찰자, 통계)
```

`run_state.json` 결과 예시:

```json
{
  "status": "completed",
  "summary": {
    "winner": { "agent_name": "한국IT솔루션", "price": 412000000 },
    "stats": {
      "min_price": 412000000,
      "max_price": 468000000,
      "avg_price": 438500000,
      "bid_count": 2,
      "winning_ratio": 82.4
    }
  }
}
```

---

## 프로젝트 구조

```
backend/
├── run.py                              # Flask 서버 진입점 (포트 5001)
├── app/
│   ├── api/
│   │   ├── graph.py                   # 지식 그래프 API
│   │   ├── simulation.py              # 시뮬레이션 API
│   │   └── report.py                  # 리포트 API
│   └── services/
│       ├── bid_profile_generator.py   # 입찰 업체 페르소나 생성 ★
│       ├── bid_config_generator.py    # 공고 → 시뮬레이션 설정 변환 ★
│       ├── bid_report_agent.py        # 낙찰 예측 리포트 ★
│       ├── simulation_runner.py       # 시뮬레이션 프로세스 관리
│       └── graph_builder.py           # Zep 지식 그래프 구축
└── scripts/
    ├── bid_platform.py                # 입찰 플랫폼 (SQLite) ★
    └── run_bid_simulation.py          # 시뮬레이션 러너 ★
```

★ BidSwarm 신규 구현

---

## 라이선스

AGPL-3.0 — 원본 [MiroFish](https://github.com/666ghj/MiroFish) 라이선스 준수
