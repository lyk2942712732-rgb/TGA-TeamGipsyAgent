import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { AgentModelChoice } from "../api/tga3-tasks";
import { ModelPicker } from "./ModelPicker";

const choices: AgentModelChoice[] = [
  { provider_id: "both", provider_name: "Both", model_id: "shared", model_name: "Shared", protocol: "anthropic", ready: true },
  { provider_id: "both", provider_name: "Both", model_id: "shared", model_name: "Shared", protocol: "openai_chat_completions", ready: true },
  { provider_id: "other", provider_name: "Other", model_id: "single", model_name: "Single", protocol: "openai_responses", ready: true },
];
function Harness() {
  const [value, setValue] = useState(choices[0]);
  return <><ModelPicker label="模型" options={choices} value={value} onChange={next => setValue({ ...value, ...next })} />
    <output>{value.provider_id}/{value.model_id}/{value.protocol}</output></>;
}
describe("ModelPicker", () => {
  it("groups the same model and explicitly changes protocol without losing endpoint identity", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(within(screen.getByLabelText("模型")).getAllByRole("option", { name: "Both / Shared" })).toHaveLength(1);
    expect(screen.getByLabelText("模型 协议")).toHaveValue("anthropic");
    await user.selectOptions(screen.getByLabelText("模型 协议"), "openai_chat_completions");
    expect(screen.getByRole("status")).toHaveTextContent("both/shared/openai_chat_completions");
    await user.selectOptions(screen.getByLabelText("模型"), JSON.stringify(["other", "single"]));
    expect(screen.queryByLabelText("模型 协议")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("other/single/openai_responses");
  });
});
