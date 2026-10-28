from grok_bridge.services.webhook_client import WebhookClient


def test_default_bearer_header() -> None:
    client = WebhookClient(url="https://example.com", key="secret")
    assert client.build_headers()["Authorization"] == "Bearer secret"


def test_custom_header_without_scheme() -> None:
    client = WebhookClient(url="https://example.com", key="secret", auth_header="X-Api-Key", auth_scheme="none")
    headers = client.build_headers()
    assert headers["X-Api-Key"] == "secret"
    assert "Authorization" not in headers


def test_no_key_no_auth_header() -> None:
    assert "Authorization" not in WebhookClient(url="https://example.com").build_headers()
