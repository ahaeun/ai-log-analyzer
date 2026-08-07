from fastapi import APIRouter, Depends, Request

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates

errors_router = APIRouter()


@errors_router.get("/errors")
def list_errors_page(
    request: Request,
    server_id: str = None, date_from: str = None, date_to: str = None, error_type: str = None,
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    errors = db.query_errors(
        config.db_path,
        server_id=server_id or None,
        date_from=date_from or None,
        date_to=date_to or None,
        error_type=error_type or None,
    )
    return templates.TemplateResponse(
        request,
        "errors.html",
        {
            "user": user,
            "errors": errors,
            "servers": db.list_servers(config.db_path),
            "filters": {
                "server_id": server_id, "date_from": date_from,
                "date_to": date_to, "error_type": error_type,
            },
        },
    )
