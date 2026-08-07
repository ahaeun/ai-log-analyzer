from datetime import datetime, timezone

from analyzer import dashboard_client, dedup, openai_client, slack_client


def process_error(event, config):
    is_dup = dedup.is_duplicate(config.redis_url, event)
    ai_analysis = openai_client.analyze_error(event, config)

    if is_dup:
        notified = False
        notified_at = None
    else:
        slack_client.send_notification(config.slack_webhook_url, event, ai_analysis)
        notified = True
        notified_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    dashboard_client.store_error(config, event, ai_analysis, notified, notified_at)
