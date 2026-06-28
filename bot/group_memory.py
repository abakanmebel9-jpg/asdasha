"""
Group Conversation Memory — помогает Даше понимать, с кем она общается в
группах и супергруппах, и активно вступать в диалоги.

Хранит лёгкий in-memory rolling-буфер недавних сообщений каждого чата:
  - кто что сказал (имя, username, фрагмент текста)
  - когда (timestamp)

Это даёт Даше возможность:
  - обращаться к собеседнику по имени
  - ссылаться на только что обсуждавшееся («как Мария тут заметила…»)
  - узнавать возвращающихся собеседников
  - естественно вплетаться в живой разговор, а не отвечать «в вакууме»

Память НЕ персистентна (in-memory) — это намеренно: достаточно «короткой
памяти» о текущем разговоре. После рестарта бота Даша начинает свежей, что
вполне естественно для живого общения.
"""

import logging
import re
import time
import threading
from collections import OrderedDict, defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("dasha.group_memory")


# ════════════════════════════════════════════════════════════════════════════
# Конфигурация (значения по умолчанию — разумные, можно тюнить)
# ════════════════════════════════════════════════════════════════════════════

_MAX_PER_CHAT = 20          # сколько недавних сообщений хранить на чат
_TTL_SECONDS = 3600         # сообщения старше 1 часа выпадают из контекста
_SNIPPET_LEN = 160          # длина фрагмента текста в контексте
_MAX_CONTEXT_ITEMS = 6      # сколько недавних реплик передаём в промпт
_CLEANUP_EVERY = 100        # периодически чистим устаревшие/переполненные буферы


def _short_name(first_name: str, username: str) -> str:
    """Красивое отображение имени собеседника: «Иван» или «Иван (@ivan)»."""
    fn = (first_name or "").strip()
    un = (username or "").strip().lstrip("@")
    if fn and un:
        return f"{fn} (@{un})"
    if fn:
        return fn
    if un:
        return f"@{un}"
    return "участник"


