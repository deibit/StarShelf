import asyncio
import base64
import httpx


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


async def cli_token() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "auth", "token", "--hostname", "github.com",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except FileNotFoundError:
        raise GitHubError("GitHub CLI is missing. Install gh, then run gh auth login.", 401)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise GitHubError("GitHub CLI authentication timed out.", 401)
    if proc.returncode or not out.strip():
        raise GitHubError("GitHub CLI is not authenticated. Run gh auth login --hostname github.com.", 401)
    return out.decode().strip()


class GitHub:
    def __init__(self, token: str, transport=None):
        self.client = httpx.AsyncClient(base_url="https://api.github.com", timeout=30,
            transport=transport, headers={"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.RequestError:
            raise GitHubError("Cannot reach GitHub. Check your connection; your local catalog is unchanged.")
        if response.status_code >= 400:
            if response.status_code == 429 or (response.status_code == 403 and
                (response.headers.get("x-ratelimit-remaining") == "0" or response.headers.get("retry-after"))):
                retry = response.headers.get("retry-after")
                reset = response.headers.get("x-ratelimit-reset")
                raise GitHubError(f"GitHub rate limit reached. Retry after {retry + ' seconds' if retry else 'reset (Unix time ' + str(reset) + ')'}. No automatic retry.", 429)
            message = {401: "GitHub authentication expired. Run gh auth login.",
                403: "GitHub denied access. Check token permissions and organization SSO.",
                404: "GitHub repository or README was not found, or the token cannot access it."}
            raise GitHubError(message.get(response.status_code, f"GitHub returned HTTP {response.status_code}."),
                response.status_code if response.status_code in (401, 403, 404) else 502)
        return response

    async def identity(self):
        return (await self.request("GET", "/user")).json()

    async def starred(self, progress):
        rows = []
        page = 1
        while True:
            response = await self.request("GET", "/user/starred", params={"per_page": 100, "page": page,
                "sort": "created", "direction": "desc"}, headers={"Accept": "application/vnd.github.star+json"})
            rows.extend(response.json())
            progress(len(rows), "Fetching starred repositories")
            if 'rel="next"' not in response.headers.get("link", ""):
                return rows
            page += 1

    async def graphql(self, query, variables, *, feature="Star Lists"):
        payload = (await self.request("POST", "/graphql", json={"query": query, "variables": variables})).json()
        if payload.get("errors"):
            types = {e.get("type") for e in payload["errors"]}
            if "RATE_LIMITED" in types:
                raise GitHubError("GitHub GraphQL rate limit reached. Retry later.", 429)
            raise GitHubError(f"GitHub {feature} are unavailable for this token or API schema. Cached data was preserved.")
        return payload["data"]

    async def commit_dates(self, entries, progress):
        """Fetch default-branch tip dates in batches, without per-repository REST calls."""
        result = {}
        for offset in range(0, len(entries), 50):
            batch = entries[offset:offset + 50]
            ids = [entry["repo"].get("node_id") for entry in batch]
            if not all(ids):
                raise GitHubError("Repository node IDs are missing; commit dates could not be refreshed.")
            data = await self.graphql("""query($ids:[ID!]!) { nodes(ids:$ids) { ... on Repository {
                id defaultBranchRef { name target { ... on Commit { committedDate } } }
            } } }""", {"ids": ids}, feature="commit dates")
            nodes = {node["id"]: node for node in data["nodes"] if node}
            for entry, node_id in zip(batch, ids):
                if node_id not in nodes:
                    raise GitHubError("A repository became inaccessible; cached commit dates were preserved.")
                branch = nodes[node_id].get("defaultBranchRef")
                result[entry["repo"]["id"]] = (
                    ((branch.get("target") or {}).get("committedDate") if branch else None),
                    branch["name"] if branch else None,
                )
            progress(None, f"Fetching commit dates: {offset + len(batch)} / {len(entries)}")
        return result

    async def lists(self, progress):
        lists = []
        cursor = None
        while True:
            data = await self.graphql("""query($cursor:String) { viewer { lists(first:100,after:$cursor) {
                nodes { id name description } pageInfo { hasNextPage endCursor } } } }""", {"cursor": cursor})
            connection = data["viewer"]["lists"]
            lists.extend(connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                break
            cursor = connection["pageInfo"]["endCursor"]
        for item in lists:
            item["repository_ids"] = []
            cursor = None
            while True:
                data = await self.graphql("""query($id:ID!,$cursor:String) { node(id:$id) { ... on UserList {
                    items(first:100,after:$cursor) { nodes { ... on Repository { databaseId } }
                    pageInfo { hasNextPage endCursor } } } } }""", {"id": item["id"], "cursor": cursor})
                if not data.get("node"):
                    raise GitHubError("A Star List changed during synchronization. Cached lists were preserved; sync again.")
                connection = data["node"]["items"]
                item["repository_ids"].extend(n["databaseId"] for n in connection["nodes"] if n and n.get("databaseId"))
                progress(None, f"Fetching list: {item['name']}")
                if not connection["pageInfo"]["hasNextPage"]:
                    break
                cursor = connection["pageInfo"]["endCursor"]
        return lists

    async def verify_write(self, owner, name):
        # GitHub reports the permissions required by this exact endpoint on the read preflight.
        response = await self.request("GET", f"/repos/{owner}/{name}")
        scopes_header = response.headers.get("x-oauth-scopes")
        if scopes_header is not None:
            scopes = {x.strip() for x in scopes_header.split(",")}
            if not scopes.intersection({"repo", "public_repo"}):
                raise GitHubError("Star writes require a classic/OAuth token with public_repo (or repo for private repositories). Run gh auth refresh -s public_repo.", 403)
            if response.json().get("private") and "repo" not in scopes:
                raise GitHubError("Private repository writes require the repo scope.", 403)
        else:
            # GitHub does not expose a token introspection endpoint for fine-grained permission grants.
            # Fail closed rather than send a mutation whose grants cannot be verified in advance.
            raise GitHubError("Cannot verify write scopes for this token. Use GitHub CLI OAuth authentication with public_repo or repo. Fine-grained tokens remain usable for reads.", 403)

    async def set_star(self, owner, name, starred):
        await self.verify_write(owner, name)
        response = await self.request("PUT" if starred else "DELETE", f"/user/starred/{owner}/{name}", content=b"")
        if response.status_code != 204:
            raise GitHubError("GitHub did not confirm the star change. Sync to check the current state.")

    async def readme(self, owner, name):
        data = (await self.request("GET", f"/repos/{owner}/{name}/readme")).json()
        if data.get("encoding") != "base64" or data.get("size", 0) > 2_000_000:
            raise GitHubError("README is too large or uses an unsupported encoding.")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
