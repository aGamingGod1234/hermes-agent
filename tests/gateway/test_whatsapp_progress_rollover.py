from types import SimpleNamespace

import pytest

from gateway.whatsapp_progress_rollover import (
    looks_like_tool_progress,
    patch_adapter_class,
    sanitize_progress_content,
)


def _make_adapter_class():
    class DummyWhatsAppAdapter:
        MAX_MESSAGE_LENGTH = 4096

        def __init__(self):
            self.sent = []
            self.edits = []
            self._counter = 0

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self._counter += 1
            message_id = f"msg_{self._counter}"
            self.sent.append({
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
                "message_id": message_id,
            })
            return SimpleNamespace(success=True, message_id=message_id)

        async def edit_message(self, chat_id, message_id, content, finalize=False):
            self.edits.append({
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "finalize": finalize,
            })
            return SimpleNamespace(success=True, message_id=message_id)

    patch_adapter_class(DummyWhatsAppAdapter)
    return DummyWhatsAppAdapter


def test_detects_tool_progress_prefixes():
    assert looks_like_tool_progress("⚙️ mcp_github_get_file_contents(['path'])")
    assert looks_like_tool_progress("📄 web_extract(['urls'])")
    assert looks_like_tool_progress("⏳ Still working... (1 min elapsed)")
    assert not looks_like_tool_progress("Here is the final answer.")


def test_sanitizes_reasoning_blocks():
    text = "⚙️ tool\n<think>private chain of thought</think>visible args"
    cleaned = sanitize_progress_content(text)
    assert "private chain" not in cleaned
    assert "<think>" not in cleaned
    assert "visible args" in cleaned


@pytest.mark.asyncio
async def test_progress_messages_edit_one_rolling_bubble(monkeypatch):
    monkeypatch.setenv("HERMES_WHATSAPP_PROGRESS_ROLLOVER_CHARS", "500")
    Adapter = _make_adapter_class()
    adapter = Adapter()

    first = await adapter.send("chat", "⚙️ tool_a(['x'])\n{\"x\": 1}")
    second = await adapter.send("chat", "⚙️ tool_b(['y'])\n{\"y\": 2}")

    assert first.success is True
    assert second.success is True
    assert len(adapter.sent) == 1
    assert len(adapter.edits) == 1
    assert adapter.edits[0]["message_id"] == "msg_1"
    assert "tool_a" in adapter.edits[0]["content"]
    assert "tool_b" in adapter.edits[0]["content"]


@pytest.mark.asyncio
async def test_rollover_opens_new_message_before_limit(monkeypatch):
    monkeypatch.setenv("HERMES_WHATSAPP_PROGRESS_ROLLOVER_CHARS", "80")
    Adapter = _make_adapter_class()
    adapter = Adapter()

    await adapter.send("chat", "⚙️ tool_a\n" + "a" * 45)
    await adapter.send("chat", "⚙️ tool_b\n" + "b" * 45)

    assert len(adapter.sent) == 2
    assert len(adapter.edits) == 0
    assert adapter.sent[0]["message_id"] == "msg_1"
    assert adapter.sent[1]["message_id"] == "msg_2"


@pytest.mark.asyncio
async def test_normal_message_closes_progress_bubble(monkeypatch):
    monkeypatch.setenv("HERMES_WHATSAPP_PROGRESS_ROLLOVER_CHARS", "500")
    Adapter = _make_adapter_class()
    adapter = Adapter()

    await adapter.send("chat", "⚙️ tool_a")
    await adapter.send("chat", "Final answer.")
    await adapter.send("chat", "⚙️ tool_b")

    assert len(adapter.sent) == 3
    assert len(adapter.edits) == 0
    assert adapter.sent[2]["content"] == "⚙️ tool_b"


@pytest.mark.asyncio
async def test_disable_env_uses_original_send(monkeypatch):
    monkeypatch.setenv("HERMES_WHATSAPP_PROGRESS_ROLLOVER", "false")
    Adapter = _make_adapter_class()
    adapter = Adapter()

    await adapter.send("chat", "⚙️ tool_a")
    await adapter.send("chat", "⚙️ tool_b")

    assert len(adapter.sent) == 2
    assert len(adapter.edits) == 0
