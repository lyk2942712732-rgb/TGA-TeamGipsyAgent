import type { AgentModelChoice } from "../api/tga3-tasks";
import type { ProviderProtocol } from "../api/tga3-config";

type Selection = { provider_id: string; model_id: string; protocol: ProviderProtocol };
const PROTOCOL_NAMES: Record<ProviderProtocol, string> = {
  openai_responses: "OpenAI Responses", openai_chat_completions: "OpenAI Chat Completions",
  anthropic: "Anthropic Messages",
};

export function ModelPicker({ options, value, onChange, label, disabled = false }: {
  options: AgentModelChoice[]; value?: Selection; onChange: (value: Selection) => void;
  label: string; disabled?: boolean;
}) {
  const groups = new Map<string, AgentModelChoice[]>();
  const key = (item: { provider_id: string; model_id: string }) => JSON.stringify([item.provider_id, item.model_id]);
  for (const option of options) {
    const group = groups.get(key(option)) ?? [];
    if (!group.some(item => item.protocol === option.protocol)) group.push(option);
    groups.set(key(option), group);
  }
  const current = value ? groups.get(key(value)) : undefined;
  return <div className="model-picker">
    <select aria-label={label} disabled={disabled || !options.length} value={current && value ? key(value) : ""}
      onChange={event => {
        const choices = groups.get(event.target.value) ?? [];
        const next = choices.find(item => item.ready && item.protocol === value?.protocol) ?? choices.find(item => item.ready);
        if (next) onChange({ provider_id: next.provider_id, model_id: next.model_id, protocol: next.protocol });
      }}>
      <option value="" disabled>请选择已启用模型</option>
      {[...groups].map(([id, choices]) => <option key={id} value={id} disabled={!choices.some(item => item.ready)}>
        {choices[0].provider_name} / {choices[0].model_name}{choices.some(item => item.ready) ? "" : "（密钥不可用）"}
      </option>)}
    </select>
    {current && current.length > 1 ? <select aria-label={`${label} 协议`} disabled={disabled}
      value={current.some(item => item.protocol === value?.protocol) ? value?.protocol : ""}
      onChange={event => {
        const next = current.find(item => item.protocol === event.target.value && item.ready);
        if (next) onChange({ provider_id: next.provider_id, model_id: next.model_id, protocol: next.protocol });
      }}>
      <option value="" disabled>请选择协议</option>
      {current.map(item => <option key={item.protocol} value={item.protocol} disabled={!item.ready}>{PROTOCOL_NAMES[item.protocol]}</option>)}
    </select> : null}
  </div>;
}
