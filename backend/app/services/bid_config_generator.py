"""
BidSwarm 시뮬레이션 설정 생성기

조달청 공고 정보와 에이전트 프로필을 조합해
run_bid_simulation.py가 소비하는 simulation_config.json을 생성한다.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.llm_client import get_llm_client
from ..utils.logger import get_logger

logger = get_logger("bidswarm.config_generator")

NOTICE_ANALYSIS_PROMPT = """당신은 조달청 공공입찰 전문가입니다.
아래 공고 내용을 분석해 입찰 시뮬레이션에 필요한 설정값을 JSON으로 추출하세요.

[공고 원문]
{raw_text}

[출력 형식] — JSON만 출력 (마크다운 없이):
{{
  "title": "공고명",
  "budget": 예정 가격 정수 (원 단위, 없으면 100000000),
  "category": "사업 카테고리 (예: IT, 건설, 용역, 물품)",
  "description": "공고 핵심 내용 요약 (3~5문장)",
  "deadline": "입찰 마감일 (YYYY-MM-DD, 알 수 없으면 빈 문자열)",
  "recommended_agents": 추천 에이전트 수 정수 (3~10),
  "recommended_rounds": 추천 라운드 수 정수 (10~50),
  "difficulty": "low | medium | high (경쟁 난이도)"
}}
"""


class BidConfigGenerator:
    """
    공고 텍스트 + 에이전트 프로필 → simulation_config.json 생성.
    """

    def __init__(self):
        self.client = get_llm_client()

    async def generate(
        self,
        raw_notice_text: str,
        agent_profiles: List[Dict[str, Any]],
        simulation_id: Optional[str] = None,
        extra_rounds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            raw_notice_text: 공고 원문 텍스트
            agent_profiles: bid_profile_generator가 만든 프로필 리스트
            simulation_id: 없으면 자동 생성
            extra_rounds: 강제 라운드 수 (없으면 LLM 추천값 사용)

        Returns:
            simulation_config dict (파일에 저장해 run_bid_simulation.py에 전달)
        """
        sim_id = simulation_id or f"sim_{uuid.uuid4().hex[:8]}"

        notice_cfg = await self._analyze_notice(raw_notice_text)
        notice_cfg["notice_id"] = 1

        total_rounds = extra_rounds or notice_cfg.pop("recommended_rounds", 20)
        notice_cfg.pop("recommended_agents", None)
        notice_cfg.pop("difficulty", None)

        # agent_id 부여
        agent_cfgs = []
        for i, profile in enumerate(agent_profiles):
            cfg = profile.copy()
            cfg["agent_id"] = i
            agent_cfgs.append(cfg)

        config = {
            "simulation_id": sim_id,
            "created_at": datetime.now().isoformat(),
            "notice": notice_cfg,
            "agent_configs": agent_cfgs,
            "simulation": {
                "total_rounds": total_rounds,
            },
        }

        logger.info(
            f"설정 생성 완료: id={sim_id}, 에이전트={len(agent_cfgs)}개, 라운드={total_rounds}"
        )
        return config

    async def _analyze_notice(self, raw_text: str) -> Dict[str, Any]:
        prompt = NOTICE_ANALYSIS_PROMPT.format(raw_text=raw_text[:4000])

        try:
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

        except Exception as e:
            logger.warning(f"공고 분석 실패, 기본값 사용: {e}")
            return {
                "title": "공공입찰 공고",
                "budget": 100_000_000,
                "category": "일반",
                "description": raw_text[:200],
                "deadline": "",
                "recommended_agents": 5,
                "recommended_rounds": 20,
                "difficulty": "medium",
            }
