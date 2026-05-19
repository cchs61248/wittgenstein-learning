"""drift_verifier questions 模式：以 full_explanation 為唯一對齊基準。

對應 spec：2026-05-19-question-explanation-grounding-design.md §6.1
核心 regression：source_chunks 含 polling 但 full_explanation 沒提，題目測 polling
                應被標 unsupported（過去寬鬆模式會放行）。
"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.drift_verifier import DriftVerifierAgent


def _fake_llm(response_dict: dict):
    """製造一個 fake LLM，chat() 回傳指定 JSON 字串。"""
    response_json = json.dumps(response_dict, ensure_ascii=False)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            return _Resp(response_json)

    return _LLM()


def _make_agent(llm):
    """建立 DriftVerifierAgent，跳過 BaseAgent.__init__ 的 token counter 依賴。

    注意：BaseAgent._log 是 @property（回傳固定 Logger），不可直接賦值，
    因此這裡不設定 _log；_tc 為 spec 保留欄位，實際未使用亦無妨。
    """
    agent = DriftVerifierAgent.__new__(DriftVerifierAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestDriftVerifierQuestionsMode(unittest.IsolatedAsyncioTestCase):
    async def test_questions_concept_only_in_chunks_not_in_explanation_misaligned(self):
        """source_chunks 有 polling，full_explanation 沒提，題目測 polling → unsupported。

        對應使用者範例：chunks 含 polling 但 LLM 寫的教學文章沒提 polling，
        過去寬鬆模式判 aligned=True（漏網），新規則必須判 aligned=False。
        """
        llm_response = {
            "aligned": False,
            "claim_checks": [
                {
                    "claim": "polling 機制與 push 機制的差異",
                    "cited_chunk_id": "chunk_001",
                    "supported": False,
                    "issue": "polling 在 source_chunks 但 full_explanation 完全沒提及",
                }
            ],
            "unsupported_claims": ["polling 機制與 push 機制的差異（漂移：講解未提及 polling）"],
            "issues": ["題目測試 polling，但 full_explanation 中無此概念"],
            "missing_evidence": [],
            "revision_hint": "避免出 polling 題目",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [
                    {"chunk_id": "chunk_001", "text": "快取系統使用 polling 機制更新…"}
                ],
                "candidate_text": json.dumps([
                    {"question_id": "q1", "text": "polling 機制與 push 機制的差異？",
                     "evidence_chunk_ids": ["chunk_001"]}
                ], ensure_ascii=False),
                "full_explanation": "斷路器（circuit breaker）開啟時拒絕請求…",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertTrue(
            any("polling" in c for c in result["unsupported_claims"]),
            f"unsupported_claims 應該提及 polling: {result['unsupported_claims']}",
        )

    async def test_questions_concept_mentioned_but_not_explained_misaligned(self):
        """concept 字面在 explanation 出現但只當道具用，沒展開運作 → unsupported。

        regression：實測 stage 2「理財型房貸」案——
        - chunks 內有完整運作說明
        - explanation 只寫「『理財型房貸』借 500 萬滾雪球」當道具引用
        - 題目卻考「運作特性」
        過去規則只看「字面出現 + chunk 支撐」會誤判 aligned=True，學生無法答題。
        新規則要求 explanation 必須對該概念有「運作/特性/機制」展開說明。
        """
        llm_response = {
            "aligned": False,
            "claim_checks": [{
                "claim": "理財型房貸的運作特性",
                "cited_chunk_id": "chunk_012",
                "supported": False,
                "issue": "explanation 只字面提及『理財型房貸』當道具用，沒展開運作機制",
            }],
            "unsupported_claims": [
                "理財型房貸的運作特性（字面提及但 explanation 未展開運作）"
            ],
            "issues": ["題目考運作特性但 explanation 未對該概念有展開說明"],
            "missing_evidence": [],
            "revision_hint": "若要考運作特性，explanation 需先展開該概念",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [{
                    "chunk_id": "chunk_012",
                    "text": "理財型房貸的玩法就是這樣，只要額度還在，"
                            "你每個月繳的只是資金使用費，本金照樣可以拿去用。",
                }],
                "candidate_text": json.dumps([{
                    "question_id": "q1",
                    "text": "「理財型房貸」的運作特性，下列敘述何者正確？",
                    "evidence_chunk_ids": ["chunk_012"],
                }], ensure_ascii=False),
                "full_explanation":
                    "想翻身就要借錢炒股，「理財型房貸」借 500 萬元來滾雪球，"
                    "30 年後可能還剩 4,154 萬元 [chunk_012]。",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertTrue(
            any("理財型房貸" in c for c in result["unsupported_claims"]),
            f"unsupported_claims 應提及理財型房貸: {result['unsupported_claims']}",
        )

    async def test_questions_comparison_decision_not_in_explanation_misaligned(self):
        """個別概念都在 explanation 有展開，但「對比/選擇/決策」框架沒講 → unsupported。

        regression：實測 stage 6「借錢工具全解析」q_stage2_8——
        - explanation 各別介紹「融資型房貸」「元大證金股票質押」運作（個別有展開）
        - 但「謹慎者選哪個 / 安全性對比 / 斷頭風險」這個對比決策框架 explanation 完全沒講
        - 題目卻考「謹慎者選哪一種」，正解出自 chunk_025（adjacent 而非 explanation）
        過去寬鬆判定只看「key_concepts 是否在 explanation」會誤判 aligned=True，
        但學生看完講解仍不知道怎麼選，是漂移。
        新規則要求對比/決策型題目必須在 explanation 中找到對應的「對比段落 / 決策依據 / 適合族群」展開。
        """
        llm_response = {
            "aligned": False,
            "claim_checks": [{
                "claim": "謹慎者在房貸與股票質押間應選哪個",
                "cited_chunk_id": "chunk_025",
                "supported": False,
                "issue": "explanation 各別介紹兩種工具，但對比決策框架完全沒講；"
                         "正解依據在 chunk_025 而非 explanation",
            }],
            "unsupported_claims": [
                "謹慎者選房貸的決策邏輯（個別工具有展開，但對比決策框架在 explanation 缺席）"
            ],
            "issues": ["題目考對比決策但 explanation 未對該決策邏輯有展開說明"],
            "missing_evidence": [],
            "revision_hint": "若要考對比決策，explanation 需先展開選擇邏輯",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [{
                    "chunk_id": "chunk_025",
                    "text": "如果房屋貸款和股票質押能夠借出來的錢差不多，要怎麼選呢？"
                            "這就得看你個人膽識，如果你是屬於比較謹慎的人，"
                            "推薦房屋貸款，少了斷頭的危機，安全性高出很多；"
                            "但如果你是個很勇猛的人，推薦你股票質押。",
                }],
                "candidate_text": json.dumps([{
                    "question_id": "q1",
                    "text": "若房貸與股票質押能借到的金額差不多，"
                            "對於「謹慎」的人，作者建議選擇哪一種？",
                    "evidence_chunk_ids": ["chunk_025"],
                    "key_concepts_tested": ["融資型房貸", "元大證金股票質押"],
                }], ensure_ascii=False),
                "full_explanation":
                    "### 融資型房貸\n房貸把房屋抵押給銀行借錢，"
                    "還款期可達 30 年 [chunk_023]。\n"
                    "### 元大證金股票質押\n股票質押把股票當抵押品，"
                    "可借市值 60%，只要按時繳利息銀行通常會讓你續約 [chunk_024]。",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertTrue(
            any("謹慎" in c or "決策" in c or "選" in c
                for c in result["unsupported_claims"]),
            f"unsupported_claims 應提及對比決策: {result['unsupported_claims']}",
        )

    async def test_questions_concept_in_explanation_aligned(self):
        """explanation 提了 retry 機制，題目測 retry → aligned=True。"""
        llm_response = {
            "aligned": True,
            "claim_checks": [{
                "claim": "retry 機制何時觸發",
                "cited_chunk_id": "chunk_002",
                "supported": True,
                "issue": "",
            }],
            "unsupported_claims": [],
            "issues": [],
            "missing_evidence": [],
            "revision_hint": "",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [
                    {"chunk_id": "chunk_002", "text": "retry 機制在失敗時重試…"}
                ],
                "candidate_text": json.dumps([
                    {"question_id": "q1", "text": "retry 機制何時觸發？",
                     "evidence_chunk_ids": ["chunk_002"]}
                ], ensure_ascii=False),
                "full_explanation": "retry 機制在下游失敗時自動重試，避免單次失敗造成整體中斷…",
            },
        )
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])
        self.assertEqual(result["unsupported_claims"], [])

    async def test_questions_concept_in_neither_misaligned(self):
        """題目要求教材外知識 → aligned=False。"""
        llm_response = {
            "aligned": False,
            "claim_checks": [{
                "claim": "Kubernetes pod 排程策略",
                "cited_chunk_id": "",
                "supported": False,
                "issue": "概念不在 source_chunks 也不在 full_explanation",
            }],
            "unsupported_claims": ["Kubernetes pod 排程策略（教材外）"],
            "issues": ["要求教材外知識"],
            "missing_evidence": [],
            "revision_hint": "避免教材外知識",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [{"chunk_id": "chunk_001", "text": "斷路器…"}],
                "candidate_text": json.dumps([
                    {"question_id": "q1", "text": "K8s pod 怎麼排程？", "evidence_chunk_ids": []}
                ], ensure_ascii=False),
                "full_explanation": "斷路器是熔斷模式…",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])

    async def test_questions_cited_unknown_chunk_id_misaligned(self):
        """evidence_chunk_ids 帶 source_chunks 不存在的 chunk_id → 後端強制 aligned=False。"""
        llm_response = {
            "aligned": True,  # LLM 誤判
            "claim_checks": [],
            "unsupported_claims": [],
            "issues": [],
            "missing_evidence": [],
            "revision_hint": "",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [{"chunk_id": "chunk_001", "text": "斷路器…"}],
                "candidate_text": json.dumps([
                    {"question_id": "q1", "text": "Q", "evidence_chunk_ids": ["chunk_999"]}
                ], ensure_ascii=False) + " [chunk_999]",  # text 內也加 [chunk_999] 觸發 regex
                "full_explanation": "",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertTrue(
            any("chunk_999" in (c.get("issue", "") + c.get("cited_chunk_id", ""))
                for c in result["claim_checks"]),
            f"claim_checks 應該包含 chunk_999 missing: {result['claim_checks']}",
        )

    async def test_questions_analogy_in_explanation_aligned(self):
        """explanation 用類比說明概念；題目測核心概念（非類比細節）→ aligned=True。"""
        llm_response = {
            "aligned": True,
            "claim_checks": [{
                "claim": "斷路器三種狀態",
                "cited_chunk_id": "chunk_001",
                "supported": True,
                "issue": "",
            }],
            "unsupported_claims": [],
            "issues": [],
            "missing_evidence": [],
            "revision_hint": "",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [
                    {"chunk_id": "chunk_001",
                     "text": "斷路器有 closed/open/half-open 三種狀態。"}
                ],
                "candidate_text": json.dumps([
                    {"question_id": "q1", "text": "斷路器在 open 狀態時會做什麼？",
                     "evidence_chunk_ids": ["chunk_001"]}
                ], ensure_ascii=False),
                "full_explanation": "（類比說明，非原文）斷路器就像家裡的保險絲。"
                                    "斷路器有 closed/open/half-open 三種狀態，open 時拒絕請求…",
            },
        )
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])

    async def test_questions_empty_explanation_falls_back_to_strict(self):
        """full_explanation 為空 → agent 不會 crash。"""
        llm_response = {
            "aligned": False,
            "claim_checks": [],
            "unsupported_claims": ["缺少 full_explanation 對齊基準"],
            "issues": ["explanation 為空"],
            "missing_evidence": [],
            "revision_hint": "",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "questions",
                "source_chunks": [{"chunk_id": "chunk_001", "text": "X"}],
                "candidate_text": "[]",
                "full_explanation": "",
            },
        )
        result = await agent.run(ctx)
        # 不檢查 aligned 值，只確認流程沒爆
        self.assertIn("aligned", result)


if __name__ == "__main__":
    unittest.main()
