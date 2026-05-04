# BidSwarm

조달청 공공입찰 낙찰가 예측 엔진 — 군집 에이전트 시뮬레이션으로 경쟁 입찰자를 모델링하고 최적 입찰가를 산출합니다.

> MiroFish 백엔드 엔진 기반 포크 프로젝트

---

## 구조

```
backend/
├── run.py                          # Flask 진입점 (포트 5001)
├── app/
│   ├── api/
│   │   ├── graph.py               # 지식 그래프 구축 API
│   │   ├── simulation.py          # 에이전트 시뮬레이션 API
│   │   └── report.py              # 낙찰 예측 리포트 API
│   ├── services/
│   │   ├── simulation_runner.py   # 시뮬레이션 실행 엔진
│   │   ├── oasis_profile_generator.py  # 입찰자 에이전트 페르소나
│   │   ├── graph_builder.py       # 입찰 데이터 지식 그래프
│   │   └── report_agent.py        # 낙찰 분석 리포트 생성
│   └── models/
│       ├── project.py             # 입찰 프로젝트 모델
│       └── task.py                # 태스크 모델
└── requirements.txt
```

---

## 시작하기

### 요구사항

- Python 3.11+
- LLM API 키 (OpenAI 호환)
- Zep Cloud API 키

### 설치

```bash
cd backend
cp ../.env.example .env
# .env 파일에 API 키 입력

pip install uv
uv sync
python run.py
```

서버 실행 후 `http://localhost:5001/health` 로 확인.

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 확인 |
| POST | `/api/graph/build` | 입찰 데이터 지식 그래프 구축 |
| POST | `/api/simulation/create` | 시뮬레이션 생성 |
| POST | `/api/simulation/start` | 시뮬레이션 실행 |
| GET | `/api/simulation/<id>/run-status` | 진행 상태 조회 |
| POST | `/api/report` | 낙찰 예측 리포트 생성 |

---

## 라이선스

AGPL-3.0 — 원본 [MiroFish](https://github.com/666ghj/MiroFish) 라이선스 준수
