from datetime import datetime, timezone
from typing import Literal
from sqlalchemy import CheckConstraint, Column, JSON
from sqlmodel import SQLModel, Field


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


ReviewStatus = Literal["unreviewed", "reviewed", "interesting", "discarded"]


class Repository(SQLModel, table=True):
    id: int = Field(primary_key=True)
    full_name: str = Field(index=True)
    owner: str
    name: str
    description: str | None = None
    url: str
    homepage_url: str | None = None
    primary_language: str | None = Field(default=None, index=True)
    stars_count: int = Field(default=0, index=True)
    forks_count: int = 0
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    archived: bool = False
    disabled: bool = False
    private: bool = False
    created_at: str | None = None
    updated_at: str | None = Field(default=None, index=True)
    pushed_at: str | None = Field(default=None, index=True)
    starred_at: str | None = Field(default=None, index=True)
    last_synced_at: str = Field(default_factory=now)
    is_starred: bool = Field(default=True, index=True)


class StarList(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    description: str | None = None
    source: str = "github"


class RepositoryStarList(SQLModel, table=True):
    repository_id: int = Field(foreign_key="repository.id", primary_key=True)
    list_id: str = Field(foreign_key="starlist.id", primary_key=True, index=True)


class RepositoryAnnotation(SQLModel, table=True):
    __table_args__ = (CheckConstraint("review_status IN ('unreviewed','reviewed','interesting','discarded')"),)
    repository_id: int = Field(foreign_key="repository.id", primary_key=True)
    personal_notes: str = ""
    review_status: str = Field(default="unreviewed", index=True)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class SyncState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    last_successful_sync: str | None = None
    last_sync_status: str = "idle"
    last_sync_error: str | None = None
    warning: str | None = None
    number_of_repositories: int = 0
    fetched: int = 0
    phase: str = "Ready"
    account_id: int | None = None
    account_login: str | None = None
