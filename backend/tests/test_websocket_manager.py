import json
import unittest

from backend.ws.connection_manager import WebSocketManager


class DummyWebSocket:
    def __init__(self) -> None:
        self.accept_called = False
        self.sent_texts: list[str] = []
        self.closed_code: int | None = None

    async def accept(self) -> None:
        self.accept_called = True

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


class TestWebSocketManagerSingleLogin(unittest.IsolatedAsyncioTestCase):
    async def test_kicks_old_connection_when_same_user_new_client_connects(self):
        manager = WebSocketManager()
        old_ws = DummyWebSocket()
        new_ws = DummyWebSocket()

        await manager.connect(
            session_id="sess_old",
            user_id="user_1",
            client_id="client_A",
            ws=old_ws,
        )
        await manager.connect(
            session_id="sess_new",
            user_id="user_1",
            client_id="client_B",
            ws=new_ws,
        )

        self.assertTrue(old_ws.sent_texts, "舊連線應收到 kicked 訊息")
        kicked_msg = json.loads(old_ws.sent_texts[0])
        self.assertEqual(kicked_msg["type"], "kicked")
        self.assertEqual(old_ws.closed_code, 4002)
        self.assertTrue(new_ws.accept_called)

    async def test_same_client_can_open_multiple_sessions_without_being_kicked(self):
        manager = WebSocketManager()
        ws1 = DummyWebSocket()
        ws2 = DummyWebSocket()

        await manager.connect(
            session_id="sess_a",
            user_id="user_1",
            client_id="client_A",
            ws=ws1,
        )
        await manager.connect(
            session_id="sess_b",
            user_id="user_1",
            client_id="client_A",
            ws=ws2,
        )

        self.assertFalse(ws1.sent_texts, "同一 client 切換教材/背景生成不應被踢")
        self.assertIsNone(ws1.closed_code)
        self.assertTrue(ws2.accept_called)


if __name__ == "__main__":
    unittest.main()
