<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import {
  defaults,
  parseView,
  viewQuery,
  isTyping,
  nextIndex,
  safeUrl,
  type Repo,
  type Detail,
  type ReviewStatus,
  type ViewState,
} from "./library";

const view = reactive(parseView(location.search));
const search = ref(view.q),
  searchInput = ref<HTMLInputElement>(),
  inspector = ref<HTMLElement>();
const rows = ref<Repo[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref(""),
  notice = ref("");
const selected = ref<Detail | null>(null),
  selectedIndex = ref(-1),
  detailLoading = ref(false);
const notes = ref(""),
  status = ref<ReviewStatus>("unreviewed"),
  saving = ref(false),
  starBusy = ref(false);
const dirty = computed(
  () =>
    !!selected.value &&
    (notes.value !== selected.value.annotation.personal_notes ||
      status.value !== selected.value.annotation.review_status),
);
const lists = ref<{ id: string; name: string; count: number }[]>([]),
  languages = ref<{ name: string; count: number }[]>([]),
  topics = ref<{ name: string; count: number }[]>([]);
const stats = ref({
  starred: 0,
  unstarred: 0,
  unclassified: 0,
  review_statuses: {} as Record<string, number>,
});
const sync = ref({
  last_sync_status: "idle",
  phase: "Ready",
  fetched: 0,
  last_successful_sync: null as string | null,
  last_sync_error: null as string | null,
  warning: null as string | null,
  account_login: null as string | null,
  number_of_repositories: 0,
});
const reviewStatuses: ReviewStatus[] = [
  "unreviewed",
  "reviewed",
  "interesting",
  "discarded",
];
const help = ref(false),
  readme = ref(""),
  readmeLoading = ref(false),
  readmeError = ref(""),
  showReadme = ref(false);
const importInput = ref<HTMLInputElement>(),
  importing = ref(false);
const theme = ref(localStorage.getItem("starshelf-theme") || "system");
const title = computed(
  () =>
    lists.value.find((l) => l.id === view.list_id)?.name ||
    (view.unclassified
      ? "Unclassified"
      : view.scope === "unstarred"
        ? "Previously starred"
        : view.scope === "all"
          ? "All repositories"
          : "All starred repositories"),
);
const activeFilters = computed(
  () =>
    !!(
      view.language ||
      view.topic ||
      view.review_status ||
      view.archived ||
      view.q ||
      view.list_id ||
      view.unclassified ||
      view.scope !== "starred"
    ),
);
const pages = computed(() => Math.max(1, Math.ceil(total.value / 50)));
const date = (value: string | null) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : "—";
const number = (value: number) =>
  new Intl.NumberFormat(undefined, {
    notation: value >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(value);
let searchTimer: ReturnType<typeof setTimeout>,
  pollTimer: ReturnType<typeof setInterval>,
  controller: AbortController | undefined,
  detailRequest = 0;
let loaded = false;

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-StarShelf": "1",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "Request failed" }));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Invalid request (HTTP ${response.status})`,
    );
  }
  return response.json();
}
async function loadRows() {
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  loading.value = true;
  try {
    const data = await api<{ items: Repo[]; total: number }>(
      "/repositories?" + viewQuery(view),
      { signal },
    );
    rows.value = data.items;
    total.value = data.total;
    selectedIndex.value = rows.value.findIndex(
      (r) => r.id === selected.value?.id,
    );
    if (view.page > pages.value) view.page = pages.value;
  } catch (e) {
    if (!signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!signal.aborted) loading.value = false;
  }
}
async function refreshFacets() {
  try {
    const [l, lang, t, s] = await Promise.all([
      api<typeof lists.value>("/lists"),
      api<typeof languages.value>("/languages"),
      api<typeof topics.value>("/topics"),
      api<typeof stats.value>("/stats"),
    ]);
    lists.value = l;
    languages.value = lang;
    topics.value = t;
    stats.value = s;
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function pollSync() {
  try {
    const previous = sync.value.last_sync_status;
    sync.value = await api<typeof sync.value>("/sync/status");
    if (previous === "running" && sync.value.last_sync_status !== "running") {
      await refreshFacets();
      await loadRows();
    }
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function startSync() {
  error.value = "";
  try {
    await api("/sync", { method: "POST" });
    await pollSync();
  } catch (e) {
    error.value = (e as Error).message;
  }
}
function setFilter(key: keyof ViewState, value: string | boolean) {
  (view as Record<string, unknown>)[key] = value;
  view.page = 1;
}
function library(scope = "starred", unclassified = false, list_id = "") {
  view.scope = scope;
  view.unclassified = unclassified;
  view.list_id = list_id;
  view.page = 1;
}
function clearFilters() {
  Object.assign(view, defaults);
  search.value = "";
}
function sortBy(key: string) {
  if (view.sort === key)
    view.direction = view.direction === "asc" ? "desc" : "asc";
  else {
    view.sort = key;
    view.direction = key === "name" ? "asc" : "desc";
  }
  view.page = 1;
}
function canLeave() {
  return (
    !dirty.value ||
    window.confirm(
      "Discard the unsaved changes to these notes and review status?",
    )
  );
}
async function selectRepo(repo: Repo, index: number) {
  if (selected.value?.id === repo.id) {
    selectedIndex.value = index;
    return;
  }
  if (!canLeave()) return;
  const request = ++detailRequest;
  detailLoading.value = true;
  error.value = "";
  showReadme.value = false;
  readme.value = "";
  readmeError.value = "";
  try {
    const data = await api<Detail>("/repositories/" + repo.id);
    if (request !== detailRequest) return;
    selected.value = data;
    selectedIndex.value = index;
    notes.value = data.annotation.personal_notes;
    status.value = data.annotation.review_status;
  } catch (e) {
    if (request === detailRequest) error.value = (e as Error).message;
  } finally {
    if (request === detailRequest) detailLoading.value = false;
  }
}
function closeDetails() {
  if (canLeave()) {
    ++detailRequest;
    selected.value = null;
    selectedIndex.value = -1;
    detailLoading.value = false;
  }
}
async function saveAnnotation() {
  if (!selected.value || saving.value) return;
  const id = selected.value.id,
    payload = { personal_notes: notes.value, review_status: status.value };
  saving.value = true;
  try {
    const annotation = await api<Detail["annotation"]>(
      `/repositories/${id}/annotation`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
    if (selected.value?.id === id) selected.value.annotation = annotation;
    notice.value = "Personal metadata saved locally.";
    await refreshFacets();
    await loadRows();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
async function toggleStar() {
  if (!selected.value || starBusy.value) return;
  const id = selected.value.id,
    starred = !selected.value.is_starred;
  if (
    !starred &&
    !window.confirm(
      `Remove your GitHub star from ${selected.value.full_name}? Your local notes will be kept. GitHub may also remove it from Star Lists.`,
    )
  )
    return;
  starBusy.value = true;
  error.value = "";
  try {
    const data = await api<Detail>(`/repositories/${id}/star`, {
      method: "PUT",
      body: JSON.stringify({ starred, confirmed: !starred }),
    });
    if (selected.value?.id === id) selected.value = data;
    notice.value = starred
      ? "Repository starred on GitHub."
      : "GitHub star removed; local metadata preserved.";
    await refreshFacets();
    await loadRows();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    starBusy.value = false;
  }
}
async function fetchReadme() {
  if (!selected.value) return;
  showReadme.value = !showReadme.value;
  if (!showReadme.value || readme.value) return;
  const id = selected.value.id;
  readmeLoading.value = true;
  readmeError.value = "";
  try {
    const data = await api<{ markdown: string }>(`/repositories/${id}/readme`);
    if (selected.value?.id !== id) return;
    const html = await marked.parse(data.markdown);
    // No remote images, inline media, forms, styles, frames, or executable content.
    readme.value = DOMPurify.sanitize(html, {
      FORBID_TAGS: [
        "img",
        "video",
        "audio",
        "iframe",
        "form",
        "input",
        "style",
        "svg",
        "math",
      ],
      FORBID_ATTR: ["style", "src", "srcset"],
    });
  } catch (e) {
    if (selected.value?.id === id) readmeError.value = (e as Error).message;
  } finally {
    readmeLoading.value = false;
  }
}
async function importBackup(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 15_000_000) {
    error.value = "Backup exceeds 15 MB.";
    return;
  }
  if (
    !window.confirm(
      "Import this backup? Notes and review statuses for matching repository IDs will be replaced.",
    )
  )
    return;
  importing.value = true;
  try {
    const body = JSON.parse(await file.text());
    const result = await api<{
      imported: number;
      skipped_repository_ids: number[];
    }>("/annotations/import", { method: "POST", body: JSON.stringify(body) });
    notice.value = `Imported ${result.imported} annotations. ${result.skipped_repository_ids.length} unknown repositories skipped.`;
    await refreshFacets();
    await loadRows();
    if (selected.value) {
      const id = selected.value.id;
      selected.value = null;
      const repo = rows.value.find((r) => r.id === id);
      if (repo) await selectRepo(repo, rows.value.indexOf(repo));
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    importing.value = false;
    if (importInput.value) importInput.value.value = "";
  }
}
function focusSearch() {
  searchInput.value?.focus();
  searchInput.value?.select();
}
async function keyboard(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    focusSearch();
    return;
  }
  if (isTyping(event.target)) {
    if (event.key === "Escape") (event.target as HTMLElement).blur();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "/") {
    event.preventDefault();
    focusSearch();
  } else if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    const i = nextIndex(selectedIndex.value, event.key, rows.value.length);
    if (i >= 0) {
      await selectRepo(rows.value[i]!, i);
      await nextTick();
      document
        .getElementById("repo-" + rows.value[i]!.id)
        ?.scrollIntoView({ block: "nearest" });
    }
  } else if (event.key === "Enter" && selected.value) {
    event.preventDefault();
    inspector.value?.focus();
  } else if (event.key === "Escape") closeDetails();
  else if (
    event.key.toLowerCase() === "o" &&
    selected.value &&
    safeUrl(selected.value.url)
  )
    window.open(safeUrl(selected.value.url), "_blank", "noopener,noreferrer");
  else if (event.key === "?") help.value = !help.value;
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value) {
    event.preventDefault();
    event.returnValue = "";
  }
}
function popstate() {
  Object.assign(view, parseView(location.search));
  search.value = view.q;
}
function applyTheme() {
  document.documentElement.dataset.theme = theme.value;
  localStorage.setItem("starshelf-theme", theme.value);
}
watch(theme, applyTheme, { immediate: true });
watch(search, (value) => {
  clearTimeout(searchTimer);
  if (value === view.q) return;
  searchTimer = setTimeout(() => {
    if (value && !view.q && view.sort === "starred_at") view.sort = "relevance";
    if (!value && view.sort === "relevance") view.sort = "starred_at";
    view.q = value;
    view.page = 1;
  }, 220);
});
watch(
  () => viewQuery(view),
  (query, previous) => {
    if (!loaded) return;
    const old = parseView(previous || "");
    if (
      view.page === old.page &&
      Object.keys(defaults).some(
        (k) =>
          k !== "page" &&
          view[k as keyof ViewState] !== old[k as keyof ViewState],
      )
    )
      view.page = 1;
    history.replaceState(
      null,
      "",
      location.pathname + (viewQuery(view) ? "?" + viewQuery(view) : ""),
    );
    loadRows();
  },
);
onMounted(async () => {
  window.addEventListener("keydown", keyboard);
  window.addEventListener("popstate", popstate);
  window.addEventListener("beforeunload", beforeUnload);
  await Promise.all([loadRows(), refreshFacets(), pollSync()]);
  loaded = true;
  pollTimer = setInterval(pollSync, 1500);
});
onUnmounted(() => {
  clearInterval(pollTimer);
  clearTimeout(searchTimer);
  controller?.abort();
  window.removeEventListener("keydown", keyboard);
  window.removeEventListener("popstate", popstate);
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a href="/" class="brand" aria-label="StarShelf home"
        ><span class="brand-mark">✳</span><strong>StarShelf</strong
        ><span class="local-badge">LOCAL</span></a
      >
      <div class="global-search">
        <span aria-hidden="true">⌕</span
        ><input
          ref="searchInput"
          v-model="search"
          maxlength="500"
          placeholder="Search your library…"
          aria-label="Search repositories, topics, and notes"
        /><button class="keycap" @click="focusSearch" title="Focus search">
          ⌘ K
        </button>
      </div>
      <div class="header-actions">
        <span class="connection"><i></i> Local catalog</span
        ><button
          class="sync-button"
          :disabled="sync.last_sync_status === 'running'"
          @click="startSync"
        >
          <span :class="{ spin: sync.last_sync_status === 'running' }">↻</span>
          {{ sync.last_sync_status === "running" ? "Syncing…" : "Sync GitHub" }}
        </button>
      </div>
    </header>

    <aside class="sidebar">
      <div class="sidebar-caption">LIBRARY</div>
      <nav aria-label="Catalog navigation">
        <button
          :class="{
            active:
              !view.list_id && !view.unclassified && view.scope === 'starred',
          }"
          @click="library()"
        >
          <span>▦</span> All starred <small>{{ stats.starred }}</small>
        </button>
        <button
          :class="{ active: view.unclassified }"
          @click="library('starred', true)"
        >
          <span>◫</span> Unclassified <small>{{ stats.unclassified }}</small>
        </button>
        <button
          :class="{ active: view.scope === 'unstarred' }"
          @click="library('unstarred')"
        >
          <span>☆</span> Previously starred <small>{{ stats.unstarred }}</small>
        </button>
        <div class="sidebar-caption section-gap">
          GITHUB STAR LISTS <span>{{ lists.length }}</span>
        </div>
        <button
          v-for="list in lists"
          :key="list.id"
          :class="{ active: view.list_id === list.id }"
          @click="library('starred', false, list.id)"
        >
          <span>☷</span><span class="truncate">{{ list.name }}</span
          ><small>{{ list.count }}</small>
        </button>
        <p v-if="!lists.length" class="sidebar-hint">
          Your GitHub lists will appear after synchronization.
        </p>
        <div class="sidebar-caption section-gap">REVIEW STATUS</div>
        <button
          v-for="item in reviewStatuses"
          :key="item"
          :class="{ active: view.review_status === item }"
          @click="
            setFilter('review_status', view.review_status === item ? '' : item)
          "
        >
          <span class="status-dot" :class="item"></span
          ><span class="capitalize">{{ item }}</span
          ><small>{{ stats.review_statuses[item] || 0 }}</small>
        </button>
        <div class="sidebar-caption section-gap">LANGUAGES</div>
        <button
          v-for="lang in languages"
          :key="lang.name"
          :class="{ active: view.language === lang.name }"
          @click="
            setFilter('language', view.language === lang.name ? '' : lang.name)
          "
        >
          <span class="language-dot" :data-language="lang.name"></span
          ><span class="truncate">{{ lang.name }}</span
          ><small>{{ lang.count }}</small>
        </button>
        <p v-if="!languages.length" class="sidebar-hint">
          No languages in your catalog yet.
        </p>
      </nav>
      <div class="sidebar-bottom">
        <label class="theme-control"
          ><span>◐ Appearance</span
          ><select v-model="theme" aria-label="Color theme">
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select></label
        ><button @click="help = !help">
          ⌨ Keyboard shortcuts <span>?</span>
        </button>
      </div>
    </aside>

    <main class="main-content">
      <div class="library-heading">
        <div>
          <div class="eyebrow">YOUR REPOSITORY COLLECTION</div>
          <h1>{{ title }}</h1>
        </div>
        <span class="result-counter"
          >{{ total.toLocaleString() }} repositories</span
        >
      </div>
      <div class="filterbar">
        <label
          ><span class="sr-only">Language</span
          ><select
            :value="view.language"
            @change="
              setFilter('language', ($event.target as HTMLSelectElement).value)
            "
          >
            <option value="">All languages</option>
            <option v-for="lang in languages" :key="lang.name">
              {{ lang.name }}
            </option>
          </select></label
        >
        <label
          ><span class="sr-only">Topic</span
          ><select
            :value="view.topic"
            @change="
              setFilter('topic', ($event.target as HTMLSelectElement).value)
            "
          >
            <option value="">All topics</option>
            <option v-for="topic in topics" :key="topic.name">
              {{ topic.name }}
            </option>
          </select></label
        >
        <label
          ><span class="sr-only">Archived status</span
          ><select
            :value="view.archived"
            @change="
              setFilter('archived', ($event.target as HTMLSelectElement).value)
            "
          >
            <option value="">Any activity</option>
            <option value="false">Active repositories</option>
            <option value="true">Archived repositories</option>
          </select></label
        >
        <label
          ><span class="sr-only">Starred status</span
          ><select
            :value="view.scope"
            @change="
              setFilter('scope', ($event.target as HTMLSelectElement).value)
            "
          >
            <option value="starred">Starred</option>
            <option value="unstarred">Unstarred</option>
            <option value="all">Starred &amp; unstarred</option>
          </select></label
        >
        <button v-if="activeFilters" class="clear-button" @click="clearFilters">
          Clear filters ×
        </button>
        <span class="filter-spacer"></span>
        <label
          ><span class="sr-only">Sort repositories</span
          ><select
            :value="view.sort"
            @change="
              setFilter('sort', ($event.target as HTMLSelectElement).value)
            "
          >
            <option value="starred_at">Date starred</option>
            <option value="name">Name</option>
            <option value="stars">Stars</option>
            <option value="pushed_at">Last push</option>
            <option value="updated_at">Last update</option>
            <option value="relevance">Search relevance</option>
          </select></label
        ><button
          class="order-button"
          :aria-label="
            'Sort ' + (view.direction === 'asc' ? 'descending' : 'ascending')
          "
          @click="view.direction = view.direction === 'asc' ? 'desc' : 'asc'"
        >
          {{ view.direction === "asc" ? "↑" : "↓" }}
        </button>
      </div>
      <div
        v-if="view.review_status || view.list_id || view.language"
        class="filter-chips"
      >
        <button v-if="view.list_id" @click="setFilter('list_id', '')">
          List: {{ title }} ×</button
        ><button
          v-if="view.review_status"
          @click="setFilter('review_status', '')"
        >
          {{ view.review_status }} ×</button
        ><button v-if="view.language" @click="setFilter('language', '')">
          {{ view.language }} ×
        </button>
      </div>
      <div v-if="error" class="alert error" role="alert">
        {{ error
        }}<button @click="error = ''" aria-label="Dismiss error">×</button>
      </div>
      <div v-if="notice" class="alert notice" role="status">
        {{ notice
        }}<button @click="notice = ''" aria-label="Dismiss notice">×</button>
      </div>
      <div v-if="sync.last_sync_error" class="alert error" role="alert">
        {{ sync.last_sync_error }}
      </div>
      <div v-if="sync.warning" class="alert warning">{{ sync.warning }}</div>
      <div class="table-scroll" :aria-busy="loading">
        <table v-if="rows.length" aria-label="Repository library">
          <thead>
            <tr>
              <th
                :aria-sort="
                  view.sort === 'name'
                    ? view.direction === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                "
              >
                <button @click="sortBy('name')">
                  Repository
                  {{
                    view.sort === "name"
                      ? view.direction === "asc"
                        ? "↑"
                        : "↓"
                      : ""
                  }}
                </button>
              </th>
              <th>Description</th>
              <th>Language</th>
              <th>
                <button @click="sortBy('stars')">
                  Stars
                  {{
                    view.sort === "stars"
                      ? view.direction === "asc"
                        ? "↑"
                        : "↓"
                      : ""
                  }}
                </button>
              </th>
              <th><button @click="sortBy('pushed_at')">Last push</button></th>
              <th><button @click="sortBy('starred_at')">Starred</button></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(repo, index) in rows"
              :id="'repo-' + repo.id"
              :key="repo.id"
              :class="{ selected: selected?.id === repo.id }"
              :aria-selected="selected?.id === repo.id"
              @click="selectRepo(repo, index)"
            >
              <td class="name-cell">
                <button class="repo-name" @click.stop="selectRepo(repo, index)">
                  <span class="repo-icon">▱</span
                  ><span
                    ><small>{{ repo.owner }} /</small
                    ><strong>{{ repo.name }}</strong></span
                  ><span v-if="repo.archived" class="tiny-label">archived</span>
                </button>
              </td>
              <td class="description-cell" :title="repo.description || ''">
                {{ repo.description || "No description provided." }}
              </td>
              <td>
                <span class="language"
                  ><i
                    class="language-dot"
                    :data-language="repo.primary_language"
                  ></i
                  >{{ repo.primary_language || "—" }}</span
                >
              </td>
              <td class="numeric">{{ number(repo.stars_count) }}</td>
              <td class="date-cell">{{ date(repo.pushed_at) }}</td>
              <td class="date-cell">{{ date(repo.starred_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="loading" class="empty-state">
          <span class="empty-symbol">◌</span>
          <h2>Opening your library…</h2>
        </div>
        <div v-else class="empty-state">
          <span class="empty-symbol">✳</span
          ><template v-if="stats.starred === 0 && stats.unstarred === 0"
            ><div class="eyebrow">A LITTLE ORDER FOR YOUR CURIOSITY</div>
            <h2>Your stars, within reach.</h2>
            <p>
              Bring your GitHub repositories and Star Lists together.<br />Search,
              revisit, and make them your own — all locally.
            </p>
            <button
              class="primary-button"
              :disabled="sync.last_sync_status === 'running'"
              @click="startSync"
            >
              ↻ Sync your GitHub stars
            </button>
            <p class="setup-hint">
              First, install GitHub CLI and run <code>gh auth login</code>.
            </p></template
          ><template v-else
            ><h2>No repositories match this view.</h2>
            <p>Try a different search or loosen your filters.</p>
            <button class="primary-button" @click="clearFilters">
              Clear all filters
            </button></template
          >
        </div>
      </div>
      <div class="pagination">
        <span
          >{{
            loading
              ? "Updating…"
              : total
                ? `${(view.page - 1) * 50 + 1}–${Math.min(view.page * 50, total)} of ${total.toLocaleString()}`
                : "0 repositories"
          }}<span v-if="view.q"> · Local full-text search</span></span
        >
        <div>
          <button :disabled="view.page <= 1 || loading" @click="view.page--">
            ← Previous</button
          ><span>{{ view.page }} / {{ pages }}</span
          ><button
            :disabled="view.page >= pages || loading"
            @click="view.page++"
          >
            Next →
          </button>
        </div>
      </div>
    </main>

    <aside
      v-if="selected"
      ref="inspector"
      class="inspector"
      tabindex="-1"
      aria-label="Repository details"
    >
      <div class="inspector-bar">
        <span>REPOSITORY DETAILS</span
        ><button @click="closeDetails" aria-label="Close details">×</button>
      </div>
      <div class="inspector-body">
        <div class="detail-owner">{{ selected.owner }} /</div>
        <h2>{{ selected.name }}</h2>
        <div class="badges">
          <span v-if="selected.archived">Archived</span
          ><span v-if="selected.disabled">Disabled</span
          ><span v-if="selected.private">Private</span
          ><span v-if="!selected.is_starred">Previously starred</span>
        </div>
        <p class="detail-description">
          {{ selected.description || "No description provided." }}
        </p>
        <a
          class="primary-button open-github"
          :href="safeUrl(selected.url)"
          target="_blank"
          rel="noopener noreferrer"
          >Open on GitHub <span>↗</span></a
        >
        <a
          v-if="safeUrl(selected.homepage_url)"
          class="homepage"
          :href="safeUrl(selected.homepage_url)"
          target="_blank"
          rel="noopener noreferrer"
          >{{ selected.homepage_url }} ↗</a
        >
        <div class="detail-metrics">
          <div>
            <span>☆</span
            ><strong>{{ selected.stars_count.toLocaleString() }}</strong
            ><small>stars</small>
          </div>
          <div>
            <span>⑂</span
            ><strong>{{ selected.forks_count.toLocaleString() }}</strong
            ><small>forks</small>
          </div>
        </div>
        <dl class="metadata">
          <div>
            <dt>Language</dt>
            <dd>
              <span
                class="language-dot"
                :data-language="selected.primary_language"
              ></span
              >{{ selected.primary_language || "—" }}
            </dd>
          </div>
          <div>
            <dt>Last push</dt>
            <dd>{{ date(selected.pushed_at) }}</dd>
          </div>
          <div>
            <dt>Last update</dt>
            <dd>{{ date(selected.updated_at) }}</dd>
          </div>
          <div>
            <dt>Date starred</dt>
            <dd>{{ date(selected.starred_at) }}</dd>
          </div>
        </dl>
        <div v-if="selected.topics.length" class="detail-section">
          <h3>TOPICS</h3>
          <div class="topics">
            <button
              v-for="topic in selected.topics"
              :key="topic"
              @click="setFilter('topic', topic)"
            >
              {{ topic }}
            </button>
          </div>
        </div>
        <div class="detail-section">
          <h3>GITHUB STAR LISTS</h3>
          <div v-if="selected.lists.length" class="detail-lists">
            <button
              v-for="list in selected.lists"
              :key="list.id"
              @click="library('starred', false, list.id)"
            >
              ☷ {{ list.name }} ↗
            </button>
          </div>
          <p v-else class="muted">No cached list membership.</p>
        </div>
        <form
          class="detail-section annotation-form"
          @submit.prevent="saveAnnotation"
        >
          <div class="section-title">
            <h3>PERSONAL METADATA</h3>
            <span class="local-label">LOCAL ONLY</span>
          </div>
          <label for="review">Review status</label
          ><select id="review" v-model="status">
            <option v-for="item in reviewStatuses" :key="item" :value="item">
              {{ item.charAt(0).toUpperCase() + item.slice(1) }}
            </option></select
          ><label for="notes">Personal notes</label
          ><textarea
            id="notes"
            v-model="notes"
            maxlength="100000"
            rows="6"
            placeholder="Why did this catch your eye? Ideas, reminders, things to try…"
          ></textarea>
          <div class="save-row">
            <span>{{ dirty ? "Unsaved changes" : "Saved on this device" }}</span
            ><button
              class="primary-button"
              :disabled="!dirty || saving"
              type="submit"
            >
              {{ saving ? "Saving…" : "Save notes" }}
            </button>
          </div>
        </form>
        <div class="detail-section">
          <button
            class="readme-button"
            :disabled="readmeLoading"
            @click="fetchReadme"
          >
            {{ showReadme ? "▾" : "▸" }}
            {{ readmeLoading ? "Loading README…" : "View README" }}
            <span>on demand</span>
          </button>
          <p v-if="readmeError" class="inline-error" role="alert">
            {{ readmeError }}
          </p>
          <article
            v-if="showReadme && readme"
            class="markdown"
            v-html="readme"
          ></article>
        </div>
        <div class="detail-section star-management">
          <button
            :disabled="starBusy || sync.last_sync_status === 'running'"
            @click="toggleStar"
          >
            {{
              starBusy
                ? "Contacting GitHub…"
                : selected.is_starred
                  ? "☆ Remove GitHub star"
                  : "★ Star again on GitHub"
            }}</button
          ><small>Personal notes are always preserved.</small>
        </div>
      </div>
    </aside>
    <div v-else class="inspector-placeholder">
      <span>▱</span>
      <p>Select a repository<br /><small>to explore the details</small></p>
      <kbd>↑</kbd> <kbd>↓</kbd><small>Browse your collection</small>
    </div>

    <footer class="statusbar">
      <span
        ><i :class="{ 'running-dot': sync.last_sync_status === 'running' }"></i
        >{{
          sync.last_sync_status === "running"
            ? `${sync.phase} · ${sync.fetched} fetched`
            : sync.last_successful_sync
              ? `Synced ${date(sync.last_successful_sync)} · ${sync.number_of_repositories} repositories${sync.account_login ? " · @" + sync.account_login : ""}`
              : "Your library stays on this device"
        }}</span
      >
      <div>
        <a href="/api/annotations/export" download>Export notes ↗</a
        ><button :disabled="importing" @click="importInput?.click()">
          {{ importing ? "Importing…" : "Import notes" }}</button
        ><button @click="help = !help">? Help</button>
      </div>
      <input
        ref="importInput"
        type="file"
        accept="application/json,.json"
        hidden
        @change="importBackup"
      />
    </footer>
    <section v-if="help" class="shortcut-panel" aria-label="Keyboard shortcuts">
      <div>
        <h3>Make yourself at home.</h3>
        <button aria-label="Close keyboard help" @click="help = false">
          ×
        </button>
      </div>
      <p><kbd>/</kbd> <kbd>⌘ / Ctrl K</kbd><span>Search your library</span></p>
      <p><kbd>↑</kbd> <kbd>↓</kbd><span>Browse repositories</span></p>
      <p><kbd>Enter</kbd><span>Focus details</span></p>
      <p><kbd>Esc</kbd><span>Close details / leave input</span></p>
      <p><kbd>O</kbd><span>Open on GitHub</span></p>
      <p><kbd>?</kbd><span>Toggle this help</span></p>
    </section>
    <div v-if="detailLoading" class="detail-loading" role="status">
      Loading repository…
    </div>
  </div>
</template>
