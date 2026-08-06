from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from dashboard import db

api_router = APIRouter()


def _check_api_key(request: Request, x_api_key: Optional[str] = Header(default=None)):
    # Declared as a dependency (Depends) rather than called from inside the
    # endpoint body: FastAPI resolves dependencies before validating the
    # request body, so an invalid/missing API key is rejected with 401
    # before a malformed body could otherwise short-circuit with a 422.
    config = request.app.state.config
    if not x_api_key or x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


class ErrorIn(BaseModel):
    server_id: str
    timestamp: str
    log_level: str
    error_type: str
    message: str
    stack_trace: str
    raw_log: str
    ai_analysis: Optional[str] = None
    notified: bool = False
    notified_at: Optional[str] = None


@api_router.get("/api/servers")
def get_servers(request: Request, _api_key_ok: None = Depends(_check_api_key)):
    config = request.app.state.config
    servers = db.list_servers(config.db_path)
    return [
        {
            "server_id": s["server_id"],
            "host": s["host"],
            "port": s["port"],
            "username": s["username"],
            "ssh_key_path": s["ssh_key_path"],
            "log_path": s["log_path"],
            "format": s["format"],
            "custom_pattern": s["custom_pattern"],
        }
        for s in servers
    ]


@api_router.post("/api/errors")
def post_error(request: Request, body: ErrorIn, _api_key_ok: None = Depends(_check_api_key)):
    config = request.app.state.config
    db.insert_error(
        config.db_path,
        body.server_id, body.timestamp, body.log_level, body.error_type,
        body.message, body.stack_trace, body.raw_log,
        ai_analysis=body.ai_analysis, notified=body.notified, notified_at=body.notified_at,
    )
    return {"status": "ok"}
