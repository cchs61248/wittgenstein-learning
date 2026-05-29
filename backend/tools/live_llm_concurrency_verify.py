"""手動驗證 LLM_MAX_CONCURRENT 是否限制平行 LLM 呼叫。

同時啟動 N 個 asyncio 工作，各自呼叫 ``.chat()``（或 dry-run 下同等的 stub），
並在**已取得全局 llm slot 之後**、於 ``_do_chat`` Body 記錄每個任務的
``perf_counter`` 起訖，再統計區間重疊與「若以 slot 上限分批」的估算。

**Dry run** 不呼叫任何遠端 API：內嵌與測試類似的 stub provider，在 ``llm_slot``
內僅 ``asyncio.sleep``，仍走 ``BaseLLMProvider.chat()`` 的 slot 包裝。

**Live** 模式使用 ``create_provider(DEFAULT_PROVIDER)`` 與最短 user 訊息，需該
provider 的有效憑證。

環境變數：

- ``LIVE_CONCURRENCY_N``：平行任務數（預設 5）
- ``DEFAULT_PROVIDER``：與 ``backend.config`` 一致（claude / openai / gemini / monica / deepseek）
- ``LIVE_CONCURRENCY_HOLD_S``：僅 dry-run，每個 stub 在 slot 內 sleep 秒數（預設 0.2）
- ``LLM_MAX_CONCURRENT``：與正式環境相同；0 表示不限流（所有任務可能同時進行）

用法範例：

.. code-block:: doscon

    REM Dry run（不需 Monica / API key）:
    backend\\.venv\\Scripts\\python.exe backend/tools/live_llm_concurrency_verify.py --dry-run

    REM Live（需 provider 憑證）:
    LLM_MAX_CONCURRENT=2 backend\\.venv\\Scripts\\python.exe backend/tools/live_llm_concurrency_verify.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import os
import sys
import time
import types
from pathlib import Path

# Repo root（wittgenstein-learning/），與 llm_concurrency_stats.py 相同
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import DEFAULT_PROVIDER, LLM_MAX_CONCURRENT
from backend.llm.base_provider import BaseLLMProvider, LLMMessage, LLMResponse, MessageRole
from backend.llm.provider_factory import create_provider

# 對應每個並行 asyncio 任務的 task id（在已取得 llm_slot 後的 `_do_chat` 內讀取）
_TASK_ID_CV: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "live_llm_concurrency_verify_task_id",
    default=None,
)


def _native_do_chat_from_mro(klass: type) -> object:
    for base in klass.__mro__:
        candidate = base.__dict__.get("_do_chat")
        if candidate is not None:
            return candidate
    raise TypeError(f"No _do_chat on class MRO: {klass!r}")


def _attach_live_do_chat_timing(
    llm: BaseLLMProvider,
    recorded: list[tuple[int, float, float]],
    lock: asyncio.Lock,
) -> None:
    native = _native_do_chat_from_mro(llm.__class__)

    async def _tracing(self: BaseLLMProvider, messages, system_prompt=None):
        tid = _TASK_ID_CV.get()
        t0 = time.perf_counter()
        try:
            return await native(self, messages, system_prompt)  # type: ignore[misc]
        finally:
            t1 = time.perf_counter()
            if tid is not None:
                async with lock:
                    recorded.append((tid, t0, t1))

    llm._do_chat = types.MethodType(_tracing, llm)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


def _peak_concurrent(intervals: list[tuple[float, float]]) -> int:
    events: list[tuple[float, int]] = []
    for s, e in intervals:
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda t: (t[0], -t[1]))
    cur = 0
    best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def _count_overlapping_pairs(intervals: list[tuple[float, float]]) -> int:
    n = len(intervals)
    c = 0
    for i in range(n):
        s1, e1 = intervals[i]
        for j in range(i + 1, n):
            s2, e2 = intervals[j]
            if s1 < e2 and s2 < e1:
                c += 1
    return c


class _DryRunStubProvider(BaseLLMProvider):
    """與 test_llm_concurrency 的 _StubProvider 相同精神：slot 內只 sleep，不碰網路。"""

    def __init__(
        self,
        model: str,
        hold_s: float,
        recorded: list[tuple[int, float, float]],
        lock: asyncio.Lock,
    ) -> None:
        super().__init__(model=model)
        self._hold_s = hold_s
        self._recorded = recorded
        self._record_lock = lock

    @property
    def context_window(self) -> int:
        return 8192

    async def _do_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: str | None = None,
    ) -> LLMResponse:
        tid = _TASK_ID_CV.get()
        t0 = time.perf_counter()
        try:
            await asyncio.sleep(self._hold_s)
            return LLMResponse(
                content="ok",
                input_tokens=1,
                output_tokens=1,
                model=self.model,
                finish_reason="stop",
            )
        finally:
            t1 = time.perf_counter()
            if tid is not None:
                async with self._record_lock:
                    self._recorded.append((tid, t0, t1))

    async def _do_stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: str | None = None,
    ):
        yield ""


async def _run_one(provider: BaseLLMProvider, task_id: int) -> None:
    token = _TASK_ID_CV.set(task_id)
    try:
        await provider.chat([LLMMessage(role=MessageRole.USER, content="ping")])
    finally:
        _TASK_ID_CV.reset(token)


def _print_summary(
    *,
    n: int,
    limit: int,
    default_provider: str,
    mode: str,
    wall_s: float,
    intervals: list[tuple[int, float, float]],
) -> None:
    intervals.sort(key=lambda x: x[0])
    rel = [(s, e) for _, s, e in intervals]
    peak = _peak_concurrent(rel)
    pairs = _count_overlapping_pairs(rel)
    total_pairs = n * (n - 1) // 2 if n >= 2 else 0

    print()
    print("=== live_llm_concurrency_verify ===")
    print(f"mode={mode}  N={n}  LLM_MAX_CONCURRENT={limit}  DEFAULT_PROVIDER={default_provider!r}")
    print(f"wall_clock_s={wall_s:.3f}")
    print("per_task (perf_counter, 已取得 llm_slot 後的 `_do_chat` 區間):")
    for tid, s, e in sorted(intervals, key=lambda x: x[0]):
        print(f"  task {tid}: start={s:.6f} end={e:.6f} dur_s={e - s:.3f}")

    print()
    print("--- overlap summary ---")
    print(f"peak_concurrent_tasks={peak}")
    print(f"overlapping_pairs={pairs} / total_pairs={total_pairs}")
    if limit > 0:
        batches = (n + limit - 1) // limit
        print(
            f"sequential_batch_estimate_ceil(N/limit)={batches}  "
            f"(若每任務耗時相近，總牆鐘時間通常 ≥ 單任務時間 × 此值)"
        )
        if peak > limit:
            print(
                f"WARNING: peak_concurrent ({peak}) > LLM_MAX_CONCURRENT ({limit}); "
                "若非量測誤差請檢查限流設定。"
            )
    else:
        print("LLM_MAX_CONCURRENT=0 → 無全域限流，peak 可能等於 N。")


async def _amain(args: argparse.Namespace) -> int:
    n = _env_int("LIVE_CONCURRENCY_N", 5)
    if n < 1:
        print("LIVE_CONCURRENCY_N must be >= 1", file=sys.stderr)
        return 2

    lock = asyncio.Lock()
    recorded: list[tuple[int, float, float]] = []

    if args.dry_run:
        hold = _env_float("LIVE_CONCURRENCY_HOLD_S", 0.2)
        llm = _DryRunStubProvider(
            model="dry-run-stub",
            hold_s=hold,
            recorded=recorded,
            lock=lock,
        )
        mode = "dry-run"
    else:
        llm = create_provider(DEFAULT_PROVIDER)
        _attach_live_do_chat_timing(llm, recorded, lock)
        mode = "live"

    t0_wall = time.perf_counter()
    await asyncio.gather(*[_run_one(llm, i) for i in range(n)])
    wall_s = time.perf_counter() - t0_wall

    _print_summary(
        n=n,
        limit=LLM_MAX_CONCURRENT,
        default_provider=DEFAULT_PROVIDER,
        mode=mode,
        wall_s=wall_s,
        intervals=recorded,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify LLM global concurrency limits (live or dry-run).")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Stub provider: llm_slot + sleep only, no API.")
    g.add_argument("--live", action="store_true", help="Real create_provider(DEFAULT_PROVIDER).chat().")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
