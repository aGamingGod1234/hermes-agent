"""Clean WhatsApp cron deliveries so scheduled output reads like a normal Hermes reply.

The stock cron delivery wrapper is useful on desktop/chat platforms, but on
WhatsApp it makes recurring status reports look like a separate bot subsystem:
"Cronjob Response", raw job IDs, separators, and management footers. Lucas wants
those reports to land as normal assistant messages while preserving the existing
cron engine and delivery routing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


_PATCH_FLAG = "_hermes_whatsapp_clean_cron_delivery_patched"
_ORIGINAL_DELIVER_ATTR = "_hermes_whatsapp_clean_cron_delivery_original"


def _is_whatsapp_target(target: dict[str, Any]) -> bool:
    return str(target.get("platform", "")).strip().lower() == "whatsapp"


def _job_for_single_target(job: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Clone a cron job so the stock deliverer resolves exactly one target."""
    cloned = dict(job)
    origin = dict(job.get("origin") or {}) if isinstance(job.get("origin"), dict) else {}
    origin.update(
        {
            "platform": str(target.get("platform", "")),
            "chat_id": str(target.get("chat_id", "")),
        }
    )
    thread_id = target.get("thread_id")
    if thread_id is None:
        origin.pop("thread_id", None)
    else:
        origin["thread_id"] = thread_id
    cloned["origin"] = origin
    cloned["deliver"] = "origin"
    return cloned


def _humanize_cron_failure(content: str) -> str:
    """Avoid visible 'cron job' wording in WhatsApp-facing failure alerts."""
    replacements = (
        ("⚠️ Cron job ", "⚠️ Scheduled task "),
        ("⚠ Cron job ", "⚠ Scheduled task "),
        ("Cron job ", "Scheduled task "),
        ("cron job ", "scheduled task "),
    )
    for old, new in replacements:
        if content.startswith(old):
            return new + content[len(old) :]
    return content


def _call_original_clean(
    scheduler_module: Any,
    original_deliver: Callable[..., Any],
    job: dict[str, Any],
    content: str,
    *,
    adapters: Any = None,
    loop: Any = None,
) -> str | None:
    """Call the stock deliverer with cron.wrap_response forced off.

    scheduler._deliver_result reads scheduler.load_config() to decide whether to
    wrap output. Temporarily overriding that function keeps the full upstream
    delivery implementation (media extraction, live adapter preference, fallback
    path, error handling) while removing the WhatsApp-only wrapper.
    """
    original_load_config = getattr(scheduler_module, "load_config", None)

    def clean_load_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {}
        if callable(original_load_config):
            try:
                loaded = original_load_config(*args, **kwargs)
                if isinstance(loaded, dict):
                    cfg = deepcopy(loaded)
            except Exception:
                cfg = {}
        cron_cfg = cfg.get("cron")
        if not isinstance(cron_cfg, dict):
            cron_cfg = {}
        else:
            cron_cfg = dict(cron_cfg)
        cron_cfg["wrap_response"] = False
        cfg["cron"] = cron_cfg
        return cfg

    if callable(original_load_config):
        setattr(scheduler_module, "load_config", clean_load_config)
    try:
        return original_deliver(
            job,
            _humanize_cron_failure(str(content or "")),
            adapters=adapters,
            loop=loop,
        )
    finally:
        if callable(original_load_config):
            setattr(scheduler_module, "load_config", original_load_config)


def _deliver_result_clean_for_whatsapp(
    scheduler_module: Any,
    original_deliver: Callable[..., Any],
    job: dict[str, Any],
    content: str,
    *,
    adapters: Any = None,
    loop: Any = None,
) -> str | None:
    try:
        targets = list(scheduler_module._resolve_delivery_targets(job))
    except Exception:
        return original_deliver(job, content, adapters=adapters, loop=loop)

    whatsapp_targets = [target for target in targets if _is_whatsapp_target(target)]
    if not whatsapp_targets:
        return original_deliver(job, content, adapters=adapters, loop=loop)

    # Single-target WhatsApp is the common path: deliver the clean assistant
    # content exactly once, without the stock Cronjob Response header/footer.
    if len(targets) == 1:
        return _call_original_clean(
            scheduler_module,
            original_deliver,
            _job_for_single_target(job, whatsapp_targets[0]),
            content,
            adapters=adapters,
            loop=loop,
        )

    # Mixed destinations keep wrappers on non-WhatsApp platforms while WhatsApp
    # receives the clean version. Each target is isolated through deliver=origin.
    errors: list[str] = []
    for target in targets:
        target_job = _job_for_single_target(job, target)
        if _is_whatsapp_target(target):
            error = _call_original_clean(
                scheduler_module,
                original_deliver,
                target_job,
                content,
                adapters=adapters,
                loop=loop,
            )
        else:
            error = original_deliver(target_job, content, adapters=adapters, loop=loop)
        if error:
            errors.append(str(error))
    return "; ".join(errors) if errors else None


def patch_scheduler_module(scheduler_module: Any) -> bool:
    """Patch cron.scheduler._deliver_result once."""
    if getattr(scheduler_module, _PATCH_FLAG, False):
        return False
    original_deliver = getattr(scheduler_module, "_deliver_result", None)
    if not callable(original_deliver):
        return False

    def patched_deliver_result(job, content, adapters=None, loop=None):
        return _deliver_result_clean_for_whatsapp(
            scheduler_module,
            original_deliver,
            job,
            content,
            adapters=adapters,
            loop=loop,
        )

    setattr(scheduler_module, _ORIGINAL_DELIVER_ATTR, original_deliver)
    setattr(scheduler_module, "_deliver_result", patched_deliver_result)
    setattr(scheduler_module, _PATCH_FLAG, True)
    return True


def install_whatsapp_clean_cron_delivery_patch() -> bool:
    """Install the WhatsApp clean-delivery patch on cron.scheduler."""
    try:
        from cron import scheduler
    except Exception:
        return False
    return patch_scheduler_module(scheduler)
