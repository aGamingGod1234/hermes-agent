from types import SimpleNamespace

from cron.whatsapp_clean_delivery import patch_scheduler_module


def _fake_scheduler(targets, *, wrap_response=True):
    calls = []
    module = SimpleNamespace()

    def load_config():
        return {"cron": {"wrap_response": wrap_response}}

    def resolve_targets(job):
        return targets

    def original_deliver(job, content, adapters=None, loop=None):
        cfg = module.load_config()
        should_wrap = cfg.get("cron", {}).get("wrap_response", True)
        delivered = content
        if should_wrap:
            delivered = (
                f"Cronjob Response: {job.get('name', job.get('id'))}\n"
                f"(job_id: {job.get('id')})\n"
                "-------------\n\n"
                f"{content}\n\n"
                "To stop or manage this job, send me a new message."
            )
        calls.append(
            {
                "job": job,
                "content": delivered,
                "wrap": should_wrap,
                "adapters": adapters,
                "loop": loop,
            }
        )
        return None

    module.load_config = load_config
    module._resolve_delivery_targets = resolve_targets
    module._deliver_result = original_deliver
    return module, calls


def test_whatsapp_delivery_is_clean_assistant_content():
    module, calls = _fake_scheduler([
        {"platform": "whatsapp", "chat_id": "home"},
    ])

    assert patch_scheduler_module(module) is True
    error = module._deliver_result(
        {"id": "abc123", "name": "Jake urgent triage", "deliver": "whatsapp"},
        "Jake urgent triage alert\n\nSpecific findings...",
    )

    assert error is None
    assert len(calls) == 1
    assert calls[0]["wrap"] is False
    assert calls[0]["content"] == "Jake urgent triage alert\n\nSpecific findings..."
    assert "Cronjob Response" not in calls[0]["content"]
    assert "job_id" not in calls[0]["content"]
    assert calls[0]["job"]["deliver"] == "origin"
    assert calls[0]["job"]["origin"]["platform"] == "whatsapp"
    assert calls[0]["job"]["origin"]["chat_id"] == "home"


def test_non_whatsapp_delivery_keeps_existing_cron_wrapper():
    module, calls = _fake_scheduler([
        {"platform": "discord", "chat_id": "home"},
    ])

    assert patch_scheduler_module(module) is True
    error = module._deliver_result(
        {"id": "abc123", "name": "Jake urgent triage", "deliver": "discord"},
        "Jake urgent triage alert",
    )

    assert error is None
    assert len(calls) == 1
    assert calls[0]["wrap"] is True
    assert calls[0]["content"].startswith("Cronjob Response: Jake urgent triage")


def test_mixed_targets_clean_whatsapp_only():
    module, calls = _fake_scheduler([
        {"platform": "whatsapp", "chat_id": "wa-home"},
        {"platform": "discord", "chat_id": "discord-home"},
    ])

    assert patch_scheduler_module(module) is True
    error = module._deliver_result(
        {"id": "abc123", "name": "Jake urgent triage", "deliver": "whatsapp,discord"},
        "Jake urgent triage alert",
    )

    assert error is None
    assert len(calls) == 2
    assert calls[0]["job"]["origin"]["platform"] == "whatsapp"
    assert calls[0]["wrap"] is False
    assert calls[0]["content"] == "Jake urgent triage alert"
    assert calls[1]["job"]["origin"]["platform"] == "discord"
    assert calls[1]["wrap"] is True
    assert calls[1]["content"].startswith("Cronjob Response: Jake urgent triage")


def test_whatsapp_failure_alert_uses_scheduled_task_wording():
    module, calls = _fake_scheduler([
        {"platform": "whatsapp", "chat_id": "home"},
    ])

    assert patch_scheduler_module(module) is True
    error = module._deliver_result(
        {"id": "abc123", "name": "Jake urgent triage", "deliver": "whatsapp"},
        "⚠️ Cron job 'Jake urgent triage' failed:\nTimeoutError",
    )

    assert error is None
    assert calls[0]["wrap"] is False
    assert calls[0]["content"].startswith("⚠️ Scheduled task 'Jake urgent triage' failed:")
    assert "Cron job" not in calls[0]["content"]


def test_patch_is_idempotent():
    module, _calls = _fake_scheduler([
        {"platform": "whatsapp", "chat_id": "home"},
    ])

    assert patch_scheduler_module(module) is True
    assert patch_scheduler_module(module) is False
