from fastapi import APIRouter, Depends, Request

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates

errors_router = APIRouter()


@errors_router.get("/errors")
def list_errors_page(
    request: Request,
    server_id: str = None, date_from: str = None, date_to: str = None, error_type: str = None,
    page: int = 1,
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    page_size = 50
    offset = (page - 1) * page_size
    errors = db.query_errors(
        config.db_path,
        server_id=server_id or None,
        date_from=date_from or None,
        date_to=date_to or None,
        error_type=error_type or None,
        limit=page_size + 1,
        offset=offset,
    )
    has_next = len(errors) > page_size
    if has_next:
        errors = errors[:page_size]

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
            "page": page,
            "has_next": has_next,
            "has_prev": page > 1,
        },
    )