def _snippet(text: str, maxlen: int = _SNIPPET_LEN) -> str:
    """Сократить текст до фрагмента, не разрывая слово."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= maxlen:
        return text
    cut = text[:maxlen]
    last_space = cut.rfind(" ")
    if last_space > maxlen - 40:
        cut = cut[:last_space]
    return cut.rstrip(" ,.;:!?—-") + "…"


class GroupMemory:
    """In-memory rolling-контекст недавних сообщений по каждому чату.

    Потокобезопасный (lock). Один процесс — один синглтон `group_memory`.
    """

    def __init__(
        self,
        max_per_chat: int = _MAX_PER_CHAT,
        ttl_seconds: int = _TTL_SECONDS,
    ):
        self._max_per_chat = max_per_chat
        self._ttl = ttl_seconds
        # chat_id -> OrderedDict[entry_id, entry]
        # entry: {"uid": int, "name": str, "text": str, "ts": float}
        self._chats: "OrderedDict[int, OrderedDict[int, dict]]" = OrderedDict()
        self._counter = 0  # монотонный id записи (ключ в OrderedDict)
        self._lock = threading.Lock()
        self._since_cleanup = 0

    # ──────────────────────────────────────────────────────────────────────
    # Запись
    # ──────────────────────────────────────────────────────────────────────

    def remember(
        self,
        chat_id: int,
        user_id: int,
        user_name: str,
        username: str,
        text: str,
        is_dasha: bool = False,
    ) -> None:
        """Запомнить сообщение в чате.

        is_dasha=True — это сообщение самой Даши (чтобы она помнила, что
        только что сказала, и могла продолжать диалог естественно).
        """
        text = (text or "").strip()
        if not text:
            return
        # Не запоминаем служебные команды
        if text.startswith("/") and len(text) < 24:
            return

        entry = {
            "uid": user_id,
            "name": _short_name(user_name, username),
            "text": _snippet(text),
            "ts": time.time(),
            "dasha": is_dasha,
        }
        with self._lock:
            self._counter += 1
            eid = self._counter
            buf = self._chats.setdefault(chat_id, OrderedDict())
            buf[eid] = entry
            # Обрезаем буфер до max_per_chat (самые свежие остаются)
            while len(buf) > self._max_per_chat:
                buf.popitem(last=False)
            self._since_cleanup += 1
            if self._since_cleanup >= _CLEANUP_EVERY:
                self._since_cleanup = 0
                self._cleanup_locked()

    def remember_dasha(self, chat_id: int, text: str) -> None:
        """Запомнить собственный ответ Даши в чате (для连贯ности диалога)."""
        self.remember(
            chat_id=chat_id,
            user_id=0,
            user_name="Даша",
            username="asdasha_bot",
            text=text,
            is_dasha=True,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Чтение
    # ──────────────────────────────────────────────────────────────────────

    def _recent_entries(self, chat_id: int, limit: int = _MAX_CONTEXT_ITEMS) -> List[dict]:
        """Вернуть последние `limit` валидных (не устаревших) записей чата."""
        now = time.time()
        with self._lock:
            buf = self._chats.get(chat_id)
            if not buf:
                return []
            # Берём с конца (самые свежие), фильтруем по TTL
            out = []
            for entry in reversed(buf.values()):
                if now - entry["ts"] > self._ttl:
                    break
                out.append(entry)
                if len(out) >= limit:
                    break
            out.reverse()
            return out

    def build_context_for_prompt(
        self,
        chat_id: int,
        current_user_id: int,
        current_user_name: str,
    ) -> str:
        """Построить фрагмент контекста для системного промпта.

        Описывает, кто пишет сейчас и что недавно обсуждали в этом чате.
        """
        entries = self._recent_entries(chat_id, limit=_MAX_CONTEXT_ITEMS)
        # Исключаем самый последний реплик, если это сам текущий юзер
        # (его сообщение и так попадёт в user_message — не дублируем).
        # Но оставляем реплики Дашы и других людей.

        lines = []
        had_any = False
        for e in entries:
            had_any = True
            speaker = e["name"]
            # Не повторяем текущее сообщение пользователя
            if e["uid"] == current_user_id and not e["dasha"]:
                # Текущий юзер — его последнюю реплику пропускаем (она в user_msg)
                continue
            prefix = "Даша" if e["dasha"] else speaker
            lines.append(f"— {prefix}: «{e['text']}»")

        current_name = _short_name(current_user_name, "")

        if not lines:
            if had_any:
                return (
                    f"\n\n[Контекст чата] Сейчас пишет {current_name}. "
                    "В чате недавно была беседа, но детали уже неактуальны."
                )
            return (
                f"\n\n[Контекст чата] Сейчас пишет {current_name}. "
                "Это первое сообщение в этом чате за последнее время — "
                "поприветствуй и вступи в разговор живо."
            )

        context_text = "\n".join(lines)
        return (
            f"\n\n[Контекст чата] Сейчас пишет {current_name}. "
            "Недавняя беседа в этом чате (последние реплики, NEW→OLD): "
            f"можешь продолжить диалог естественно, ссылаться на сказанное, "
            f"обратиться к собеседнику или другому участнику по имени.\n"
            f"{context_text}"
        )

    def user_profile(self, chat_id: int, user_id: int) -> Dict:
        """Профиль участника в чате: сколько реплик, последняя тема, имя."""
        now = time.time()
        count = 0
        last_text = ""
        name = ""
        with self._lock:
            buf = self._chats.get(chat_id)
            if not buf:
                return {"count": 0, "last_text": "", "name": ""}
            for entry in reversed(buf.values()):
                if entry["uid"] != user_id:
                    continue
                if now - entry["ts"] > self._ttl:
                    break
                count += 1
                if not last_text:
                    last_text = entry["text"]
                if not name:
                    name = entry["name"]
        return {"count": count, "last_text": last_text, "name": name}

    def forget_chat(self, chat_id: int) -> None:
        """Полностью очистить память чата (например, по команде /clear в группе)."""
        with self._lock:
            self._chats.pop(chat_id, None)

    # ──────────────────────────────────────────────────────────────────────
    # Внутреннее
    # ──────────────────────────────────────────────────────────────────────

    def _cleanup_locked(self) -> None:
        """Удалить устаревшие записи (вызывать под lock)."""
        now = time.time()
        empty_chats = []
        for chat_id, buf in self._chats.items():
            stale_keys = [
                eid for eid, e in buf.items()
                if now - e["ts"] > self._ttl
            ]
            for k in stale_keys:
                buf.pop(k, None)
            if not buf:
                empty_chats.append(chat_id)
        for cid in empty_chats:
            self._chats.pop(cid, None)


# ════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ════════════════════════════════════════════════════════════════════════════

group_memory = GroupMemory()
