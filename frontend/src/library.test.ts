import { describe, it, expect } from "vitest";
import {
  defaults,
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
