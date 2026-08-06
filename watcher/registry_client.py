import requests

from watcher.models import ServerEntry


def fetch_servers(registry_url):
    response = requests.get(registry_url, timeout=5)
    response.raise_for_status()
    data = response.json()

    servers = []
    skipped = []
    for item in data:
        try:
            servers.append(ServerEntry(**item))
        except (TypeError, ValueError) as e:
            skipped.append((item.get("server_id", "<unknown>"), str(e)))

    return servers, skipped
