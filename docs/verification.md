# Verification

Completed locally on macOS, 2026-10-16, using Python 3.12.14 and Node 26.11.1.

- Backend: **23 tests passed**, temporary SQLite databases and mocked GitHub HTTP transports. One upstream Starlette deprecation warning advises a future test-client transport migration; tests currently pass with the requested httpx dependency.
- Frontend: **6 tests passed**; Vue/TypeScript checks and Vite production build passed.
- Production: FastAPI returned HTTP 200 for `/`, compiled JavaScript/CSS assets, and `/api/health`; the browser loaded the compiled app through the Python server.
- Browser: checked empty state, a GitHub list filter, list membership in two lists, notes editing/saving, note full-text search, Cmd+K, arrow navigation, Enter focus, Escape dismissal, and light/dark appearance.
- Scale smoke check: a **temporary synthetic catalog** of 10,000 repositories imported in 2.60 seconds. Representative catalog/search/filter queries returned in 0.4–7.6 ms, returning at most 50 items. These are local observations, not a general performance guarantee. The fixture is outside the project and is not user data or a default seed.
- Packaging: built a wheel and source archive; inspected the wheel to confirm it contains `starshelf/static/index.html` and compiled asset files.
- Lockfiles use official PyPI/npm registry URLs. A temporary loopback curl relay worked around this execution environment's Python/Node outbound connection stalls during dependency installation; it is not part of the application.

## Not verified against a live account

GitHub CLI is not installed in this execution environment, so no real credentials were obtained, no personal catalog was imported, and no real GitHub star was changed. Pagination, Star List access/fallback, rate limits, authentication errors, account pinning, and star writes were exercised with mocks. The supported API surface was researched in official documentation; actual list access remains dependent on GitHub's schema and token grants. Install/authenticate `gh`, launch StarShelf, and perform the first manual sync to populate your catalog.
