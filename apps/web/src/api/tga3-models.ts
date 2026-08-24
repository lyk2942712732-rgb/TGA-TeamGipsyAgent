import { requestJson } from "./client";
import type { ModelsConfig, ProviderProtocol, TGA3Provider } from "./tga3-config";

export type ProviderPreset = {
  id: string;
  name: string;
  base_url: string;
  protocols: ProviderProtocol[];
  default_protocol: ProviderProtocol;
};

export type ProviderAPIKey = {
  id: string;
  label: string;
  masked: string;
  selected: boolean;
  configured: boolean;
};

export type ProviderModel = {
  id: string;
  name: string;
  max_output_tokens: number;
  timeout_seconds: number;
  temperature: number;
  reasoning_mode: "auto" | "enabled" | "disabled";
  verification_status: "unverified" | "verifying" | "verified" | "failed" | "stale";
};

export type ModelProvider = {
  id: string;
  name: string;
  preset_id: string;
  built_in: boolean;
  protocols: ProviderProtocol[];
  base_url: string;
  models: ProviderModel[];
  api_keys: ProviderAPIKey[];
  selected_api_key_id?: string | null;
};

export type ProviderCatalog = { schema_version: number; presets: ProviderPreset[]; providers: ModelProvider[] };

const jsonHeaders = { "Content-Type": "application/json" };
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `provider-${Date.now()}`;
const mask = (value: string) => value.length < 8 ? "••••••••" : `${value.slice(0, 3)}••••${value.slice(-4)}`;
const loadModels = () => requestJson<ModelsConfig>("/api/v3/config/models");
const saveModels = (value: ModelsConfig) => requestJson<ModelsConfig>("/api/v3/config/models", { method: "PUT", headers: jsonHeaders, body: JSON.stringify(value) });

function providerView(provider: TGA3Provider): ModelProvider {
  return {
    id: provider.id,
    name: provider.name,
    preset_id: provider.preset_id ?? "custom",
    built_in: provider.built_in ?? false,
    protocols: provider.protocols,
    base_url: provider.base_url ?? "",
    selected_api_key_id: provider.selected_api_key_id,
    models: provider.models.map((model) => ({
      id: model.id,
      name: model.name,
      max_output_tokens: model.max_output_tokens ?? 8192,
      timeout_seconds: model.timeout_seconds ?? 180,
      temperature: model.temperature ?? 0,
      reasoning_mode: model.reasoning_mode ?? "auto",
      verification_status: model.verification_status ?? "unverified",
    })),
    api_keys: provider.api_keys.map((key) => ({
      id: key.id,
      label: key.label,
      masked: key.api_key ? mask(key.api_key) : "未设置",
      selected: key.id === provider.selected_api_key_id,
      configured: Boolean(key.api_key),
    })),
  };
}

export async function fetchProviderCatalog(): Promise<ProviderCatalog> {
  const [models, presets] = await Promise.all([
    loadModels(),
    requestJson<ProviderPreset[]>("/api/v3/config/model-provider-presets"),
  ]);
  return { schema_version: models.schema_version, presets, providers: models.providers.map(providerView) };
}

export async function createModelProvider(payload: {
  name: string;
  preset_id?: string;
  protocols: ProviderProtocol[];
  base_url: string;
  api_key: string;
  api_key_label?: string;
}): Promise<{ provider: ModelProvider }> {
  const models = await loadModels();
  const id = slug(payload.name);
  if (models.providers.some((provider) => provider.id === id)) throw new Error("供应商名称已存在");
  const keyId = `${id}-primary`;
  models.providers.push({
    id,
    name: payload.name,
    preset_id: payload.preset_id ?? "custom",
    built_in: false,
    protocols: payload.protocols,
    base_url: payload.base_url,
    api_keys: [{ id: keyId, label: payload.api_key_label ?? "Primary", api_key: payload.api_key }],
    selected_api_key_id: keyId,
    models: [],
  });
  await saveModels(models);
  await discoverProviderModels(id);
  const refreshed = await loadModels();
  return { provider: providerView(refreshed.providers.find((provider) => provider.id === id)!) };
}

export async function deleteModelProvider(providerId: string): Promise<void> {
  await requestJson<void>(`/api/v3/config/models/${encodeURIComponent(providerId)}`, { method: "DELETE" });
}

export async function discoverProviderModels(providerId: string): Promise<{ provider_id: string; count: number; models: ProviderModel[] }> {
  return requestJson(`/api/v3/config/models/${encodeURIComponent(providerId)}/discover`, { method: "POST" });
}

export async function updateProviderEndpoint(providerId: string, baseUrl: string): Promise<{ provider: ModelProvider }> {
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider) throw new Error("供应商不存在");
  provider.base_url = baseUrl;
  await saveModels(models);
  await discoverProviderModels(providerId);
  const refreshed = await loadModels();
  return { provider: providerView(refreshed.providers.find((item) => item.id === providerId)!) };
}

export async function addProviderAPIKey(providerId: string, payload: { api_key: string; label?: string }): Promise<{ api_key: ProviderAPIKey }> {
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider) throw new Error("供应商不存在");
  const key = { id: `${providerId}-${Date.now()}`, label: payload.label ?? "Key", api_key: payload.api_key };
  provider.api_keys.push(key);
  provider.selected_api_key_id = key.id;
  await saveModels(models);
  await discoverProviderModels(providerId);
  const refreshed = await loadModels();
  const keys = providerView(refreshed.providers.find((item) => item.id === providerId)!).api_keys;
  return { api_key: keys.find((item) => item.id === key.id)! };
}

export async function selectProviderAPIKey(providerId: string, keyId: string): Promise<{ provider: ModelProvider }> {
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider?.api_keys.some((key) => key.id === keyId)) throw new Error("密钥不存在");
  provider.selected_api_key_id = keyId;
  await saveModels(models);
  await discoverProviderModels(providerId);
  const refreshed = await loadModels();
  return { provider: providerView(refreshed.providers.find((item) => item.id === providerId)!) };
}
