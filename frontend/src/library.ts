export type ReviewStatus =
  "unreviewed" | "reviewed" | "interesting" | "discarded";
export type ViewState = {
  q: string;
  owner: string;
  language: string;
  topic: string;
  review_status: string;
  list_id: string;
  unclassified: boolean;
  archived: string;
  scope: string;
  sort: string;
  direction: string;
  page: number;
};
export const defaults: ViewState = {
  q: "",
  owner: "",
  language: "",
  topic: "",
  review_status: "",
  list_id: "",
  unclassified: false,
  archived: "",
  scope: "starred",
  sort: "starred_at",
  direction: "desc",
  page: 1,
};
export function parseView(search: string): ViewState {
  const params = new URLSearchParams(search);
  const state = { ...defaults };
  for (const key of [
    "q",
    "owner",
    "language",
    "topic",
    "review_status",
    "list_id",
    "archived",
    "scope",
    "sort",
    "direction",
  ] as const)
    state[key] = params.get(key) ?? defaults[key];
  state.q = state.q.slice(0, 500);
  state.owner = state.owner.slice(0, 100);
  state.unclassified = params.get("unclassified") === "true";
  state.page = Math.max(
    1,
    Math.min(1_000_000, Math.trunc(Number(params.get("page"))) || 1),
  );
  if (!["starred", "unstarred", "all"].includes(state.scope))
    state.scope = defaults.scope;
  if (
    ![
      "name",
      "stars",
      "starred_at",
      "pushed_at",
      "last_commit_at",
      "updated_at",
      "relevance",
    ].includes(state.sort)
  )
    state.sort = defaults.sort;
  if (!["asc", "desc"].includes(state.direction))
    state.direction = defaults.direction;
  if (!["", "true", "false"].includes(state.archived)) state.archived = "";
  if (
    !["", "unreviewed", "reviewed", "interesting", "discarded"].includes(
      state.review_status,
    )
  )
    state.review_status = "";
  return state;
}
export function viewQuery(state: ViewState): string {
  const params = new URLSearchParams();
  for (const key of Object.keys(defaults) as (keyof ViewState)[]) {
    if (state[key] !== defaults[key]) params.set(key, String(state[key]));
  }
  return params.toString();
}
export function ownerView(state: ViewState, owner: string): ViewState {
  // Show the whole local owner's collection so the detail count matches the view.
  return {
    ...defaults,
    owner,
    scope: "all",
    sort: state.sort,
    direction: state.direction,
  };
}
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return (
    !!el &&
    (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
      !!el.isContentEditable)
  );
}
export function nextIndex(index: number, key: string, length: number): number {
  if (!length) return -1;
  return Math.max(
    0,
    Math.min(length - 1, index + (key === "ArrowDown" ? 1 : -1)),
  );
}
export interface Repo {
  id: number;
  full_name: string;
  name: string;
  owner: string;
  description: string | null;
  url: string;
  homepage_url: string | null;
  primary_language: string | null;
  stars_count: number;
  forks_count: number;
  topics: string[];
  archived: boolean;
  disabled: boolean;
  private: boolean;
  is_starred: boolean;
  starred_at: string | null;
  pushed_at: string | null;
  last_commit_at: string | null;
  default_branch: string | null;
  updated_at: string | null;
  review_status?: ReviewStatus;
}
export interface Detail extends Repo {
  other_owner_repositories_count: number;
  annotation: { personal_notes: string; review_status: ReviewStatus };
  lists: { id: string; name: string }[];
}
export const safeUrl = (value: string | null): string | undefined => {
  try {
    const url = new URL(value || "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
};
