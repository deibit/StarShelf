import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .db import make_engine, initialize, default_path
from .models import Repository, RepositoryAnnotation, SyncState, ReviewStatus, now
from .github import GitHub, GitHubError, cli_token
from .sync import synchronize, update_state
from .catalog import listing, detail


class AnnotationPatch(BaseModel):
    personal_notes: str | None = Field(default=None, max_length=100_000)
    review_status: ReviewStatus | None = None


class StarChange(BaseModel):
    starred: bool
    confirmed: bool = False


class AnnotationBackup(BaseModel):
    repository_id: int = Field(gt=0)
    personal_notes: str = Field(max_length=100_000)
    review_status: ReviewStatus
    created_at: str | None = Field(default=None, max_length=100)
    updated_at: str | None = Field(default=None, max_length=100)


class Backup(BaseModel):
    version: Literal[1] = 1
    account_id: int | None = None
    annotations: list[AnnotationBackup] = Field(max_length=100_000)


def create_app(db_path=None, github_factory=None):
    engine = make_engine(db_path or os.environ.get("STARSHELF_DB", default_path()))
    operation_lock = asyncio.Lock()
    tasks = set()

    @asynccontextmanager
    async def lifespan(app):
        initialize(engine)
        yield
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        engine.dispose()

    app = FastAPI(title="StarShelf", lifespan=lifespan, docs_url=None, redoc_url=None,
        telemetry={"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False})
    app.state.engine = engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("x-starshelf") != "1":
                return JSONResponse({"detail": "Missing StarShelf request header."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin not in {f"{request.url.scheme}://{request.headers.get('host')}", "http://127.0.0.1:5173", "http://localhost:5173"}:
                return JSONResponse({"detail": "Cross-origin writes are blocked."}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 15_000_000:
                    return JSONResponse({"detail": "Request exceeds 15 MB."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid request size."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    async def github():
        return github_factory() if github_factory else GitHub(await cli_token())

    def get_repo(s, rid):
        repo = s.get(Repository, rid)
        if not repo:
            raise HTTPException(404, "Repository not found in local catalog.")
        return repo

    async def check_account(client):
        identity = await client.identity()
        with Session(engine) as s:
            state = s.get(SyncState, 1)
            if not state.account_id:
                raise GitHubError("Sync this catalog before changing stars.", 409)
            if state.account_id != identity["id"]:
                raise GitHubError("Authenticated GitHub account differs from the catalog owner.", 409)

    @app.exception_handler(GitHubError)
    async def github_error(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/repositories")
    def repositories(q: str = Query("", max_length=500), owner: str | None = Query(None, max_length=100),
        language: str | None = None, topic: str | None = None,
        review_status: ReviewStatus | None = None, list_id: str | None = None, unclassified: bool = False,
        archived: bool | None = None, starred: bool | None = True,
        scope: Literal["starred", "unstarred", "all"] | None = None,
        sort: Literal["name", "stars", "starred_at", "pushed_at", "last_commit_at", "updated_at", "relevance"] = "starred_at",
        direction: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1, le=1_000_000),
        per_page: int = Query(50, ge=1, le=100)):
        if scope:
            starred = {"starred": True, "unstarred": False, "all": None}[scope]
        return listing(engine, q=q, owner=owner, language=language, topic=topic, review_status=review_status,
            list_id=list_id, unclassified=unclassified, archived=archived, starred=starred,
            sort=sort, direction=direction, page=page, per_page=per_page)

    @app.get("/api/repositories/{rid}")
    def repository(rid: int):
        with Session(engine) as s:
            return detail(s, get_repo(s, rid))

    @app.patch("/api/repositories/{rid}/annotation")
    def annotate(rid: int, patch: AnnotationPatch):
        with Session(engine) as s:
            get_repo(s, rid)
            annotation = s.get(RepositoryAnnotation, rid) or RepositoryAnnotation(repository_id=rid)
            for key, value in patch.model_dump(exclude_none=True).items():
                setattr(annotation, key, value)
            annotation.updated_at = now()
            s.add(annotation)
            s.commit()
            s.refresh(annotation)
            return annotation

    @app.get("/api/lists")
    def lists():
        with engine.connect() as c:
            return [dict(r) for r in c.execute(text("""SELECT l.*, count(r.id) AS count FROM starlist l
                LEFT JOIN repositorystarlist m ON m.list_id=l.id
                LEFT JOIN repository r ON r.id=m.repository_id AND r.is_starred=1
                GROUP BY l.id ORDER BY l.name COLLATE NOCASE""")).mappings()]

    @app.get("/api/languages")
    def languages():
        with engine.connect() as c:
            return [dict(r) for r in c.execute(text("""SELECT primary_language AS name, count(*) AS count
                FROM repository WHERE is_starred=1 AND primary_language IS NOT NULL
                GROUP BY primary_language ORDER BY count DESC,name""")).mappings()]

    @app.get("/api/topics")
    def topics():
        with engine.connect() as c:
            return [dict(r) for r in c.execute(text("""SELECT j.value AS name,count(*) AS count FROM repository r,
                json_each(r.topics) j WHERE r.is_starred=1 GROUP BY j.value ORDER BY count DESC,name""")).mappings()]

    @app.get("/api/stats")
    def stats():
        with engine.connect() as c:
            total = c.execute(text("SELECT count(*) FROM repository WHERE is_starred=1")).scalar_one()
            unstarred = c.execute(text("SELECT count(*) FROM repository WHERE is_starred=0")).scalar_one()
            unclassified = c.execute(text("""SELECT count(*) FROM repository r WHERE is_starred=1
                AND NOT EXISTS(SELECT 1 FROM repositorystarlist l WHERE l.repository_id=r.id)""")).scalar_one()
            statuses = dict(c.execute(text("""SELECT COALESCE(a.review_status,'unreviewed'),count(*)
                FROM repository r LEFT JOIN repositoryannotation a ON a.repository_id=r.id
                WHERE is_starred=1 GROUP BY COALESCE(a.review_status,'unreviewed')""")).all())
            return {"starred": total, "unstarred": unstarred, "unclassified": unclassified, "review_statuses": statuses}

    async def run_sync():
        try:
            async with await github() as client:
                await synchronize(engine, client)
        except Exception as exc:
            update_state(engine, last_sync_status="error", phase="Failed", last_sync_error=
                str(exc) if isinstance(exc, GitHubError) else "Unable to start sync. Catalog preserved.")
        finally:
            operation_lock.release()

    @app.post("/api/sync", status_code=202)
    async def sync():
        if operation_lock.locked():
            raise HTTPException(409, "A GitHub operation is already in progress.")
        await operation_lock.acquire()
        update_state(engine, last_sync_status="running", last_sync_error=None, warning=None, fetched=0, phase="Authenticating")
        task = asyncio.create_task(run_sync())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"status": "running"}

    @app.get("/api/sync/status")
    def sync_status():
        with Session(engine) as s:
            return s.get(SyncState, 1)

    @app.put("/api/repositories/{rid}/star")
    async def change_star(rid: int, change: StarChange):
        if not change.starred and not change.confirmed:
            raise HTTPException(400, "Confirm removal of the GitHub star before proceeding.")
        if operation_lock.locked():
            raise HTTPException(409, "Wait for the current GitHub operation to finish.")
        async with operation_lock:
            with Session(engine) as s:
                repo = get_repo(s, rid)
                owner, name = repo.owner, repo.name
            async with await github() as client:
                await check_account(client)
                await client.set_star(owner, name, change.starred)
            with Session(engine) as s:
                repo = get_repo(s, rid)
                if change.starred and not repo.is_starred:
                    repo.starred_at = now()
                repo.is_starred = change.starred
                s.add(repo)
                s.commit()
                return detail(s, repo)

    @app.get("/api/repositories/{rid}/readme")
    async def readme(rid: int):
        with Session(engine) as s:
            repo = get_repo(s, rid)
            owner, name = repo.owner, repo.name
        async with await github() as client:
            content = await client.readme(owner, name)
        return {"markdown": content}

    @app.get("/api/annotations/export")
    def export_annotations():
        with Session(engine) as s:
            state = s.get(SyncState, 1)
            return JSONResponse({"version": 1, "account_id": state.account_id,
                "annotations": [a.model_dump() for a in s.exec(select(RepositoryAnnotation))]},
                headers={"Content-Disposition": 'attachment; filename="starshelf-annotations.json"'})

    @app.post("/api/annotations/import")
    def import_annotations(backup: Backup):
        with Session(engine) as s:
            state = s.get(SyncState, 1)
            if backup.account_id and state.account_id and backup.account_id != state.account_id:
                raise HTTPException(409, "This backup belongs to a different GitHub account.")
            skipped, imported = [], 0
            for item in backup.annotations:
                if not s.get(Repository, item.repository_id):
                    skipped.append(item.repository_id)
                    continue
                annotation = s.get(RepositoryAnnotation, item.repository_id) or RepositoryAnnotation(repository_id=item.repository_id)
                annotation.personal_notes = item.personal_notes
                annotation.review_status = item.review_status
                annotation.updated_at = now()
                s.add(annotation)
                imported += 1
            s.commit()
            return {"imported": imported, "skipped_repository_ids": skipped}

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (static / "index.html").exists():
            raise HTTPException(503, "Frontend not built. Run npm --prefix frontend ci && npm --prefix frontend run build.")
        return FileResponse(static / "index.html")

    return app


app = create_app()
