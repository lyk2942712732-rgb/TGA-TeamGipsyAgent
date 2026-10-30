import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";
describe("Markdown evidence and report content", () => {
  it("renders headings, code and GFM tables without executing embedded markup or loading remote images", () => {
    render(<Markdown content={'# 结果\n\n```python\nprint("ok")\n```\n\n| 证据 | 状态 |\n| --- | --- |\n| 请求 | 已验证 |\n\n<script>alert(1)</script>\n\n![截图](https://external.test/private.png)\n\n[危险链接](javascript:alert(1))'} />);
    expect(screen.getByRole("heading", { name: "结果" })).toBeInTheDocument(); expect(screen.getByRole("table")).toHaveTextContent("已验证");
    expect(document.querySelector("pre")).toHaveTextContent('print("ok")'); expect(document.querySelector("script")).toBeNull(); expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("危险链接")).not.toHaveAttribute("href", expect.stringContaining("javascript:"));
  });
});
