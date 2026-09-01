import pytest

from scripts.sanitization import sanitize_error_text


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Basic dXNlcjpwYXNz",
        "Authorization: Bearer hidden-token",
        "Cookie: session=SECRET; auth=OTHER",
        "{'Cookie': 'session=SECRET; auth=OTHER'}",
        'request failed {"api_key":"MYSUPERSECRET"}',
        "token=hidden-token",
        "client_secret=client-secret-value",
        "refresh_token=refresh-secret-value",
        "https://media.example/image.jpg?signature=SECRET",
    ],
)
def test_sanitize_error_text_removes_complete_credentials(raw):
    cleaned = sanitize_error_text(raw)

    for secret in (
        "dXNlcjpwYXNz",
        "hidden-token",
        "SECRET",
        "OTHER",
        "MYSUPERSECRET",
        "client-secret-value",
        "refresh-secret-value",
    ):
        assert secret not in cleaned
    assert "[REDACTED" in cleaned
