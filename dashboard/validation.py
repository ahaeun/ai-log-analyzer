from watcher.models import ServerEntry


class ServerValidationError(ValueError):
    pass


class EmailValidationError(ValueError):
    pass


def validate_email(email):
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise EmailValidationError(f"'{email}'은 올바른 이메일 형식이 아닙니다")


def validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    try:
        ServerEntry(
            server_id=server_id,
            host=host,
            port=port,
            username=username,
            ssh_key_path=ssh_key_path,
            log_path=log_path,
            format=format,
            custom_pattern=custom_pattern,
        )
    except ValueError as e:
        raise ServerValidationError(str(e)) from e
