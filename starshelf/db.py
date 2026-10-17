import os
from pathlib import Path
from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine
from .models import SyncState


def default_path() -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "starshelf/catalog.db"


def make_engine(path: str | Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
    return engine


def initialize(engine):
    Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql("""CREATE VIRTUAL TABLE IF NOT EXISTS repository_fts USING fts5(
            full_name, description, topics, primary_language, personal_notes,
            tokenize='unicode61 remove_diacritics 2')""")
        # Triggers keep the index in the same transaction as all writes, including imports.
        for action in ("INSERT", "UPDATE"):
            c.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS repository_fts_{action.lower()}
                AFTER {action} ON repository BEGIN
                DELETE FROM repository_fts WHERE rowid=new.id;
                INSERT INTO repository_fts(rowid, full_name, description, topics, primary_language, personal_notes)
                SELECT new.id, new.full_name, new.description, new.topics, new.primary_language,
                COALESCE((SELECT personal_notes FROM repositoryannotation WHERE repository_id=new.id),''); END""")
            c.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS annotation_fts_{action.lower()}
                AFTER {action} ON repositoryannotation BEGIN
                UPDATE repository_fts SET personal_notes=new.personal_notes WHERE rowid=new.repository_id; END""")
        c.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS repository_fts_delete AFTER DELETE ON repository
            BEGIN DELETE FROM repository_fts WHERE rowid=old.id; END""")
        c.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS annotation_fts_delete AFTER DELETE ON repositoryannotation
            BEGIN UPDATE repository_fts SET personal_notes='' WHERE rowid=old.repository_id; END""")
    with Session(engine) as s:
        state = s.get(SyncState, 1)
        if not state:
            s.add(SyncState())
        elif state.last_sync_status == "running":
            state.last_sync_status = "error"
            state.last_sync_error = "Previous synchronization was interrupted. Your catalog was preserved; sync again."
            state.phase = "Interrupted"
            s.add(state)
        s.commit()
