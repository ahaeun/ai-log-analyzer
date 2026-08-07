import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from dashboard import db
from dashboard.auth import require_master
from dashboard.templating import templates
from dashboard.validation import EmailValidationError, validate_email

settings_router = APIRouter()


@settings_router.get("/settings/emails")
def list_emails_page(request: Request, user: dict = Depends(require_master)):
    config = request.app.state.config
    return templates.TemplateResponse(
        request,
        "emails.html",
        {
            "user": user,
            "emails": db.list_allowed_emails(config.db_path),
            "master_email": config.master_email,
            "error": None,
        },
    )


@settings_router.post("/settings/emails")
def add_email(request: Request, email: str = Form(...), user: dict = Depends(require_master)):
    config = request.app.state.config
    email = email.strip()
    try:
        validate_email(email)
    except EmailValidationError as e:
        return templates.TemplateResponse(
            request,
            "emails.html",
            {
                "user": user,
                "emails": db.list_allowed_emails(config.db_path),
                "master_email": config.master_email,
                "error": str(e),
            },
        )

    try:
        db.add_allowed_email(config.db_path, email)
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(
            request,
            "emails.html",
            {
                "user": user,
                "emails": db.list_allowed_emails(config.db_path),
                "master_email": config.master_email,
                "error": f"'{email}'은 이미 등록되어 있습니다",
            },
        )
    return RedirectResponse("/settings/emails", status_code=303)


@settings_router.post("/settings/emails/{email}/delete")
def delete_email(request: Request, email: str, user: dict = Depends(require_master)):
    config = request.app.state.config
    db.delete_allowed_email(config.db_path, email)
    return RedirectResponse("/settings/emails", status_code=303)
