import { describe, expect, it } from "vitest";
import { formatApiErrorDetail, resolveApiBase } from "./client";

describe("resolveApiBase", () => {
  it("prefers the explicit VITE API base", () => {
    expect(resolveApiBase(" https://api.example.com/ ", "http://public.example.com"))
      .toBe("https://api.example.com");
  });

  it("uses the page origin when no API base is configured", () => {
    expect(resolveApiBase(undefined, "https://public.example.com:5173"))
      .toBe("https://public.example.com:5173");
  });

  it("keeps a local fallback for non-browser callers", () => {
    expect(resolveApiBase(undefined, undefined)).toBe("http://127.0.0.1:5173");
  });

  it("renders FastAPI validation issues with their field path", () => {
    expect(formatApiErrorDetail([
      { loc: ["body", "task"], msg: "Field required" },
      { loc: ["body", "input", "hintText"], msg: "String should have at most 16384 characters" },
    ], 422)).toBe("task: Field required；input.hintText: String should have at most 16384 characters");
  });
});
