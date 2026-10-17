import json
import re
from sqlalchemy import text
from sqlmodel import Session, select
from .models import Repository, RepositoryAnnotation, StarList, RepositoryStarList


def search_expression(query):
    # Treat all input as literal Unicode terms, never as FTS operators or syntax.
    terms = re.findall(r"\w+", query, re.UNICODE)[:20]
    return " AND ".join('"' + term.replace('"', '""') + '"*' for term in terms)


def listing(engine, *, q="", language=None, topic=None, review_status=None,
            list_id=None, unclassified=False, archived=None, starred=True,
            sort="starred_at", direction="desc", page=1, per_page=50):
    clauses, params = [], {}
    expression = search_expression(q)
    join = "LEFT JOIN repositoryannotation a ON a.repository_id=r.id"
    if expression:
        join += " JOIN repository_fts ON repository_fts.rowid=r.id"
        clauses.append("repository_fts MATCH :search")
        params["search"] = expression
    for column, value in (("primary_language", language), ("archived", archived), ("is_starred", starred)):
        if value is not None:
            clauses.append(f"r.{column} = :{column}")
            params[column] = value
    if topic:
        clauses.append("EXISTS (SELECT 1 FROM json_each(r.topics) WHERE value=:topic)")
        params["topic"] = topic
    if review_status:
        clauses.append("COALESCE(a.review_status,'unreviewed')=:review_status")
        params["review_status"] = review_status
    if list_id:
        clauses.append("EXISTS (SELECT 1 FROM repositorystarlist l WHERE l.repository_id=r.id AND l.list_id=:list_id)")
        params["list_id"] = list_id
    if unclassified:
        clauses.append("NOT EXISTS (SELECT 1 FROM repositorystarlist l WHERE l.repository_id=r.id)")
    where = " AND ".join(clauses) or "1=1"
    orders = {"name": "r.full_name COLLATE NOCASE", "stars": "r.stars_count", "starred_at": "r.starred_at",
        "pushed_at": "r.pushed_at", "updated_at": "r.updated_at"}
    order = "bm25(repository_fts,8,3,2,1,4) ASC" if sort == "relevance" and expression else f"{orders.get(sort, 'r.starred_at')} {direction}"
    with engine.connect() as c:
        total = c.execute(text(f"SELECT count(*) FROM repository r {join} WHERE {where}"), params).scalar_one()
        params.update(limit=per_page, offset=(page - 1) * per_page)
        rows = c.execute(text(f"""SELECT r.*, COALESCE(a.review_status,'unreviewed') AS review_status
            FROM repository r {join} WHERE {where} ORDER BY {order},r.id ASC LIMIT :limit OFFSET :offset"""), params).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            item["topics"] = json.loads(item["topics"])
            for flag in ("archived", "disabled", "private", "is_starred"):
                item[flag] = bool(item[flag])
            items.append(item)
        return {"items": items, "total": total, "page": page, "per_page": per_page}


def detail(session: Session, repo: Repository):
    result = repo.model_dump()
    annotation = session.get(RepositoryAnnotation, repo.id)
    result["annotation"] = annotation.model_dump() if annotation else {"personal_notes": "", "review_status": "unreviewed"}
    result["lists"] = [item.model_dump() for item in session.exec(select(StarList).join(
        RepositoryStarList, RepositoryStarList.list_id == StarList.id).where(RepositoryStarList.repository_id == repo.id)).all()]
    return result
