import pytest
from fastapi.testclient import TestClient
from starshelf.app import create_app
from starshelf.sync import apply_snapshot


def entry(rid=1, name="demo/alpha", description="Fast SQLite explorer", language="Python", topics=None):
    owner, short = name.split('/')
    return {"starred_at": "2026-01-02T03:00:00Z", "repo": {"id": rid, "full_name": name,
        "name": short, "owner": {"login": owner}, "html_url": f"https://github.com/{name}",
        "description": description, "language": language, "topics": topics or ["database"],
        "stargazers_count": rid * 10, "pushed_at": f"2026-01-{rid:02}T00:00:00Z"}}


ACCOUNT = {"id": 7, "login": "demo"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app, headers={"X-StarShelf": "1"}) as client:
        apply_snapshot(app.state.engine, [entry(), entry(2,"demo/beta","Vue UI toolkit","TypeScript",["frontend"]),
            entry(3,"demo/gamma","Search engine","Rust")],
            [{"id":"UL_1","name":"Tools","repository_ids":[1,2]},
             {"id":"UL_2","name":"Favorites","repository_ids":[1]}], ACCOUNT)
        yield client
