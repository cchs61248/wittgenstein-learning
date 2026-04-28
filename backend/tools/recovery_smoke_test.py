import argparse
import asyncio
import json
from typing import Any

import websockets


async def collect_resume_messages(
    ws_url: str,
    session_id: str,
    provider: str,
    model: str | None,
    timeout_sec: float,
) -> dict[str, Any]:
    found: dict[str, Any] = {}
    async with websockets.connect(ws_url) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "resume_session",
                    "payload": {
                        "session_id": session_id,
                        "provider": provider,
                        "model": model,
                    },
                },
                ensure_ascii=False,
            )
        )

        end_at = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < end_at:
            remaining = max(0.1, end_at - asyncio.get_event_loop().time())
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            msg_type = msg.get("type")
            if msg_type in ("session_started", "session_snapshot", "resume_state"):
                found[msg_type] = msg.get("payload", {})
            if len(found) == 3:
                break
    return found


def assert_resume_contract(found: dict[str, Any]) -> None:
    required = ("session_started", "session_snapshot", "resume_state")
    missing = [k for k in required if k not in found]
    if missing:
        raise AssertionError(f"Missing resume messages: {missing}")

    snapshot = found["session_snapshot"]
    if "stage_explanations" not in snapshot or "stage_qa_histories" not in snapshot:
        raise AssertionError("session_snapshot payload missing required fields")

    resume_state = found["resume_state"]
    if "current_question" not in resume_state or "last_feedback" not in resume_state:
        raise AssertionError("resume_state payload missing required fields")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Disconnect recovery smoke test skeleton.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True, help="JWT token")
    parser.add_argument("--session-id", required=True, help="Active session_id to resume")
    parser.add_argument("--provider", default="claude", help="claude|openai|gemini|monica")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    args = parser.parse_args()

    ws_base = args.base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_base}/ws/{args.session_id}?token={args.token}"

    print("[recovery-smoke] connecting:", ws_url.split("?")[0] + "?token=***")
    found = await collect_resume_messages(
        ws_url=ws_url,
        session_id=args.session_id,
        provider=args.provider,
        model=args.model,
        timeout_sec=args.timeout_sec,
    )
    assert_resume_contract(found)

    print("[recovery-smoke] PASS")
    print("[recovery-smoke] received:", ", ".join(sorted(found.keys())))
    print(
        "[recovery-smoke] note: this is a skeleton; extend with edge timing cases "
        "(submit in-flight, feedback-received, stage-transition)."
    )


if __name__ == "__main__":
    asyncio.run(main())
