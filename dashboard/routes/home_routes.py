from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates

home_router = APIRouter()


@home_router.get("/")
def home(request: Request, user: dict = Depends(require_session)):
    config = request.app.state.config
    now = datetime.now(timezone.utc).astimezone()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    week_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    daily_counts = db.error_counts_by_day(config.db_path, week_start)
    counts_by_day = {row["day"]: row["count"] for row in daily_counts}
    trend_labels = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    trend_counts = [counts_by_day.get(day, 0) for day in trend_labels]

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "server_count": db.count_servers(config.db_path),
            "errors_today": db.count_errors_since(config.db_path, today_start),
            "notified_today": db.count_notified_since(config.db_path, today_start),
            "recent": db.recent_errors(config.db_path, limit=5),
            "trend_labels": trend_labels,
            "trend_counts": trend_counts,
        },
    )
