import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MCPWizard } from "./MCPWizard";

const inspectMCPImage = vi.fn();
const createMCPServer = vi.fn();
const updateMCPServer = vi.fn();

vi.mock("../../runtime/api-v2", () => ({
  runtimeApi: {
    inspectMCPImage: (...args: unknown[]) => inspectMCPImage(...args),
    createMCPServer: (...args: unknown[]) => createMCPServer(...args),
    updateMCPServer: (...args: unknown[]) => updateMCPServer(...args),
  },
}));

describe("MCPWizard edit", () => {
  it("patches an existing connection instead of replacing its complete policy", async () => {
    inspectMCPImage.mockResolvedValue({ image: "binwalk-mcp:latest", local: true, details: {} });
    updateMCPServer.mockResolvedValue({ server: {} });
    const onSaved = vi.fn();

    render(<MCPWizard
      initial={{
        id: "binwalk",
        config: {
          enabled: true,
          transport: "stdio",
          enabledTools: ["scan"],
          stdio: {
            source: "docker_image",
            image: "binwalk-mcp:latest",
            docker: { memory: "1g", cpus: 1, pidsLimit: 256, network: "bridge", readOnly: true },
          },
        },
      }}
      onClose={vi.fn()}
      onSaved={onSaved}
    />);

    fireEvent.click(screen.getByRole("button", { name: "保存连接并继续" }));

    await waitFor(() => expect(updateMCPServer).toHaveBeenCalledWith("binwalk", expect.objectContaining({
      enabled: false,
      transport: "stdio",
      http: null,
    })));
    expect(createMCPServer).not.toHaveBeenCalled();
  });
});
