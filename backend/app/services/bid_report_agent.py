"""
BidSwarm 낙찰 예측 리포트 에이전트

시뮬레이션 완료 후 입찰 결과를 분석해
낙찰 예측, 최적 입찰가, 경쟁 전략 인사이트를 생성한다.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ..utils.llm_client import get_llm_client
from ..utils.logger import get_logger

logger = get_logger("bidswarm.report_agent")

REPORT_PROMPT = """당신은 조달청 공공입찰 분석 전문가입니다.
아래 입찰 시뮬레이션 결과를 분석해 실무에 유용한 리포트를 작성하세요.

[공고 정보]
{notice_info}

[시뮬레이션 통계]
{stats}

[입찰 참여 현황]
{bids_info}

[에이전트 행동 로그 요약]
{actions_summary}

다음 항목을 포함한 분석 리포트를 작성하세요:

1. **낙찰 결과 요약** — 낙찰자, 낙찰가, 예정가 대비 비율
2. **입찰 경쟁 분석** — 참여사 수, 가격 분포, 경쟁 강도
3. **낙찰 전략 분석** — 낙찰 업체의 전략, 왜 낙찰됐는지
4. **최적 입찰가 추천** — 다음 유사 공고 대비 최적 범위 (예정가 대비 %)
5. **리스크 요인** — 덤핑 우려, 이상 입찰 여부 등
6. **결론 및 시사점** — 입찰 참여 업체가 얻을 수 있는 핵심 인사이트

한국어로 작성하고, 각 항목은 명확한 제목과 함께 3~5문장으로 서술하세요.
"""

OPTIMAL_BID_PROMPT = """아래 입찰 시뮬레이션 데이터를 바탕으로
특정 업체가 다음 유사 공고에서 낙찰을 받기 위한 최적 입찰가 전략을 JSON으로 제시하세요.

[시뮬레이션 결과]
{summary}

[대상 업체]
{company_profile}

[출력 형식] — JSON만 출력 (마크다운 없이):
{{
  "optimal_ratio_min": 예정가 대비 최적 최소 비율 정수 (%),
  "optimal_ratio_max": 예정가 대비 최적 최대 비율 정수 (%),
  "recommended_ratio": 가장 추천하는 단일 비율 정수 (%),
  "win_probability": 이 전략으로 낙찰될 확률 추정 정수 (0~100),
  "strategy_advice": "전략 조언 (2~3문장)",
  "cautions": "주의사항 (1~2문장)"
}}
"""


class BidReportAgent:
    """
    시뮬레이션 결과 → 낙찰 예측 리포트 생성.
    """

    def __init__(self):
        self.client = get_llm_client()

    async def generate_report(
        self,
        summary: Dict[str, Any],
        actions: List[Dict[str, Any]],
        agent_profiles: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            summary: BidPlatform.get_bid_summary() 반환값
            actions: actions.jsonl의 행동 기록 리스트
            agent_profiles: 에이전트 프로필 (선택)

        Returns:
            {
                "report_text": str,   # 자연어 리포트
                "stats": dict,        # 통계 요약
                "optimal_bids": list, # 에이전트별 최적 입찰가 추천
            }
        """
        notice = summary.get("notice", {})
        stats = summary.get("stats", {})
        active_bids = summary.get("active_bids", [])
        winner = summary.get("winner")

        # 행동 로그 요약
        actions_summary = _summarize_actions(actions)

        # 리포트 생성
        report_text = await self._generate_report_text(
            notice, stats, active_bids, actions_summary
        )

        # 에이전트별 최적 입찰가 추천
        optimal_bids = []
        if agent_profiles:
            for profile in agent_profiles[:5]:  # 최대 5개
                try:
                    opt = await self._get_optimal_bid(summary, profile)
                    opt["company_name"] = profile.get("company_name", "")
                    opt["agent_id"] = profile.get("agent_id", 0)
                    optimal_bids.append(opt)
                except Exception as e:
                    logger.warning(f"최적 입찰가 생성 실패 ({profile.get('company_name')}): {e}")

        return {
            "report_text": report_text,
            "stats": stats,
            "winner": winner,
            "optimal_bids": optimal_bids,
        }

    async def _generate_report_text(
        self,
        notice: Dict,
        stats: Dict,
        bids: List[Dict],
        actions_summary: str,
    ) -> str:
        notice_info = json.dumps(notice, ensure_ascii=False, indent=2)
        stats_info = json.dumps(stats, ensure_ascii=False, indent=2)
        bids_info = "\n".join(
            f"- {b['agent_name']}: {b['price']:,}원 (예정가 대비 {round(b['price']/notice.get('budget',1)*100,1)}%)"
            for b in bids
        ) or "입찰 없음"

        prompt = REPORT_PROMPT.format(
            notice_info=notice_info,
            stats=stats_info,
            bids_info=bids_info,
            actions_summary=actions_summary,
        )

        response = self.client.chat.completions.create(
            model=self.client._model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        return response.choices[0].message.content.strip()

    async def _get_optimal_bid(self, summary: Dict, profile: Dict) -> Dict:
        summary_str = json.dumps(summary, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False)

        prompt = OPTIMAL_BID_PROMPT.format(
            summary=summary_str[:3000],
            company_profile=profile_str,
        )

        response = self.client.chat.completions.create(
            model=self.client._model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1])
        return json.loads(raw)


def _summarize_actions(actions: List[Dict]) -> str:
    if not actions:
        return "행동 기록 없음"

    counts: Dict[str, int] = {}
    for a in actions:
        t = a.get("action_type", "UNKNOWN")
        counts[t] = counts.get(t, 0) + 1

    lines = [f"- {t}: {c}회" for t, c in sorted(counts.items(), key=lambda x: -x[1])]
    lines.insert(0, f"총 행동 수: {len(actions)}회")
    return "\n".join(lines)
