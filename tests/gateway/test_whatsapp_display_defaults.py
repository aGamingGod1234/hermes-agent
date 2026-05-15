"""WhatsApp display defaults for operator-visible progress."""

from gateway.display_config import resolve_display_setting


def test_whatsapp_defaults_to_verbose_tool_progress_without_truncation():
    assert resolve_display_setting({}, "whatsapp", "tool_progress") == "verbose"
    assert resolve_display_setting({}, "whatsapp", "tool_preview_length") == 0


def test_whatsapp_display_overrides_still_win():
    cfg = {
        "display": {
            "platforms": {
                "whatsapp": {
                    "tool_progress": "new",
                    "tool_preview_length": 80,
                }
            }
        }
    }

    assert resolve_display_setting(cfg, "whatsapp", "tool_progress") == "new"
    assert resolve_display_setting(cfg, "whatsapp", "tool_preview_length") == 80
