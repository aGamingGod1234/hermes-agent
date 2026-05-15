"""Hermes local startup customisations.

Python imports sitecustomize automatically when this repository is on sys.path.
Keep this file tiny and best-effort: it must never prevent Hermes from starting.
"""

try:
    from gateway.whatsapp_progress_rollover import install_whatsapp_progress_rollover_patch

    install_whatsapp_progress_rollover_patch()
except Exception:
    # Startup customisations are optional; Hermes must boot even if this fails.
    pass

try:
    from cron.whatsapp_clean_delivery import install_whatsapp_clean_cron_delivery_patch

    install_whatsapp_clean_cron_delivery_patch()
except Exception:
    # Cron delivery cleanup is optional; Hermes must boot even if this fails.
    pass
