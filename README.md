# StarShelf

A compact, local-first library for your GitHub starred repositories. Vue 3 + TypeScript + Tailwind, served by one FastAPI process. SQLite/SQLModel stores your catalog and personal notes; FTS5 searches names, descriptions, topics, languages, and notes. No cloud infrastructure, analytics, AI, Docker, or account registration.

## Install and launch

Requires macOS or Linux, Python **3.12+**, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node **22.12+** (or Node 20.19+), npm, and [GitHub CLI](https://cli.github.com/). Node is only needed to build/develop the frontend.

From the repository root:

```sh
gh auth login --hostname github.com
uv sync --python 3.12
npm --prefix frontend ci
npm --prefix frontend run build
uv run starshelf
```

Open **http://127.0.0.1:8765**, then click **Sync GitHub**. Subsequent launches need only `uv run starshelf`. `uv run --offline starshelf` also works once dependencies are installed. The built assets are in `starshelf/static/` and are included when packaging a wheel after building. The default server binds only to `127.0.0.1`.

The SQLite database is persistent at `$XDG_DATA_HOME/starshelf/catalog.db`, defaulting to `~/.local/share/starshelf/catalog.db` on both supported systems. To choose a different database:

```sh
STARSHELF_DB=/absolute/path/to/catalog.db uv run starshelf
```

The application creates its data directory. Keep the database on a local filesystem. A running catalog uses SQLite WAL sidecar files; use annotation export for a portable notes backup, or stop the app before copying the entire database. Existing catalogs are upgraded automatically on startup to add nullable commit-date and default-branch fields; notes and other catalog data are preserved.

## Development

In two terminals:

```sh
uv run uvicorn starshelf.app:app --host 127.0.0.1 --port 8765 --reload
```

```sh
npm --prefix frontend run dev
```

Open the Vite URL, normally http://127.0.0.1:5173. Vite proxies `/api` to FastAPI. No permissive CORS is enabled. The API schema is at http://127.0.0.1:8765/openapi.json; mutation requests require the `X-StarShelf: 1` header. CDN-backed API documentation pages are disabled to keep the application self-contained. Run a single server process/worker for this single-user application; the operation lock and sync task live in that process.

## Using the library

- Search uses debounced, case-insensitive FTS5 prefix matching. Terms combine with AND; punctuation is treated as separators. Search defaults to weighted BM25 ranking when starting from the default date sort; choose **Search relevance** to restore it after changing sort, or sort by name, stars, star date, last commit, last activity, or last update.
- Combine sidebar lists/review statuses/languages with topic, archived, and starred-status filters. Results are filtered/sorted/paginated on the server, 50 per page. View state is encoded in URL query parameters. Detail selection preserves the view.
- Repository details show how many **other repositories by the same owner** are in your local catalog, including previously starred repositories. Click that button to show the owner’s whole collection. It clears other filters/search, keeps sorting, and resets pagination. The removable **Owner** filter is stored in the URL and can be combined with other filters afterward. “Owner” means the GitHub user or organization that owns the repository, not its commit authors.
- Click a row, or use **↑ / ↓**, to open its details. **Enter** focuses the inspector; **Esc** closes it (or leaves an editable field). **/** or **Cmd/Ctrl+K** focuses search; **O** opens GitHub; **?** shows shortcut help. Shortcuts do not capture typing in editable controls.
- Write notes, choose a review status, and click **Save notes**. Unsaved edits prompt before leaving the selected repository. Notes belong to StarShelf and survive every sync and unstar.
- **Remove GitHub star** requires confirmation. **Star again on GitHub** restores a previously starred repository. Both need internet and verified CLI OAuth scopes. Local changes happen only after GitHub confirms success. No operation is queued for later execution.
- **View README** fetches Markdown only on demand. DOMPurify sanitizes rendered HTML; embedded images/media, styles, forms, frames, and scripts are removed. Remote images are intentionally blocked to avoid tracking. Relative README links are not rewritten, and README content is not cached across page reloads.
- Use the footer's **Export notes** / **Import notes** for JSON backups. Import replaces notes/statuses only for matching repository IDs and rejects backups identified as another account. Unknown IDs are skipped and counted; sync first before restoring. The full import validates before writing and commits atomically. Protect backups like other personal files.
- Choose **System**, **Light**, or **Dark** in the sidebar. Appearance stays in local browser storage.

## GitHub behavior and limitations

See [the API investigation](docs/github-api.md) for official sources, authentication permissions, queries, pagination, and limits.

GitHub's current documented GraphQL schema supports Star Lists. StarShelf imports their names, descriptions, and many-to-many memberships. If GraphQL access fails, reliable REST starred sync continues, cached lists remain untouched, and the interface displays a warning. List editing stays on GitHub. Metadata is limited to what the authenticated token can access; a previously visible repository absent from the current result is marked unstarred (loss of access is indistinguishable from unstar in that feed).

Every synchronization scans all starred pages. This is intentional full reconciliation, **not incremental sync**. All repository metadata and successful list updates commit atomically after retrieval. Local annotations are separate. Repositories absent from a successful scan are marked unstarred, never deleted. The first sync pins the GitHub account ID; changing CLI accounts requires switching back or choosing a separate database.

The local catalog, search, filters, details, annotations, and JSON backups work offline. Sync, README retrieval, GitHub links, and star changes need internet. Authentication tokens stay exclusively in backend memory and are obtained from `gh` when needed. Fine-grained tokens can read, but write actions fail closed when their permissions cannot be verified beforehand; use CLI OAuth authentication for star management.

This utility has no multi-user security boundary: run it on loopback for your own use. Same-origin mutation checks, a required custom request header, trusted-host validation, a restrictive production CSP, URL validation, and sanitized Markdown protect the local interface. Do not expose it on a network or run multiple workers.

## Verify

```sh
uv run pytest
npm --prefix frontend run test
npm --prefix frontend run check
npm --prefix frontend run build
uv run starshelf
curl --fail http://127.0.0.1:8765/api/health
curl --fail http://127.0.0.1:8765/
```

Backend tests use temporary databases and mocked GitHub transports. They cover persistence, renames, full reconciliation, idempotency, annotation retention, multi-list membership, filters, sorting/pagination, FTS triggers/ranking, API/network/rate errors, scope checks, confirmed writes, backups, and interrupted synchronization. Frontend unit tests cover bookmarkable filters, safe URL handling, and keyboard helpers. No real GitHub credentials are needed.

## Project map

- `starshelf/models.py`, `db.py`: relational schema, WAL and FTS5 triggers.
- `starshelf/github.py`, `sync.py`: authenticated GitHub requests and atomic reconciliation.
- `starshelf/catalog.py`, `app.py`: query logic, REST routes, local security, static serving.
- `frontend/src/`: Vue explorer, URL/keyboard helpers, styles and tests.

Dependencies are deliberately small: httpx handles GitHub requests; SQLModel/FastAPI provide validated persistence/API; Uvicorn serves the app; marked + DOMPurify safely render optional READMEs. Vue native reactivity manages state. No external search service or state library is used.

## Commit and activity dates

The table and inspector show two independent dates, each sortable:

- **Last commit** is the committer date of the commit at the tip of the repository’s **default branch**. The inspector shows the branch name. It does not measure development on other branches or the time a commit was pushed.
- **Last activity** is GitHub’s repository `pushed_at` timestamp, previously labeled “Last push.” It may reflect PR-related Git activity even when the default branch has not changed. It is **not** a complete history of issues, comments, or reviews. The separate “Last update” field remains repository metadata’s `updated_at`.

Default-branch commit dates are fetched during manual sync using GraphQL batches of 50 repositories. This avoids one REST request per repository. Empty repositories or missing default branches show “—”; existing catalogs show “—” until their next successful commit-date sync. If GraphQL access fails, starred sync still succeeds with a warning and cached commit dates remain intact. No push or update timestamp is substituted for an unknown commit date.
