import { describe, expect, it } from "vitest";
import { browsePath, corpusCount } from "./meta";

describe("corpusCount", () => {
  it("counts the talks, and agrees with itself about one", () => {
    expect(corpusCount(473)).toBe("473 talks watched");
    expect(corpusCount(1)).toBe("1 talk watched");
  });

  it("says nothing at all about an empty or absent corpus", () => {
    expect(corpusCount(0)).toBeNull();
    expect(corpusCount(null)).toBeNull();
    expect(corpusCount(undefined)).toBeNull();
  });
});

describe("browsePath", () => {
  it("admits the root-relative path the facade sends", () => {
    expect(browsePath("/dashboard")).toBe("/dashboard");
    expect(browsePath("/ops/read_only-2")).toBe("/ops/read_only-2");
  });

  it("refuses anything that could leave this origin", () => {
    // `//host` is a protocol-relative URL, which is where `safeUrl` would have
    // been the wrong tool: it resolves against the document and would take it.
    expect(browsePath("//evil.example/dashboard")).toBeNull();
    expect(browsePath("https://evil.example/dashboard")).toBeNull();
    expect(browsePath("javascript:alert(1)")).toBeNull();
    expect(browsePath("dashboard")).toBeNull();
  });

  it("says nothing when the deployment turned the route group off", () => {
    expect(browsePath(null)).toBeNull();
    expect(browsePath(undefined)).toBeNull();
  });
});
