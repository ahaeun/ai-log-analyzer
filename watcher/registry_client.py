import requests

from watcher.models import ServerEntry


def fetch_servers(registry_url, api_key):
    response = requests.get(registry_url, headers={"X-API-Key": api_key}, timeout=5)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise ValueError(f"registry response is not a JSON array: {type(data).__name__}")

    servers = []
    skipped = []
    for item in data:
        if not isinstance(item, dict):
            skipped.append(("<unknown>", f"server entry is not a JSON object: {type(item).__name__}"))
            continue
        try:
            servers.append(ServerEntry(**item))
        except (TypeError, ValueError) as e:
            skipped.append((item.get("server_id", "<unknown>"), str(e)))

    return servers, skipped
