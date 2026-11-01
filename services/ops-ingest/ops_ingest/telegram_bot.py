"""Тонкий Telegram Bot API клиент (long-poll). httpx, без доп. SDK."""
import httpx


def extract_text_messages(updates):
    out = []
    for u in updates:
        m = u.get("message") or {}
        text = m.get("text")
        chat_id = (m.get("chat") or {}).get("id")
        reply_to = (m.get("reply_to_message") or {}).get("message_id")
        if text and chat_id is not None:
            out.append({"update_id": u["update_id"], "chat_id": chat_id, "text": text,
                        "reply_to": reply_to})
    return out


class NullBot:
    """Пинги выключены: токен бота не задан."""

    def send_message(self, chat_id, text: str, parse_mode: str | None = None) -> dict:
        return {}


class TelegramBot:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id, text: str, parse_mode: str | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{self.base}/sendMessage", json=payload)
            if not r.is_success:
                raise RuntimeError(f"Telegram API {r.status_code}")
            return r.json()

    def get_updates(self, offset=None, timeout: int = 25) -> list[dict]:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        with httpx.Client(timeout=timeout + 10) as c:
            r = c.get(f"{self.base}/getUpdates", params=params)
            if not r.is_success:
                raise RuntimeError(f"Telegram API {r.status_code}")
            return r.json().get("result", [])
