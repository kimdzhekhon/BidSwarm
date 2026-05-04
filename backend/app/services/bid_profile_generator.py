"""
BidSwarm 입찰 업체 프로필 생성기

Zep 지식 그래프에서 추출한 엔티티를 기반으로
입찰 업체 에이전트 페르소나를 LLM으로 생성한다.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ..utils.llm_client import get_llm_client
from ..utils.logger import get_logger

logger = get_logger("bidswarm.profile_generator")

# ------------------------------------------------------------------ #
# 기본 프로필 템플릿
# ------------------------------------------------------------------ #

PROFILE_PROMPT = """당신은 공공조달 입찰 데이터 분석 전문가입니다.
아래 기업 정보를 바탕으로 조달청 공공입찰 시뮬레이션용 에이전트 프로필을 JSON으로 생성하세요.

[기업 정보]
{entity_info}

[출력 형식] — 아래 JSON만 출력하세요 (마크다운 없이):
{{
  "company_name": "회사명",
  "industry": "업종 (예: IT서비스, 건설, 제조 등)",
  "company_size": "대기업 | 중견기업 | 중소기업",
  "win_rate": 낙찰률 정수 (0~100),
  "strategy": "입찰 전략 설명 (1~2문장, 예: 공격적 저가 전략, 보수적 안정 전략)",
  "strengths": "강점 (예: 가격 경쟁력, 기술력, 납기 준수)",
  "budget_capacity": "자금 여력 설명 (예: 예정가 80% 이하도 수익 가능)",
  "min_ratio": 최소 입찰 비율 정수 (예정가 대비 %, 60~95 사이),
  "max_ratio": 최대 입찰 비율 정수 (예정가 대비 %, min_ratio보다 크고 100 이하),
  "active_hours": [활동 시간대 리스트, 0~23 정수],
  "activity_level": 활동성 실수 (0.3~1.0)
}}
"""

DEFAULT_PROFILE = {
    "company_name": "미상기업",
    "industry": "일반",
    "company_size": "중소기업",
    "win_rate": 15,
    "strategy": "시장 평균 수준의 가격으로 안정적 참여",
    "strengths": "다양한 공공입찰 경험",
    "budget_capacity": "예정가 85% 이상에서 수익 가능",
    "min_ratio": 80,
    "max_ratio": 95,
    "active_hours": list(range(9, 18)),
    "activity_level": 0.6,
}


class BidProfileGenerator:
    """
    Zep 엔티티 → 입찰 업체 에이전트 프로필 변환기.

    엔티티 정보가 없으면 기본값으로 더미 프로필을 생성한다.
    """

    def __init__(self):
        self.client = get_llm_client()

    async def generate_profiles(
        self,
        entities: List[Dict[str, Any]],
        max_agents: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        엔티티 목록에서 입찰 에이전트 프로필을 생성한다.

        Args:
            entities: Zep에서 추출한 엔티티 리스트
            max_agents: 최대 에이전트 수

        Returns:
            에이전트 프로필 리스트 (simulation_config의 agent_configs 형식)
        """
        profiles = []
        targets = entities[:max_agents]

        for idx, entity in enumerate(targets):
            try:
                profile = await self._generate_single(idx, entity)
            except Exception as e:
                logger.warning(f"프로필 생성 실패 (entity={entity.get('name', idx)}): {e}")
                profile = self._make_default(idx, entity)

            profile["agent_id"] = idx
            profiles.append(profile)
            logger.info(f"프로필 생성: [{profile['company_name']}] id={idx}")

        # 엔티티가 없으면 더미 에이전트 생성
        if not profiles:
            profiles = self._make_dummy_profiles(max_agents)

        return profiles

    async def _generate_single(self, idx: int, entity: Dict) -> Dict:
        entity_info = json.dumps(entity, ensure_ascii=False, indent=2)
        prompt = PROFILE_PROMPT.format(entity_info=entity_info)

        response = self.client.chat.completions.create(
            model=self.client._model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        raw = response.choices[0].message.content.strip()

        # 마크다운 코드블록 제거
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1])

        profile = json.loads(raw)
        return self._validate(profile)

    def _make_default(self, idx: int, entity: Dict) -> Dict:
        profile = DEFAULT_PROFILE.copy()
        profile["company_name"] = entity.get("name", f"기업_{idx}")
        return profile

    def _validate(self, profile: Dict) -> Dict:
        """필수 필드 검증 및 기본값 보완"""
        validated = DEFAULT_PROFILE.copy()
        validated.update(profile)

        # 범위 클램핑
        validated["win_rate"] = max(0, min(100, int(validated.get("win_rate", 15))))
        validated["min_ratio"] = max(60, min(99, int(validated.get("min_ratio", 80))))
        validated["max_ratio"] = max(
            validated["min_ratio"] + 1,
            min(100, int(validated.get("max_ratio", 95))),
        )
        validated["activity_level"] = max(0.3, min(1.0, float(validated.get("activity_level", 0.6))))

        if not isinstance(validated.get("active_hours"), list):
            validated["active_hours"] = list(range(9, 18))

        return validated

    def _make_dummy_profiles(self, count: int) -> List[Dict]:
        """엔티티 없을 때 다양한 전략의 더미 에이전트 생성"""
        templates = [
            {
                "company_name": "한국건설(주)",
                "industry": "건설",
                "company_size": "중견기업",
                "win_rate": 22,
                "strategy": "기술력 기반 적정가 입찰, 덤핑 지양",
                "strengths": "시공 실적, 품질 관리",
                "budget_capacity": "예정가 88% 이상에서 수익 가능",
                "min_ratio": 85,
                "max_ratio": 97,
                "active_hours": list(range(9, 18)),
                "activity_level": 0.8,
            },
            {
                "company_name": "스마트IT솔루션",
                "industry": "IT서비스",
                "company_size": "중소기업",
                "win_rate": 35,
                "strategy": "공격적 저가 전략, 규모의 경제 활용",
                "strengths": "가격 경쟁력, 빠른 납기",
                "budget_capacity": "예정가 75% 이상에서 수익 가능",
                "min_ratio": 72,
                "max_ratio": 88,
                "active_hours": list(range(8, 20)),
                "activity_level": 0.9,
            },
            {
                "company_name": "대한엔지니어링",
                "industry": "엔지니어링",
                "company_size": "대기업",
                "win_rate": 18,
                "strategy": "보수적 안정 전략, 브랜드 가치 유지",
                "strengths": "기술력, 브랜드 신뢰도, 풍부한 레퍼런스",
                "budget_capacity": "예정가 90% 이상에서 수익 가능",
                "min_ratio": 88,
                "max_ratio": 99,
                "active_hours": list(range(9, 17)),
                "activity_level": 0.6,
            },
            {
                "company_name": "글로벌서비스(주)",
                "industry": "전문서비스",
                "company_size": "중소기업",
                "win_rate": 28,
                "strategy": "경쟁사 동향 관찰 후 마지막에 입찰가 조정",
                "strengths": "시장 정보력, 유연한 가격 전략",
                "budget_capacity": "예정가 80% 이상에서 수익 가능",
                "min_ratio": 78,
                "max_ratio": 92,
                "active_hours": list(range(10, 19)),
                "activity_level": 0.7,
            },
            {
                "company_name": "미래테크",
                "industry": "제조",
                "company_size": "중소기업",
                "win_rate": 12,
                "strategy": "기술 특화 틈새 전략, 저가 경쟁 회피",
                "strengths": "특허 기술, 품질 인증",
                "budget_capacity": "예정가 85% 이상에서 수익 가능",
                "min_ratio": 83,
                "max_ratio": 96,
                "active_hours": list(range(9, 18)),
                "activity_level": 0.5,
            },
        ]

        profiles = []
        for i in range(count):
            tmpl = templates[i % len(templates)].copy()
            if i >= len(templates):
                tmpl["company_name"] = f"{tmpl['company_name']}_{i}"
            profiles.append(tmpl)

        return profiles
