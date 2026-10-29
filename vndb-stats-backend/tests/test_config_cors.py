"""CORS origin parsing.

The API accepts the docker-compose/.env JSON form and the plain
comma-separated form used by the native systemd environment file. systemd can
strip the quotes inside a JSON literal, so the mangled variant is covered too.
"""

import pytest

pytest.importorskip("pydantic_settings")

DATABASE_URL = "postgresql+asyncpg://test:test@localhost/test"


def make_settings(**kwargs):
    from app.config import Settings

    return Settings(_env_file=None, database_url=DATABASE_URL, **kwargs)


def test_json_array():
    settings = make_settings(cors_origins='["https://a.example","https://b.example"]')
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_comma_separated():
    settings = make_settings(cors_origins="https://a.example,https://b.example")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_single_url():
    settings = make_settings(cors_origins="https://a.example")
    assert settings.cors_origins == ["https://a.example"]


def test_json_without_quotes_from_systemd():
    settings = make_settings(cors_origins="[https://a.example,https://b.example]")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_whitespace_and_trailing_comma():
    settings = make_settings(cors_origins=" https://a.example , https://b.example , ")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_empty_string():
    settings = make_settings(cors_origins="")
    assert settings.cors_origins == []
