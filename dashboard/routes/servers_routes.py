from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates
from dashboard.validation import ServerValidationError, validate_server_fields

servers_router = APIRouter()


@servers_router.get("/servers")
def list_servers_page(request: Request, user: dict = Depends(require_session)):
    config = request.app.state.config
    return templates.TemplateResponse(
        request,
        "servers.html",
        {"user": user, "servers": db.list_servers(config.db_path), "error": None},
    )


@servers_router.post("/servers")
def add_server(
    request: Request,
    server_id: str = Form(...), host: str = Form(...), port: int = Form(...),
    username: str = Form(...), ssh_key_path: str = Form(...), log_path: str = Form(...),
    format: str = Form(...), custom_pattern: str = Form(""),
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    pattern = custom_pattern or None
    try:
        validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    except ServerValidationError as e:
        return templates.TemplateResponse(
            request,
            "servers.html",
            {"user": user, "servers": db.list_servers(config.db_path), "error": str(e)},
        )

    db.insert_server(config.db_path, server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    return RedirectResponse("/servers", status_code=303)


@servers_router.get("/servers/{server_id}/edit")
def edit_server_page(request: Request, server_id: str, user: dict = Depends(require_session)):
    config = request.app.state.config
    server = db.get_server(config.db_path, server_id)
    return templates.TemplateResponse(
        request, "server_edit.html", {"user": user, "server": server, "error": None}
    )


@servers_router.post("/servers/{server_id}/edit")
def edit_server(
    request: Request, server_id: str,
    host: str = Form(...), port: int = Form(...), username: str = Form(...),
    ssh_key_path: str = Form(...), log_path: str = Form(...),
    format: str = Form(...), custom_pattern: str = Form(""),
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    pattern = custom_pattern or None
    try:
        validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    except ServerValidationError as e:
        server = {
            "server_id": server_id, "host": host, "port": port, "username": username,
            "ssh_key_path": ssh_key_path, "log_path": log_path, "format": format, "custom_pattern": pattern,
        }
        return templates.TemplateResponse(
            request, "server_edit.html", {"user": user, "server": server, "error": str(e)}
        )

    db.update_server(config.db_path, server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    return RedirectResponse("/servers", status_code=303)


@servers_router.post("/servers/{server_id}/delete")
def delete_server(request: Request, server_id: str, user: dict = Depends(require_session)):
    config = request.app.state.config
    db.delete_server(config.db_path, server_id)
    return RedirectResponse("/servers", status_code=303)
