"""WhatsApp tool-progress coalescing for verbose edited rollover output.

This module patches the WhatsApp adapter send path so tool/progress updates are
kept in one edited message per chat until the configured safe length is reached,
then a fresh rollover message is opened. Normal assistant/final messages clear
the active progress bubble so future tool progress stays chronologically below
user-visible content.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Optional


_PATCH_FLAG = "_hermes_whatsapp_progress_rollover_patched"
_ORIGINAL_SEND_ATTR = "_hermes_whatsapp_progress_rollover_original_send"
_STATE_ATTR = "_hermes_whatsapp_progress_rollover_state"

_OPEN_THINK_TAGS = (
    "<REASONING_SCRATCHPAD>", "<think>", "<reasoning>",
    "<THINKING>", "<thinking>", "<thought>",
)
_THINK_BLOCK_RE = re.compile(
    r"<(?:REASONING_SCRATCHPAD|think|reasoning|THINKING|thinking|thought)>.*?</(?:REASONING_SCRATCHPAD|think|reasoning|THINKING|thinking|thought)>",
    re.DOTALL,
)

_PROGRESS_PREFIXES = (
    "⚙️ ", "⚙ ", "🔧 ", "🛠️ ", "🛠 ", "📄 ", "🔍 ", "🌐 ",
    "🖱️ ", "🖱 ", "⌨️ ", "⌨ ", "🧠 ", "✅ ", "❌ ", "⚠️ ",
    "⚠ ", "⏳ ",
)
_PROGRESS_SUBSTRINGS = (
    "mcp_", "tool call", "tool_call", "delegate", "Iteration budget exhausted",
    "Still working", "receiving stream response",
)


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _safe_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def looks_like_tool_progress(content: str) -> bool:
    """Return True when content is a gateway/tool progress update."""
    if not content or not content.strip():
        return False
    stripped = content.lstrip()
    if stripped.startswith(_PROGRESS_PREFIXES):
        return True
    lower = stripped.lower()
    return any(token.lower() in lower for token in _PROGRESS_SUBSTRINGS)


def sanitize_progress_content(content: str) -> str:
    """Hide reasoning/internal planning markers from visible progress text."""
    if not content:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", content)

    lowered = cleaned.lower()
    cut_at: Optional[int] = None
    for tag in _OPEN_THINK_TAGS:
        idx = lowered.find(tag.lower())
        if idx != -1 and (cut_at is None or idx < cut_at):
            cut_at = idx
    if cut_at is not None:
        cleaned = cleaned[:cut_at]

    lines = []
    skip_block = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip().lower()
        if line in {"refined prompt", "refined spec", "internal plan", "analysis"}:
            skip_block = True
            continue
        if skip_block and not line:
            skip_block = False
            continue
        if skip_block:
            continue
        lines.append(raw_line.rstrip())
    return "\n".join(lines).strip()


def _progress_limit(adapter: Any) -> int:
    hard_max = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4096) or 4096)
    try:
        outgoing_limit = int(adapter._outgoing_chunk_limit())
        hard_max = min(hard_max, outgoing_limit)
    except Exception:
        pass
    return _safe_int_env(
        "HERMES_WHATSAPP_PROGRESS_ROLLOVER_CHARS",
        default=min(3500, max(1000, hard_max - 300)),
        minimum=500,
        maximum=max(500, hard_max - 100),
    )


def _get_state(adapter: Any) -> Dict[str, Dict[str, Any]]:
    state = getattr(adapter, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {}
        setattr(adapter, _STATE_ATTR, state)
    return state


def _clear_state(adapter: Any, chat_id: str) -> None:
    state = getattr(adapter, _STATE_ATTR, None)
    if isinstance(state, dict):
        state.pop(str(chat_id), None)


async def _send_with_rollover(
    adapter: Any,
    original_send: Callable[..., Any],
    *,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    metadata = metadata or None

    if not _truthy_env("HERMES_WHATSAPP_PROGRESS_ROLLOVER", True):
        return await original_send(adapter, chat_id, content, reply_to=reply_to, metadata=metadata)

    if not looks_like_tool_progress(content):
        _clear_state(adapter, chat_id)
        return await original_send(adapter, chat_id, content, reply_to=reply_to, metadata=metadata)

    entry = sanitize_progress_content(content)
    if not entry:
        return await original_send(adapter, chat_id, content, reply_to=reply_to, metadata=metadata)

    limit = _progress_limit(adapter)
    state_by_chat = _get_state(adapter)
    key = str(chat_id)
    current = state_by_chat.get(key) or {}
    current_text = str(current.get("text") or "")
    current_message_id = current.get("message_id")
    separator = "\n\n"
    candidate = f"{current_text}{separator}{entry}" if current_text else entry

    if len(entry) > limit:
        result = await original_send(adapter, chat_id, entry, reply_to=reply_to, metadata=metadata)
        if getattr(result, "success", False) and getattr(result, "message_id", None):
            state_by_chat[key] = {"message_id": str(result.message_id), "text": entry[-limit:]}
        return result

    if not current_message_id or len(candidate) > limit:
        result = await original_send(adapter, chat_id, entry, reply_to=reply_to, metadata=metadata)
        if getattr(result, "success", False) and getattr(result, "message_id", None):
            state_by_chat[key] = {"message_id": str(result.message_id), "text": entry}
        return result

    edit_message = getattr(adapter, "edit_message", None)
    if edit_message is None:
        return await original_send(adapter, chat_id, entry, reply_to=reply_to, metadata=metadata)

    edit_result = await edit_message(chat_id=chat_id, message_id=str(current_message_id), content=candidate)
    if getattr(edit_result, "success", False):
        state_by_chat[key] = {"message_id": str(current_message_id), "text": candidate}
        if not getattr(edit_result, "message_id", None):
            try:
                edit_result.message_id = str(current_message_id)
            except Exception:
                pass
        return edit_result

    result = await original_send(adapter, chat_id, entry, reply_to=reply_to, metadata=metadata)
    if getattr(result, "success", False) and getattr(result, "message_id", None):
        state_by_chat[key] = {"message_id": str(result.message_id), "text": entry}
    return result


def patch_adapter_class(adapter_cls: type) -> bool:
    """Patch an adapter class once. Returns True when patch installed."""
    if getattr(adapter_cls, _PATCH_FLAG, False):
        return False
    original_send = getattr(adapter_cls, "send", None)
    if original_send is None:
        return False

    async def patched_send(self, chat_id: str, content: str, reply_to=None, metadata=None):
        return await _send_with_rollover(
            self,
            original_send,
            chat_id=chat_id,
            content=content,
            reply_to=reply_to,
            metadata=metadata,
        )

    setattr(adapter_cls, _ORIGINAL_SEND_ATTR, original_send)
    setattr(adapter_cls, "send", patched_send)
    setattr(adapter_cls, _PATCH_FLAG, True)
    return True


def install_whatsapp_progress_rollover_patch() -> bool:
    """Install the patch on gateway.platforms.whatsapp.WhatsAppAdapter."""
    try:
        from gateway.platforms.whatsapp import WhatsAppAdapter
    except Exception:
        return False
    return patch_adapter_class(WhatsAppAdapter)
