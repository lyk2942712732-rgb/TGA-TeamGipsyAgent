import { describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock("./client", () => mocks);
import { fetchAgentModelOptions } from "./tga3-tasks";

describe("task model options", () => {
  it("keeps enabled choices scoped to their endpoint and rejects disabled defaults", async () => {
    const model = (id: string, enabled = true, available = true) => ({ id, name: id, enabled, available });
    mocks.requestJson.mockImplementation(async (url: string) => url.endsWith("/models") ? {
      providers: [{ id: "gateway", name: "网关", selected_api_key_id: "key", api_keys: [{ id: "key", api_key: "test" }], endpoints: {
        openai_responses: { base_url: "https://api.test/v1", models: [model("enabled"), model("off", false), model("missing", true, false)] },
        anthropic: { base_url: "https://api.test/anthropic", models: [model("claude-only")] },
      } }],
    } : {
      agents: { "worker-openai": { display_name: "Codex", runtime: "codex_cli", role: "worker", provider_id: "gateway", model_id: "off", protocol: "openai_responses" } },
    });
    const result = await fetchAgentModelOptions("penetration_test");
    expect(result.models.map(({ model_id, protocol }) => [model_id, protocol])).toEqual([
      ["enabled", "openai_responses"], ["claude-only", "anthropic"],
    ]);
    expect(result.agents[0].model.ready).toBe(false);
    expect(result.models.every((model) => model.ready)).toBe(true);
  });
});
