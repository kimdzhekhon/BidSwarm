"""
BidPlatform — 조달청 공공입찰 시뮬레이션 플랫폼

SQLite 기반으로 입찰 공고, 입찰가 제출, 에이전트 행동을 관리한다.
OASIS의 Twitter/Reddit 플랫폼을 대체한다.
"""

import json
import sqlite3
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class BidActionType(str, Enum):
    SUBMIT_BID = "SUBMIT_BID"           # 입찰가 제출
    REVISE_BID = "REVISE_BID"           # 입찰가 수정
    WITHDRAW_BID = "WITHDRAW_BID"       # 입찰 철회
    OBSERVE_COMPETITORS = "OBSERVE_COMPETITORS"  # 경쟁사 관찰 (공개 정보)
    ANALYZE_NOTICE = "ANALYZE_NOTICE"   # 공고 분석
    DO_NOTHING = "DO_NOTHING"           # 관망


class BidStatus(str, Enum):
    ACTIVE = "active"
    REVISED = "revised"
    WITHDRAWN = "withdrawn"


SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id   INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    budget      INTEGER NOT NULL,
    description TEXT,
    category    TEXT,
    deadline    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bids (
    bid_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id    INTEGER NOT NULL,
    agent_id     INTEGER NOT NULL,
    agent_name   TEXT NOT NULL,
    price        INTEGER NOT NULL,
    strategy_note TEXT,
    round_num    INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (notice_id) REFERENCES notices(notice_id)
);

