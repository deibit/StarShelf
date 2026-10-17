import { describe, it, expect } from "vitest";
import {
  defaults,
  ownerView,
  parseView,
  viewQuery,
  nextIndex,
  isTyping,
  safeUrl,
} from "./library";
describe("bookmarkable filters", () => {
  it("round-trips combined filters and pagination", () => {
    const state = {
      ...defaults,
      q: "sqlite notes",
      list_id: "UL/a+",
      language: "TypeScript",
      review_status: "interesting",
      scope: "all",
      page: 4,
    };
    expect(parseView(viewQuery(state))).toEqual(state);
  });
  it("validates URL controls", () => {
    const state = parseView(
      "?sort=DROP&direction=bad&page=-5&scope=invalid&review_status=nope",
    );
    expect(state).toEqual(defaults);
  });
  it("omits defaults", () => expect(viewQuery({ ...defaults })).toBe(""));
});
describe("keyboard navigation", () => {
  it("clamps arrows at both ends and handles empty results", () => {
    expect(nextIndex(-1, "ArrowDown", 10)).toBe(0);
    expect(nextIndex(9, "ArrowDown", 10)).toBe(9);
    expect(nextIndex(0, "ArrowUp", 10)).toBe(0);
    expect(nextIndex(-1, "ArrowDown", 0)).toBe(-1);
  });
  it("protects editable controls", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"])
      expect(isTyping({ tagName } as unknown as EventTarget)).toBe(true);
    expect(
      isTyping({
        tagName: "DIV",
        isContentEditable: true,
      } as unknown as EventTarget),
    ).toBe(true);
    expect(isTyping({ tagName: "BUTTON" } as unknown as EventTarget)).toBe(
      false,
    );
  });
  it("rejects executable repository URLs", () => {
    expect(safeUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeUrl("https://github.com/vuejs/core")).toBe(
      "https://github.com/vuejs/core",
    );
  });
});

describe("activity date sorting", () => {
  it("preserves independent commit and activity sorts in bookmarked views", () => {
    for (const sort of ["last_commit_at", "pushed_at"]) {
      const view = { ...defaults, sort, direction: "asc", page: 3 };
      expect(parseView(viewQuery(view))).toEqual(view);
    }
  });
});

describe("owner navigation", () => {
  it("shows the whole owner's local collection and resets conflicting filters and page", () => {
    const current = {
      ...defaults,
      q: "unrelated",
      owner: "old",
      list_id: "L1",
      language: "Rust",
      review_status: "reviewed",
      archived: "true",
      page: 5,
      sort: "name",
      direction: "asc",
    };
    const next = ownerView(current, "octocat");
    expect(next).toEqual({
      ...defaults,
      owner: "octocat",
      scope: "all",
      sort: "name",
      direction: "asc",
    });
    expect(parseView(viewQuery(next))).toEqual(next);
    expect(current.q).toBe("unrelated");
  });
  it("combines owner with other filters in a bookmarked URL", () => {
    const view = {
      ...defaults,
      owner: "VueJS",
      language: "TypeScript",
      topic: "frontend",
      page: 2,
    };
    expect(parseView(viewQuery(view))).toEqual(view);
    expect(viewQuery({ ...view, owner: "" })).not.toContain("owner=");
  });
});
