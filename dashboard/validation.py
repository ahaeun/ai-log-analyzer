from watcher.models import ServerEntry


class ServerValidationError(ValueError):
    pass


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