CREATE TABLE IF NOT EXISTS bid_trace (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    round_num    INTEGER NOT NULL,
    agent_id     INTEGER NOT NULL,
    agent_name   TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    action_args  TEXT,
    result       TEXT,
    created_at   TEXT NOT NULL
);
"""


class BidPlatform:
    """
    입찰 시뮬레이션 플랫폼.

    에이전트들이 입찰 공고에 대해 가격을 제시하고,
    경쟁사 행동을 관찰하며, 전략적으로 입찰가를 조정한다.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = self._connect()
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ #
    # 공고 관리
    # ------------------------------------------------------------------ #

    def post_notice(
        self,
        notice_id: int,
        title: str,
        budget: int,
        description: str,
        category: str = "",
        deadline: str = "",
    ):
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO notices
                (notice_id, title, budget, description, category, deadline, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (notice_id, title, budget, description, category, deadline, _now()),
        )
        conn.commit()
        conn.close()

    def get_notice(self, notice_id: int) -> Optional[Dict]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM notices WHERE notice_id = ?", (notice_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # 에이전트 행동 처리
    # ------------------------------------------------------------------ #

    def step(
        self,
        round_num: int,
        agent_id: int,
        agent_name: str,
        action_type: BidActionType,
        action_args: Dict[str, Any],
        notice_id: int,
    ) -> Dict[str, Any]:
        """
        에이전트 행동 하나를 처리하고 결과를 반환한다.
        모든 행동은 bid_trace에 기록된다.
        """
        handler = {
            BidActionType.SUBMIT_BID: self._handle_submit,
            BidActionType.REVISE_BID: self._handle_revise,
            BidActionType.WITHDRAW_BID: self._handle_withdraw,
            BidActionType.OBSERVE_COMPETITORS: self._handle_observe,
            BidActionType.ANALYZE_NOTICE: self._handle_analyze,
            BidActionType.DO_NOTHING: self._handle_nothing,
        }.get(action_type, self._handle_nothing)

        result = handler(agent_id, agent_name, action_args, notice_id, round_num)

        self._trace(round_num, agent_id, agent_name, action_type, action_args, result)
        return result

    def _handle_submit(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        price = args.get("price")
        if not price or not isinstance(price, int):
            return {"success": False, "error": "price 필드가 없거나 정수가 아닙니다."}

        notice = self.get_notice(notice_id)
        if not notice:
            return {"success": False, "error": "공고를 찾을 수 없습니다."}

        conn = self._connect()
        # 기존 활성 입찰이 있으면 revised 처리
        conn.execute(
            "UPDATE bids SET status = 'revised' WHERE notice_id = ? AND agent_id = ? AND status = 'active'",
            (notice_id, agent_id),
        )
        conn.execute(
            """
            INSERT INTO bids (notice_id, agent_id, agent_name, price, strategy_note, round_num, submitted_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (notice_id, agent_id, agent_name, price, args.get("strategy_note", ""), round_num, _now()),
        )
        conn.commit()
        conn.close()

        ratio = round(price / notice["budget"] * 100, 2) if notice["budget"] else 0
        return {"success": True, "price": price, "budget_ratio": ratio}

    def _handle_revise(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        new_price = args.get("new_price")
        if not new_price or not isinstance(new_price, int):
            return {"success": False, "error": "new_price 필드가 없거나 정수가 아닙니다."}

        conn = self._connect()
        existing = conn.execute(
            "SELECT * FROM bids WHERE notice_id = ? AND agent_id = ? AND status = 'active'",
            (notice_id, agent_id),
        ).fetchone()

        if not existing:
            conn.close()
            return {"success": False, "error": "수정할 활성 입찰이 없습니다. 먼저 SUBMIT_BID를 하세요."}

        old_price = existing["price"]
        conn.execute(
            "UPDATE bids SET price = ?, strategy_note = ?, round_num = ?, submitted_at = ? WHERE bid_id = ?",
            (new_price, args.get("reason", ""), round_num, _now(), existing["bid_id"]),
        )
        conn.commit()
        conn.close()
        return {"success": True, "old_price": old_price, "new_price": new_price}

    def _handle_withdraw(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        conn = self._connect()
        updated = conn.execute(
            "UPDATE bids SET status = 'withdrawn' WHERE notice_id = ? AND agent_id = ? AND status = 'active'",
            (notice_id, agent_id),
        ).rowcount
        conn.commit()
        conn.close()
        if updated:
            return {"success": True, "message": "입찰을 철회했습니다."}
        return {"success": False, "error": "철회할 활성 입찰이 없습니다."}

    def _handle_observe(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        """
        경쟁사 공개 정보만 반환한다.
        실제 입찰가는 비공개 — 입찰 참여 여부와 입찰자 수만 공개.
        """
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT agent_name, status, round_num
            FROM bids
            WHERE notice_id = ? AND agent_id != ? AND status != 'withdrawn'
            ORDER BY submitted_at DESC
            """,
            (notice_id, agent_id),
        ).fetchall()
        conn.close()

        competitors = [{"name": r["agent_name"], "status": r["status"], "round": r["round_num"]} for r in rows]
        return {
            "success": True,
            "competitor_count": len(competitors),
            "competitors": competitors,
        }

    def _handle_analyze(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        notice = self.get_notice(notice_id)
        if not notice:
            return {"success": False, "error": "공고를 찾을 수 없습니다."}
        return {"success": True, "notice": notice}

    def _handle_nothing(self, agent_id, agent_name, args, notice_id, round_num) -> Dict:
        return {"success": True, "message": "관망"}

    # ------------------------------------------------------------------ #
    # 조회
    # ------------------------------------------------------------------ #

    def get_active_bids(self, notice_id: int) -> List[Dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM bids WHERE notice_id = ? AND status = 'active' ORDER BY price ASC",
            (notice_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_agent_bid(self, notice_id: int, agent_id: int) -> Optional[Dict]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM bids WHERE notice_id = ? AND agent_id = ? AND status = 'active'",
            (notice_id, agent_id),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_trace(self, since_rowid: int = 0) -> Tuple[List[Dict], int]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM bid_trace WHERE rowid > ? ORDER BY rowid ASC",
            (since_rowid,),
        ).fetchall()
        conn.close()
        if not rows:
            return [], since_rowid
        result = [dict(r) for r in rows]
        new_rowid = result[-1]["rowid"]
        return result, new_rowid

    def get_bid_summary(self, notice_id: int) -> Dict:
        """낙찰 분석용 전체 요약 반환"""
        conn = self._connect()
        notice = self.get_notice(notice_id)
        all_bids = [dict(r) for r in conn.execute(
            "SELECT * FROM bids WHERE notice_id = ? ORDER BY price ASC",
            (notice_id,),
        ).fetchall()]
        active_bids = [b for b in all_bids if b["status"] == "active"]
        conn.close()

        if not active_bids:
            return {"notice": notice, "active_bids": [], "winner": None, "stats": {}}

        prices = [b["price"] for b in active_bids]
        winner = active_bids[0]  # 최저가 낙찰

        return {
            "notice": notice,
            "active_bids": active_bids,
            "winner": winner,
            "stats": {
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": int(sum(prices) / len(prices)),
                "bid_count": len(prices),
                "budget": notice["budget"] if notice else 0,
                "winning_ratio": round(winner["price"] / notice["budget"] * 100, 2) if notice else 0,
            },
        }

    # ------------------------------------------------------------------ #
    # 내부 유틸
    # ------------------------------------------------------------------ #

    def _trace(self, round_num, agent_id, agent_name, action_type, action_args, result):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO bid_trace (round_num, agent_id, agent_name, action_type, action_args, result, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                round_num,
                agent_id,
                agent_name,
                action_type.value if isinstance(action_type, BidActionType) else str(action_type),
                json.dumps(action_args, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
        conn.close()


def _now() -> str:
    return datetime.now().isoformat()
