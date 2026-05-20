"""DriftVerifier explanation 模式精簡偵測（反向 coverage）regression tests。

過去 DriftVerifier 只做前向驗證（防無中生有），現在加上反向 coverage：
- 並列方案被省略 → aligned=false
- 關鍵數據被略過 → aligned=false
- 教學目標：避免 Teacher 為了縮短而砍掉教材關鍵內容
"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.drift_verifier import DriftVerifierAgent


def _fake_llm(response_dict: dict):
    response_json = json.dumps(response_dict, ensure_ascii=False)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            return _Resp(response_json)

    return _LLM()


def _make_agent(llm):
    agent = DriftVerifierAgent.__new__(DriftVerifierAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestExplanationCoverage(unittest.IsolatedAsyncioTestCase):
    """模擬 LLM 對精簡漂移的判決——agent 應正確傳遞 aligned=false 並收集 issues。"""

    async def test_parallel_options_truncated_misaligned(self):
        """範例 E case：教材列 3 種方案，講解只覆蓋 2 種 → aligned=false。"""
        llm_response = {
            "aligned": False,
            "claim_checks": [],
            "unsupported_claims": [
                "股票質押的運作機制與適用情境（教材有但講解未涵蓋）"
            ],
            "issues": [
                "精簡省略：教材列了 3 種借錢方式，講解只覆蓋 2 種，遺漏「股票質押」"
            ],
            "missing_evidence": ["股票質押運作機制"],
            "revision_hint": "請補上「股票質押」的運作（用股票當抵押品借錢、"
                             "可借市值 60%）與適用情境",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [
                    {"chunk_id": "chunk_021",
                     "text": "借錢炒股的主要方法分為 3 種：信用貸款、房屋貸款、股票質押"},
                    {"chunk_id": "chunk_022", "text": "信用貸款的運作..."},
                    {"chunk_id": "chunk_023", "text": "房屋貸款的運作..."},
                    {"chunk_id": "chunk_024", "text": "股票質押的運作..."},
                ],
                "candidate_text":
                    "借錢炒股有兩種安全選擇：信用貸款與房屋貸款 [chunk_022, chunk_023]。",
                "full_explanation": "",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        # 必須在 issues 或 unsupported_claims 中明確提到「股票質押」缺漏
        all_text = " ".join(result.get("issues", [])) + \
                   " ".join(result.get("unsupported_claims", []))
        self.assertIn("股票質押", all_text)
        # revision_hint 給 Teacher retry 用，必須具體
        self.assertTrue(result.get("revision_hint"))

    async def test_key_data_truncated_misaligned(self):
        """範例 F case：教材有具體計算數據，講解全部略過 → aligned=false。"""
        llm_response = {
            "aligned": False,
            "claim_checks": [],
            "unsupported_claims": [
                "無本炒股的具體現金流數字（1000 萬借款、4.1 萬還款、5.8 萬股息）"
            ],
            "issues": [
                "精簡省略：教材有具體計算示範（1000 萬借款 / 4.1 萬還款 / "
                "5.8 萬股息 / 1.6 萬倒貼），講解全部略過，學生無法做數字應用題"
            ],
            "missing_evidence": [],
            "revision_hint": "請補上完整的現金流計算範例",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [{
                    "chunk_id": "chunk_030",
                    "text": "1000 萬房貸利息 2.88%、30 年，每月還 4.1 萬；"
                            "高股息 ETF 殖利率 7%，每月領 5.8 萬；"
                            "銀行倒貼你 1.6 萬生活費",
                }],
                "candidate_text":
                    "借房貸投資高股息 ETF 是一種無本炒股的方式，"
                    "可以靠股息支付利息 [chunk_030]。",
                "full_explanation": "",
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        # 必須明確指出數據被略
        joined = " ".join(result.get("issues", []))
        self.assertTrue(
            any(t in joined for t in ["數據", "計算", "1000 萬", "1,000 萬"]),
            f"issues 應提及數據省略：{joined}",
        )

    async def test_complete_explanation_aligned(self):
        """無精簡的講解 → aligned=true（不該誤判）。"""
        llm_response = {
            "aligned": True,
            "claim_checks": [{
                "claim": "三種借錢工具各有運作",
                "cited_chunk_id": "chunk_021",
                "supported": True,
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
                "content_type": "explanation",
                "source_chunks": [
                    {"chunk_id": "chunk_021",
                     "text": "借錢炒股的主要方法分為 3 種：信用貸款、房屋貸款、股票質押"},
                ],
                "candidate_text":
                    "借錢炒股有三種工具：信用貸款、房屋貸款、股票質押 [chunk_021]，"
                    "三者各有運作機制與適用情境。",
                "full_explanation": "",
            },
        )
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])


class TestAnalogyWhitelistPrompt(unittest.TestCase):
    """驗證 DriftVerifier prompt 已加入「類比作情境包裝可 aligned」的規則與範例 G。

    這是 prompt-level regression：實際 LLM 行為驗證在實戰中，但 prompt 內容
    必須包含明確的白名單規則與範例，否則 LLM 仍會把類比情境題誤判 false。
    """

    def test_prompt_includes_analogy_whitelist_rule(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        self.assertIn("情境包裝", prompt)
        self.assertIn("不要因為", prompt)

    def test_prompt_includes_example_g(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        # 「範例 G（」帶括弧才會精確命中範例段落本身，
        # 避免命中規則段中「詳見範例 G」的反向引用
        self.assertIn("範例 G（", prompt)
        g_idx = prompt.find("範例 G（")
        self.assertGreater(g_idx, 0)
        g_section = prompt[g_idx:g_idx + 1000]
        self.assertIn("aligned=true", g_section)
        # 範例 G 必須示範「類比作情境包裝、答案對應教材」的核心論點
        self.assertIn("情境包裝", g_section)


if __name__ == "__main__":
    unittest.main()
