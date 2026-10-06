"""The shape of data/repos.json. The site validates the same shape with zod."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Owner(BaseModel):
    """Only what GitHub shows on a public profile. No email, no linked sites."""

    model_config = ConfigDict(extra="forbid")

    login: str
    type: str
    name: str | None = None
    location: str | None = None
    avatar_url: str
    html_url: str


class StarsPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    stars: int


class Repo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    full_name: str
    html_url: str
    description: str | None
    stars: int
    forks: int
    language: str | None
    topics: list[str]
    license: str | None
    homepage: str | None
    created_at: str
    pushed_at: str | None
    fork: bool
    archived: bool
    is_template: bool
    # full_name of the root repository of a fork, and of the template a repo was
    # generated from. Copies of fal's own templates never count toward the headline.
    fork_source: str | None = None
    template_source: str | None = None
    owner: Owner
    # Owned by fal itself (fal-ai, fal-ai-community): kept out of the headline, the default
    # list, the digest and the builders page.
    owner_is_fal: bool = False
    # "code": fal shows up in the code. "mention": README, description or topics only.
    evidence: Literal["code", "mention"]
    sources: list[str]
    clients: list[str]
    # Model families seen in code. A lower bound, never a full list.
    models: list[str]
    kind: str
    stack: list[str]
    active: bool
    notable: bool
    # README size in bytes, looked up only when it decides "notable"; None when not looked up.
    readme_bytes: int | None = None
    # What the repo's manifest said, kept so a run that reads no manifest keeps the labels.
    # None: no manifest read yet.
    manifest_bot: bool | None = None
    manifest_library: bool | None = None
    first_seen: str
    last_seen: str
    stars_history: list[StarsPoint]


class Mention(BaseModel):
    """A repo whose README, description, name or topics mention fal, with no fal in its code.

    Kept slim: these come straight from repository search results, without extra lookups.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    full_name: str
    html_url: str
    description: str | None
    stars: int
    language: str | None
    topics: list[str]
    created_at: str
    pushed_at: str | None
    archived: bool
    owner_login: str
    owner_type: str
    owner_avatar_url: str
    owner_is_fal: bool = False
    sources: list[str]
    first_seen: str
    last_seen: str


class Exclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repos: list[str] = []
    owners: list[str] = []

    def drops(self, full_name: str, owner_login: str) -> bool:
        repos = {r.lower() for r in self.repos}
        owners = {o.lower() for o in self.owners}
        return full_name.lower() in repos or owner_login.lower() in owners
