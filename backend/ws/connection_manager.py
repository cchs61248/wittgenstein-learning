import json
from collections import defaultdict
from typing import Any


class WebSocketManager:
    def __init__(self) -> None:
        self._sid_to_ws: dict[str, Any] = {}
        self._sid_to_user: dict[str, str] = {}
        self._sid_to_client: dict[str, str] = {}
        self._uid_client_to_sids: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._uid_to_active_client: dict[str, str] = {}

    async def connect(
        self,
        session_id: str,
        user_id: str,
        client_id: str,
        ws: Any,
    ) -> None:
        await ws.accept()

        old_active_client = self._uid_to_active_client.get(user_id)
        if old_active_client and old_active_client != client_id:
            await self._kick_client_connections(user_id=user_id, client_id=old_active_client)

        old_ws = self._sid_to_ws.get(session_id)
        if old_ws and old_ws is not ws:
            try:
                await old_ws.close(code=4002)
            except Exception:
                pass

        self._sid_to_ws[session_id] = ws
        self._sid_to_user[session_id] = user_id
        self._sid_to_client[session_id] = client_id
        self._uid_client_to_sids[user_id][client_id].add(session_id)
        self._uid_to_active_client[user_id] = client_id

    async def _kick_client_connections(self, user_id: str, client_id: str) -> None:
        session_ids = list(self._uid_client_to_sids.get(user_id, {}).get(client_id, set()))
        for sid in session_ids:
            ws = self._sid_to_ws.get(sid)
            if ws:
                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "kicked",
                                "payload": {"message": "你已在其他裝置登入，此裝置已登出。"},
                            },
                            ensure_ascii=False,
                        )
                    )
                    await ws.close(code=4002)
                except Exception:
                    pass
            self._remove_session(sid)

    async def send(self, session_id: str, message: dict) -> None:
        ws = self._sid_to_ws.get(session_id)
        if ws:
            await ws.send_text(json.dumps(message, ensure_ascii=False))

    def disconnect(self, session_id: str, ws: Any) -> None:
        current = self._sid_to_ws.get(session_id)
        if current is ws:
            self._remove_session(session_id)

    def _remove_session(self, session_id: str) -> None:
        user_id = self._sid_to_user.pop(session_id, None)
        client_id = self._sid_to_client.pop(session_id, None)
        self._sid_to_ws.pop(session_id, None)
        if not user_id or not client_id:
            return

        per_user = self._uid_client_to_sids.get(user_id)
        if not per_user:
            return
        sessions = per_user.get(client_id)
        if sessions:
            sessions.discard(session_id)
            if not sessions:
                per_user.pop(client_id, None)
        if not per_user:
            self._uid_client_to_sids.pop(user_id, None)
            self._uid_to_active_client.pop(user_id, None)
            return

        if self._uid_to_active_client.get(user_id) == client_id and client_id not in per_user:
            self._uid_to_active_client[user_id] = next(iter(per_user.keys()))

    def has_active_ws(self, session_id: str) -> bool:
        return session_id in self._sid_to_ws
