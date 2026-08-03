import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchHostCapabilities: vi.fn(),
  fetchHostCapabilityProfiles: vi.fn(),
  fetchKaliProfiles: vi.fn(),
  fetchSolverDefinitions: vi.fn(),
  fetchSolverManifest: vi.fn(),
  updateSolverCapabilities: vi.fn(),
}));

vi.mock("../api/catalog-query-adapter", async (original) => ({
  ...await original<typeof import("../api/catalog-query-adapter")>(),
  ...mocks,
}));

import { SolversPage } from "./SolversPage";

const solver = {
  id: "ctf-pwn-solver",
  version: "1",
  role: "worker" as const,
  specialties: ["pwn"],
  supported_modes: ["ctf"],
  supported_subtypes: ["pwn"],
  system_prompt_template: "Solve the challenge.",
  default_skill_tags: [],
  required_skill_names: [],
  host_capability_profile_id: "worker-default",
  host_capability_overrides: { add: [], remove: [] },
  host_capabilities: [{
    id: "artifact.inspect",
    display_name: "Inspect artifact",
    category: "artifact",
    risk: "passive",
    source: "worker-default",
  }],
  kali: {
    profile_id: "ctf-pwn-v1",
    capabilities: ["kali.exec" as const],
    image_name: "tga/kali-ctf-pwn",
    image_tag: "2026.08",
    image_digest: null,
    allowed_executables: ["gdb"],
    session_executables: ["gdb"],
    network_mode: "disabled",
    limits: { cpu_cores: 2, memory_mb: 4096, timeout_seconds: 300, max_processes: 256 },
    tools: [],
  },
  accepted_intent_kinds: ["exploit_development"],
  output_contract: { name: "worker-result", required_fields: [] },
  default_budget: {},
  completion_authority: "worker_result",
  content_sha256: "a".repeat(64),
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><SolversPage /></QueryClientProvider>);
}

describe("SolversPage capability editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchSolverDefinitions.mockResolvedValue({ items: [solver], total: 1 });
    mocks.fetchSolverManifest.mockResolvedValue({ host_capabilities: [], kali: null });
    mocks.fetchHostCapabilityProfiles.mockResolvedValue({
      items: [{ id: "worker-default", capability_ids: ["artifact.inspect"] }],
      total: 1,
    });
    mocks.fetchHostCapabilities.mockResolvedValue({
      items: [
        {
          id: "artifact.inspect", display_name: "Inspect artifact", category: "artifact",
          description: "Inspect an artifact.", allowed_roles: ["worker"], risk: "passive",
          input_schema: {}, output_schema: {}, handler_key: "artifact.inspect",
          handler_status: "ready", assigned_solver_count: 1,
          assigned_solver_ids: [solver.id],
        },
        {
          id: "artifact.publish", display_name: "Publish artifact", category: "artifact",
          description: "Publish an artifact.", allowed_roles: ["worker"], risk: "high",
          input_schema: {}, output_schema: {}, handler_key: "artifact.publish",
          handler_status: "ready", assigned_solver_count: 0, assigned_solver_ids: [],
        },
      ],
      total: 2,
    });
    mocks.fetchKaliProfiles.mockResolvedValue({
      items: [{
        id: "ctf-pwn-v1", display_name: "CTF pwn", image_name: "tga/kali-ctf-pwn",
        image_tag: "2026.08", image_digest: null, image: "tga/kali-ctf-pwn:2026.08",
        tools: [], supported_capabilities: ["kali.exec", "kali.session"],
        allowed_executables: ["gdb"], session_executables: ["gdb"], network_mode: "disabled",
        input_mount: "read_only", scratch_mount: "private_read_write",
        shared_artifact_mount: "read_only",
        limits: { cpu_cores: 2, memory_mb: 4096, timeout_seconds: 300, max_processes: 256 },
        enabled: true, assigned_solver_count: 1, assigned_solver_ids: [solver.id],
        config_sha256: "b".repeat(64),
      }],
      total: 1,
    });
    mocks.updateSolverCapabilities.mockResolvedValue(solver);
  });

  it("submits Host overrides with the current Solver revision", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "编辑能力" }));
    await user.click(screen.getByRole("checkbox", { name: /artifact\.inspect/ }));
    await user.click(screen.getByRole("checkbox", { name: /artifact\.publish/ }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(mocks.updateSolverCapabilities).toHaveBeenCalledWith(
      solver.id,
      {
        expected_content_sha256: solver.content_sha256,
        host_capability_profile_id: "worker-default",
        host_capability_overrides: {
          add: ["artifact.publish"],
          remove: ["artifact.inspect"],
        },
        kali: { profile_id: "ctf-pwn-v1", capabilities: ["kali.exec"] },
      },
    ));
  });
});
