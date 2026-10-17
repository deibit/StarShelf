from sqlalchemy import delete, update
from sqlmodel import Session, select
from .models import Repository, StarList, RepositoryStarList, SyncState, now
from .github import GitHubError


def repository_from_github(entry):
    r = entry["repo"]
    return Repository(id=r["id"], full_name=r["full_name"], owner=r["owner"]["login"], name=r["name"],
        description=r.get("description"), url=r["html_url"], homepage_url=r.get("homepage"),
        primary_language=r.get("language"), stars_count=r.get("stargazers_count", 0),
        forks_count=r.get("forks_count", 0), topics=r.get("topics", []), archived=r.get("archived", False),
        disabled=r.get("disabled", False), private=r.get("private", False), created_at=r.get("created_at"),
        updated_at=r.get("updated_at"), pushed_at=r.get("pushed_at"), starred_at=entry["starred_at"])


def update_state(engine, **fields):
    with Session(engine) as s:
        state = s.get(SyncState, 1)
        for key, value in fields.items():
            setattr(state, key, value)
        s.add(state)
        s.commit()


def apply_snapshot(engine, entries, lists, account):
    # All catalog mutations commit together, only after all starred pages succeed.
    with Session(engine) as s:
        state = s.get(SyncState, 1)
        if state.account_id and state.account_id != account["id"]:
            raise GitHubError("Authenticated GitHub account changed. Use the original account or a separate STARSHELF_DB.", 409)
        s.exec(update(Repository).values(is_starred=False))
        ids = set()
        for entry in entries:
            repo = repository_from_github(entry)
            ids.add(repo.id)
            s.merge(repo)
        s.flush()
        if lists is not None:
            s.exec(delete(RepositoryStarList).where(RepositoryStarList.list_id.in_(
                select(StarList.id).where(StarList.source == "github"))))
            s.exec(delete(StarList).where(StarList.source == "github"))
            for item in lists:
                s.add(StarList(id=item["id"], name=item["name"], description=item.get("description")))
            s.flush()
            for item in lists:
                for rid in set(item["repository_ids"]) & ids:
                    s.add(RepositoryStarList(repository_id=rid, list_id=item["id"]))
        state.account_id = account["id"]
        state.account_login = account["login"]
        state.number_of_repositories = len(ids)
        state.last_successful_sync = now()
        state.last_sync_status = "success"
        state.phase = "Complete"
        state.last_sync_error = None
        s.add(state)
        s.commit()


async def synchronize(engine, github):
    def progress(count, phase):
        fields = {"phase": phase}
        if count is not None:
            fields["fetched"] = count
        update_state(engine, **fields)
    try:
        account = await github.identity()
        with Session(engine) as s:
            state = s.get(SyncState, 1)
            if state.account_id and state.account_id != account["id"]:
                raise GitHubError("Authenticated GitHub account changed. Switch back or use a separate STARSHELF_DB.", 409)
        entries = await github.starred(progress)
        lists = None
        try:
            lists = await github.lists(progress)
        except GitHubError as exc:
            update_state(engine, warning=f"Starred repositories synchronized; lists unavailable: {exc}")
        apply_snapshot(engine, entries, lists, account)
    except Exception as exc:
        # Never log response bodies, credentials, or exception reprs from network libraries.
        message = str(exc) if isinstance(exc, GitHubError) else "Synchronization failed. Local catalog preserved. Try again."
        update_state(engine, last_sync_status="error", last_sync_error=message, phase="Failed")
