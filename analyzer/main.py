from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from watcher.models import ErrorEvent

from analyzer.config import load_config_from_env
from analyzer.processor import process_error


class ErrorIn(BaseModel):
    server_id: str
    timestamp: str
    log_level: str
    error_type: str
    message: str
    stack_trace: str
    raw_log: str


def _check_api_key(request: Request, x_api_key: Optional[str] = Header(default=None)):
    config = request.app.state.config
    if not x_api_key or x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def create_app(config):
    app = FastAPI()
    app.state.config = config

    @app.post("/api/errors", status_code=202, dependencies=[Depends(_check_api_key)])
    def receive_error(body: ErrorIn, background_tasks: BackgroundTasks):
        event = ErrorEvent(
            server_id=body.server_id,
            timestamp=body.timestamp,
            log_level=body.log_level,
            error_type=body.error_type,
            message=body.message,
            stack_trace=body.stack_trace,
            raw_log=body.raw_log,
        )
        background_tasks.add_task(process_error, event, config)
        return {"status": "accepted"}

    return app


def app_factory():
    """실제 서버 구동 시 환경변수를 읽어 앱을 만든다.
    실행: uvicorn analyzer.main:app_factory --factory
    """
    return create_app(load_config_from_env())
