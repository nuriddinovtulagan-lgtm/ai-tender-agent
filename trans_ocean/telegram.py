"""Minimal Telegram Bot API helpers for the coordinator (messages, buttons, webhook)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 20.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx  # imported here so the package loads without network deps in tests

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(API.format(token=self.token, method=method), json=payload)
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method}: {data.get('description', response.status_code)}")
            return data.get("result") or {}

    async def send(self, text: str, buttons: Optional[List[List[Dict[str, str]]]] = None,
                   chat_id: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id or self.chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return await self.call("sendMessage", payload)

    async def send_document(self, filename: str, content: bytes, caption: str = "",
                            chat_id: Optional[str] = None) -> Dict[str, Any]:
        import httpx

        data = {"chat_id": str(chat_id or self.chat_id)}
        if caption:
            data.update({"caption": caption[:1024], "parse_mode": "HTML"})
        async with httpx.AsyncClient(timeout=max(self.timeout, 60)) as client:
            response = await client.post(
                API.format(token=self.token, method="sendDocument"), data=data,
                files={"document": (filename, content,
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError(f"Telegram sendDocument: {result.get('description', response.status_code)}")
            return result.get("result") or {}

    async def edit(self, chat_id: Any, message_id: Any, text: str) -> None:
        await self.call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text[:4096],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    async def set_webhook(self, url: str, secret: str) -> Dict[str, Any]:
        return await self.call("setWebhook", {
            "url": url, "secret_token": secret,
            "allowed_updates": ["callback_query", "message"],
            "drop_pending_updates": False,
        })

    async def webhook_info(self) -> Dict[str, Any]:
        return await self.call("getWebhookInfo", {})


def approval_buttons(task_id: str) -> List[List[Dict[str, str]]]:
    return [[
        {"text": "✅ Утвердить", "callback_data": f"t:{task_id}:ok"},
        {"text": "❌ Отклонить", "callback_data": f"t:{task_id}:no"},
    ]]


def parse_callback(data: str) -> Optional[Dict[str, str]]:
    """'t:<task id>:ok|no' -> {'task_id', 'decision'}; anything else -> None."""
    parts = (data or "").split(":")
    if len(parts) == 3 and parts[0] == "t" and parts[1] and parts[2] in {"ok", "no"}:
        return {"task_id": parts[1], "decision": parts[2]}
    return None
