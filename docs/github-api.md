# GitHub API investigation

Verified against official GitHub documentation on 2026-10-16. No internal endpoints or HTML scraping are used.

## Stars and timestamps

[REST starring](https://docs.github.com/en/rest/activity/starring): `GET /user/starred`, with `Accept: application/vnd.github.star+json`, returns `{starred_at, repo}` entries. Use `per_page=100` and follow the `next` pagination relation. Star dates are the user's star dates, not repository creation dates. Repository integer IDs survive renames.

We deliberately scan the **entire collection on each sync**. The endpoint has no reliable change feed for removals. Sorting by creation and stopping at a known repository would miss unstars and old metadata changes. This is a full reconciliation, not incremental synchronization. GitHub pagination is not a point-in-time snapshot: avoid concurrently changing your stars during a sync and rerun sync if needed.

## Star Lists are currently documented

The [GraphQL User and UserList reference](https://docs.github.com/en/graphql/reference/users) documents `User.lists`, `UserList.items`, IDs, names, descriptions, and pagination. `UserListItems` includes Repository; `Repository.databaseId` connects these items to REST IDs. `UpdateUserListsForItemInput.listIds` and the mutation's plural `lists` return field establish multiple-list membership. StarShelf reads lists but never modifies GitHub's list organization.

Enumerate `viewer.lists(first:100,after:...)`; independently paginate every list's `items(first:100,after:...)`. No nested connection is silently truncated. Members outside the currently accessible starred catalog are omitted. On schema/access/GraphQL errors, keep **all** previously cached lists and memberships, complete the REST import, and show a warning. Lists may then be stale. A successful list import replaces the GitHub list snapshot atomically. No local lists are needed for the MVP.

## Authentication and permissions

The backend runs `gh auth token --hostname github.com` using argument arrays, without a shell. The token stays in process memory for the GitHub operation. It is never stored in SQLite, returned in an API response, or placed in frontend assets. The catalog pins the stable GitHub account ID after the first sync and rejects sync/write operations under another account.

Preferred setup: `gh auth login --hostname github.com`. The normal GitHub CLI OAuth grant includes `repo` and `read:org`. See [GitHub CLI login](https://cli.github.com/manual/gh_auth_login) and [OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps). For classic/OAuth tokens use `public_repo` for public repository writes or `repo` for private access. Organization SSO and repository visibility can restrict access further. GraphQL requires authentication and accessible resources; its UserList reference does not document a distinct list-specific OAuth scope. List access is therefore capability-tested, rather than promised for every token.

The current REST reference requires fine-grained **Starring user permission (read)** for listing; star/unstar requires **Starring (write)** and **Metadata repository permission (read)**. Private access also depends on the token's selected repositories. GitHub offers no general fine-grained token permission-introspection API. To honor pre-write verification, StarShelf fails closed on writes when `X-OAuth-Scopes` cannot establish `public_repo`/`repo` grants; fine-grained tokens can still read. Use CLI OAuth authentication for writes. Before every write StarShelf verifies `/user` account identity and scopes from `GET /repos/{owner}/{repo}`, then sends `PUT` or `DELETE /user/starred/{owner}/{repo}`. Only a `204` updates SQLite. Unstar also requires explicit confirmation in both the interface and API payload. No writes are retried or queued. Unstarring on GitHub may remove list membership; sync again to refresh lists. Ambiguous network failures require a manual sync to reconcile.

## Limits and failure behavior

[REST limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api): typically 5,000 authenticated requests/hour; use returned limit/reset headers rather than assuming remaining budget. [GraphQL limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api): typically 5,000 points/hour for users, connection sizes 1–100, at most 500,000 nodes per query, plus secondary limits. These budgets can differ by account and app. Requests are sequential; rate-limit failures report retry/reset information and stop without sleeping or retrying mutations. GraphQL rate errors become a lists warning; a REST page failure leaves the entire previous catalog intact. An interrupted process is reported on next startup. Only manual sync creates an in-process asyncio task; there is no worker, queue, scheduler, or additional Python process.

## Default-branch commit dates

StarShelf reads `nodes(ids: ...) { ... on Repository { id defaultBranchRef { name target { ... on Commit { committedDate } } } } }` in batches of 50 using the REST repositories’ `node_id` values. See the official [Repository reference](https://docs.github.com/en/graphql/reference/repos#repository) and [Commit reference](https://docs.github.com/en/graphql/reference/git#commit). `last_commit_at` stores that tip commit’s committer timestamp, independently of REST `pushed_at` (“Last activity”) and `updated_at` (“Last update”). These are distinct timestamps, not an overall community activity feed.

Commit-date retrieval is separate from Star Lists. Any failed batch discards the tentative commit-date snapshot, preserves previously cached dates, and reports a warning while allowing REST starred synchronization and list synchronization to continue. Null default branches are successful empty results and clear the old cached commit date. Existing databases receive an additive upgrade on startup; no catalog or annotation records are deleted.
