import logging

import requests

logger = logging.getLogger(__name__)


def store_error(config, event, ai_analysis, notified, notified_at):
    payload = {
        "server_id": event.server_id,
        "timestamp": event.timestamp,
        "log_level": event.log_level,
        "error_type": event.error_type,
        "message": event.message,
        "stack_trace": event.stack_trace,
        "raw_log": event.raw_log,
        "ai_analysis": ai_analysis,
        "notified": notified,
        "notified_at": notified_at,
    }
    try:
        response = requests.post(
            f"{config.dashboard_url.rstrip('/')}/api/errors",
            json=payload,
            headers={"X-API-Key": config.dashboard_api_key},
            timeout=5,
        )
        if response.status_code >= 300:
            logger.warning(
                "dashboard rejected error event (status %s): %s",
                response.status_code,
                response.text[:500],
            )
    except Exception as e:
        logger.warning("failed to store error in dashboard: %s", e)
