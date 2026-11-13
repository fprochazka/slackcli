"""The job title from the Slack profile in user output and users search."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from click.testing import Result

from slackcli.users import UserInfo, load_user_from_cache, save_user_to_cache

from .conftest import FakeWebClient

RunCli = Callable[..., Result]

ORG = "testorg"


def user_data(user_id: str, name: str, title: str | None = None) -> dict[str, Any]:
    profile: dict[str, Any] = {"display_name": name}
    if title is not None:
        profile["title"] = title
    return {"id": user_id, "name": name, "real_name": name.title(), "profile": profile}


def test_title_is_read_from_the_profile() -> None:
    user = UserInfo.from_api(user_data("U0123456789", "alice", "Product Manager"))

    assert user.title == "Product Manager"
    assert user.to_dict()["title"] == "Product Manager"


def test_missing_title_is_an_empty_string() -> None:
    user = UserInfo.from_api(user_data("U0123456789", "alice"))

    assert user.title == ""
    assert user.to_dict()["title"] == ""


def test_title_survives_the_cache() -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice", "Product Manager")))

    cached = load_user_from_cache(ORG, "U0123456789")

    assert cached is not None
    assert cached.title == "Product Manager"
    assert not cached.is_expired()


def test_users_get_shows_the_title(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.on("users.info", {"user": user_data("U0123456789", "alice", "Product Manager")})

    text = run_cli("users", "get", "U0123456789")

    assert text.exit_code == 0, text.stderr
    assert "Title:        Product Manager" in text.stdout

    as_json = run_cli("users", "get", "U0123456789", "--json")

    assert as_json.exit_code == 0, as_json.stderr
    assert json.loads(as_json.stdout)["title"] == "Product Manager"


def test_users_search_matches_the_title(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.on(
        "users.list",
        {
            "members": [
                user_data("U0123456789", "alice", "Product Manager"),
                user_data("U0987654321", "bob", "Backend Engineer"),
            ]
        },
    )

    result = run_cli("users", "search", "product", "--json")

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert [u["id"] for u in data["users"]] == ["U0123456789"]

    text = run_cli("users", "search", "engineer")

    assert text.exit_code == 0, text.stderr
    assert "@bob" in text.stdout
    assert "- Backend Engineer" in text.stdout
    assert "@alice" not in text.stdout
