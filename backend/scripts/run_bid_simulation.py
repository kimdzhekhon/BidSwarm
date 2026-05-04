"""
BidSwarm 입찰 시뮬레이션 러너

camel-ai ChatAgent 기반으로 입찰 업체 에이전트들이
BidPlatform 위에서 경쟁 입찰을 시뮬레이션한다.

사용:
    python run_bid_simulation.py --config simulation_config.json
    python run_bid_simulation.py --config simulation_config.json --max-rounds 20
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------ #
# 경로 설정
# ------------------------------------------------------------------ #
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, ".."))
_project_root = os.path.abspath(os.path.join(_backend_dir, ".."))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
_env = os.path.join(_project_root, ".env")
if os.path.exists(_env):
    load_dotenv(_env)
else:
    load_dotenv(os.path.join(_backend_dir, ".env"))

# ------------------------------------------------------------------ #
# 외부 의존성
# ------------------------------------------------------------------ #
try:
    from camel.agents import ChatAgent
    from camel.messages import BaseMessage
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType, RoleType
except ImportError as e:
    print(f"오류: camel-ai 패키지가 없습니다 — {e}")
    print("설치: pip install camel-ai")
    sys.exit(1)

from bid_platform import BidActionType, BidPlatform

# ------------------------------------------------------------------ #
# 글로벌 종료 이벤트
# ------------------------------------------------------------------ #
_shutdown = False


def _sig_handler(signum, frame):
    global _shutdown
    _shutdown = True
    print(f"\n종료 신호 수신 ({signum}), 현재 라운드 완료 후 종료합니다...")


signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)

# ------------------------------------------------------------------ #
# 로깅
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bidswarm")


# ================================================================== #
# LLM 모델 생성
# ================================================================== #

def create_model():
    api_key = os.environ.get("LLM_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "")
    model_name = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")

    if not api_key:
        raise ValueError(".env에 LLM_API_KEY가 설정되지 않았습니다.")

    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_API_BASE_URL"] = base_url

    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=model_name,
    )


# ================================================================== #
# 입찰 에이전트
# ================================================================== #

SYSTEM_PROMPT_TEMPLATE = """당신은 조달청 공공입찰에 참여하는 기업 입찰 담당자입니다.

[회사 정보]
- 회사명: {company_name}
- 업종: {industry}
- 규모: {company_size}
- 과거 낙찰률: {win_rate}%
- 입찰 전략: {strategy}
- 강점: {strengths}
- 자금 여력: {budget_capacity}

[입찰 원칙]
1. 예정 가격(budget)의 {min_ratio}% ~ {max_ratio}% 범위 내에서 입찰가를 결정하세요.
2. 경쟁사보다 낮은 가격을 제시해야 낙찰 가능성이 높아지지만, 너무 낮으면 적자입니다.
3. 공고 내용을 분석해 원가 구조를 추정하고 전략적으로 판단하세요.
4. 매 라운드마다 반드시 아래 JSON 형식으로만 응답하세요.

[응답 형식]
반드시 아래 JSON만 출력하세요 (마크다운 블록 없이):
{{
  "action": "SUBMIT_BID" | "REVISE_BID" | "WITHDRAW_BID" | "OBSERVE_COMPETITORS" | "ANALYZE_NOTICE" | "DO_NOTHING",
  "args": {{
    // SUBMIT_BID:  "price": 정수, "strategy_note": "전략 설명"
    // REVISE_BID:  "new_price": 정수, "reason": "수정 이유"
    // WITHDRAW_BID: {{}}
    // OBSERVE_COMPETITORS: {{}}
    // ANALYZE_NOTICE: {{}}
    // DO_NOTHING: {{}}
  }},
  "reasoning": "판단 근거 (내부용, 1~2문장)"
}}
"""

ROUND_PROMPT_TEMPLATE = """[라운드 {round_num} / {total_rounds}]

=== 입찰 공고 ===
공고명: {notice_title}
예정 가격: {budget:,}원
카테고리: {category}
설명: {notice_description}

=== 현재 내 입찰 상태 ===
{my_bid_status}

=== 경쟁사 현황 ===
{competitor_status}

=== 시장 분석 ===
- 현재까지 입찰 참여 업체 수: {bid_count}개사
- 남은 라운드: {remaining_rounds}

지금 어떻게 하시겠습니까?
"""


class BidAgent:
    """단일 입찰 업체 에이전트"""

    def __init__(self, profile: Dict[str, Any], model):
        self.agent_id: int = profile["agent_id"]
        self.agent_name: str = profile["company_name"]
        self.profile = profile

        system_msg = BaseMessage.make_assistant_message(
            role_name="입찰 담당자",
            content=SYSTEM_PROMPT_TEMPLATE.format(**profile),
        )
        self._chat_agent = ChatAgent(
            system_message=system_msg,
            model=model,
        )

    def decide(self, round_num: int, total_rounds: int, notice: Dict, platform: "BidPlatform") -> Dict[str, Any]:
        """라운드 상황을 보고 행동을 결정한다."""
        my_bid = platform.get_agent_bid(notice["notice_id"], self.agent_id)
        competitors = platform._handle_observe(
            self.agent_id, self.agent_name, {}, notice["notice_id"], round_num
        )

        my_bid_status = (
            f"입찰가: {my_bid['price']:,}원 (제출 라운드: {my_bid['round_num']})"
            if my_bid
            else "아직 입찰하지 않음"
        )
        comp_count = competitors.get("competitor_count", 0)
        competitor_status = (
            f"{comp_count}개사 참여 중"
            if comp_count > 0
            else "아직 다른 참여사 없음"
        )

        prompt = ROUND_PROMPT_TEMPLATE.format(
            round_num=round_num,
            total_rounds=total_rounds,
            notice_title=notice["title"],
            budget=notice["budget"],
            category=notice.get("category", ""),
            notice_description=notice.get("description", ""),
            my_bid_status=my_bid_status,
            competitor_status=competitor_status,
            bid_count=comp_count + (1 if my_bid else 0),
            remaining_rounds=total_rounds - round_num,
        )

        user_msg = BaseMessage.make_user_message(role_name="시스템", content=prompt)
        response = self._chat_agent.step(user_msg)
        content = response.msgs[0].content if response.msgs else ""

        return _parse_action(content)


def _parse_action(raw: str) -> Dict[str, Any]:
    """LLM 응답에서 JSON 액션을 파싱한다."""
    raw = raw.strip()
    # 마크다운 코드블록 제거
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
    try:
        data = json.loads(raw)
        action_str = data.get("action", "DO_NOTHING").upper()
        try:
            action = BidActionType(action_str)
        except ValueError:
            action = BidActionType.DO_NOTHING
        return {
            "action": action,
            "args": data.get("args", {}),
            "reasoning": data.get("reasoning", ""),
        }
    except (json.JSONDecodeError, KeyError):
        return {"action": BidActionType.DO_NOTHING, "args": {}, "reasoning": "파싱 실패"}


# ================================================================== #
# 시뮬레이션 러너
# ================================================================== #

def load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_run_state(sim_dir: str, state: Dict):
    path = os.path.join(sim_dir, "run_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_action_jsonl(sim_dir: str, entry: Dict):
    path = os.path.join(sim_dir, "actions.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def run_simulation(config: Dict, sim_dir: str, max_rounds: Optional[int] = None):
    global _shutdown

    notice_cfg = config["notice"]
    agent_cfgs = config["agent_configs"]
    sim_cfg = config.get("simulation", {})
    total_rounds = sim_cfg.get("total_rounds", 30)
    if max_rounds:
        total_rounds = min(total_rounds, max_rounds)

    notice_id = notice_cfg.get("notice_id", 1)
    db_path = os.path.join(sim_dir, "bid_simulation.db")

    platform = BidPlatform(db_path)

    # 공고 등록
    platform.post_notice(
        notice_id=notice_id,
        title=notice_cfg["title"],
        budget=notice_cfg["budget"],
        description=notice_cfg.get("description", ""),
        category=notice_cfg.get("category", ""),
        deadline=notice_cfg.get("deadline", ""),
    )
    print(f"공고 등록: [{notice_cfg['title']}] 예정가 {notice_cfg['budget']:,}원")

    # 모델 & 에이전트 생성
    model = create_model()
    agents = [BidAgent(cfg, model) for cfg in agent_cfgs]
    print(f"에이전트 {len(agents)}개 생성 완료")

    notice = platform.get_notice(notice_id)
    last_rowid = 0
    start = datetime.now()

    for round_num in range(1, total_rounds + 1):
        if _shutdown:
            break

        print(f"\n--- 라운드 {round_num}/{total_rounds} ---")

        for agent in agents:
            if _shutdown:
                break

            decision = agent.decide(round_num, total_rounds, notice, platform)
            action = decision["action"]
            args = decision["args"]

            result = platform.step(round_num, agent.agent_id, agent.agent_name, action, args, notice_id)

            entry = {
                "round_num": round_num,
                "timestamp": datetime.now().isoformat(),
                "agent_id": agent.agent_id,
                "agent_name": agent.agent_name,
                "action_type": action.value,
                "action_args": args,
                "result": result,
                "reasoning": decision.get("reasoning", ""),
            }
            log_action_jsonl(sim_dir, entry)

            status = "✓" if result.get("success") else "✗"
            note = ""
            if action == BidActionType.SUBMIT_BID and result.get("success"):
                note = f"→ {result['price']:,}원 ({result['budget_ratio']}%)"
            elif action == BidActionType.REVISE_BID and result.get("success"):
                note = f"→ {result['old_price']:,} → {result['new_price']:,}원"
            print(f"  {status} [{agent.agent_name}] {action.value} {note}")

        # 라운드 종료 후 상태 기록
        new_traces, last_rowid = platform.get_trace(last_rowid)
        active_bids = platform.get_active_bids(notice_id)
        elapsed = (datetime.now() - start).total_seconds()

        write_run_state(sim_dir, {
            "status": "running",
            "round_num": round_num,
            "total_rounds": total_rounds,
            "active_bid_count": len(active_bids),
            "elapsed_seconds": round(elapsed, 1),
            "progress_pct": round(round_num / total_rounds * 100, 1),
            "updated_at": datetime.now().isoformat(),
        })

    # 종료
    summary = platform.get_bid_summary(notice_id)
    write_run_state(sim_dir, {
        "status": "completed",
        "round_num": total_rounds,
        "total_rounds": total_rounds,
        "progress_pct": 100.0,
        "summary": summary,
        "updated_at": datetime.now().isoformat(),
    })

    print("\n=== 시뮬레이션 완료 ===")
    if summary["winner"]:
        w = summary["winner"]
        s = summary["stats"]
        print(f"낙찰자: {w['agent_name']} — {w['price']:,}원 (예정가 대비 {s['winning_ratio']}%)")
        print(f"참여: {s['bid_count']}개사 | 최저 {s['min_price']:,} / 최고 {s['max_price']:,} / 평균 {s['avg_price']:,}원")
    else:
        print("유효한 입찰이 없습니다.")


async def main():
    parser = argparse.ArgumentParser(description="BidSwarm 입찰 시뮬레이션")
    parser.add_argument("--config", required=True, help="simulation_config.json 경로")
    parser.add_argument("--max-rounds", type=int, default=None, help="최대 라운드 수")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"오류: 설정 파일 없음 — {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    sim_dir = os.path.dirname(os.path.abspath(args.config))

    await run_simulation(config, sim_dir, args.max_rounds)


if __name__ == "__main__":
    asyncio.run(main())
