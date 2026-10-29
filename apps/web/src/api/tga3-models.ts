import { requestJson } from "./client";
import type { ModelsConfig, ProviderEndpoints, ProviderProtocol, TGA3Model, TGA3Provider } from "./tga3-config";

export type ProviderPreset = { id: string; name: string; endpoints: ProviderEndpoints; default_protocol: ProviderProtocol };
export type ProviderAPIKey = { id: string; label: string; masked: string; selected: boolean; configured: boolean };
export type ProviderModel = TGA3Model;
export type ModelProvider = Omit<TGA3Provider, "api_keys"> & { api_keys: ProviderAPIKey[] };
export type ProviderCatalog = { schema_version: number; presets: ProviderPreset[]; providers: ModelProvider[] };

const jsonHeaders = { "Content-Type": "application/json" };
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `provider-${Date.now()}`;
const mask = (value: string) => value.length < 8 ? "••••••••" : `${value.slice(0, 3)}••••${value.slice(-4)}`;
const loadModels = () => requestJson<ModelsConfig>("/api/v3/config/models");
const saveModels = (value: ModelsConfig) => requestJson<ModelsConfig>("/api/v3/config/models", { method: "PUT", headers: jsonHeaders, body: JSON.stringify(value) });
const providerPath = (id: string) => `/api/v3/config/models/${encodeURIComponent(id)}`;

function providerView(provider: TGA3Provider): ModelProvider {
  return {
    ...provider,
    api_keys: provider.api_keys.map((key) => ({
      id: key.id, label: key.label, masked: key.api_key ? mask(key.api_key) : "未设置",
      selected: key.id === provider.selected_api_key_id, configured: Boolean(key.api_key),
    })),
  };
}

export async function fetchProviderCatalog(): Promise<ProviderCatalog> {
  const [models, presets] = await Promise.all([
    loadModels(), requestJson<ProviderPreset[]>("/api/v3/config/model-provider-presets"),
  ]);
  return { schema_version: models.schema_version, presets, providers: models.providers.map(providerView) };
}

export async function createModelProvider(payload: {
  name: string; preset_id?: string; endpoints: ProviderEndpoints; api_key: string;
}): Promise<{ provider: ModelProvider; warnings: string[] }> {
  if (!Object.keys(payload.endpoints).length) throw new Error("至少填写一个协议端点");
  const models = await loadModels();
  const id = slug(payload.name);
  if (models.providers.some((provider) => provider.id === id)) throw new Error("供应商名称已存在");
  const keyId = `${id}-primary`;
  models.providers.push({
    id, name: payload.name, preset_id: payload.preset_id ?? "custom", built_in: false,
    endpoints: payload.endpoints,
    api_keys: [{ id: keyId, label: "Primary", api_key: payload.api_key }], selected_api_key_id: keyId,
  });
  await saveModels(models);
  const warnings = await discoverAllEndpoints(id, payload.endpoints);
  const refreshed = await loadModels();
  return { provider: providerView(refreshed.providers.find((provider) => provider.id === id)!), warnings };
}

export async function deleteModelProvider(providerId: string): Promise<void> {
  await requestJson<void>(providerPath(providerId), { method: "DELETE" });
}

export async function discoverProviderModels(providerId: string, protocol: ProviderProtocol): Promise<{ count: number }> {
  return requestJson(`${providerPath(providerId)}/discover?protocol=${protocol}`, { method: "POST" });
}

async function discoverAllEndpoints(providerId: string, endpoints: ProviderEndpoints): Promise<string[]> {
  const results = await Promise.allSettled(
    (Object.keys(endpoints) as ProviderProtocol[]).map((protocol) => discoverProviderModels(providerId, protocol)),
  );
  return results.flatMap((result) => result.status === "rejected" ? [String(result.reason instanceof Error ? result.reason.message : result.reason)] : []);
}

export async function updateProviderEndpoints(providerId: string, endpoints: ProviderEndpoints): Promise<void> {
  if (!Object.keys(endpoints).length) throw new Error("至少填写一个协议端点");
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider) throw new Error("供应商不存在");
  provider.endpoints = endpoints;
  await saveModels(models);
}

export async function setProviderModelsEnabled(providerId: string, protocol: ProviderProtocol, modelIds: string[], enabled: boolean): Promise<void> {
  await requestJson(`${providerPath(providerId)}/endpoints/${protocol}/models`, {
    method: "PATCH", headers: jsonHeaders, body: JSON.stringify({ model_ids: modelIds, enabled }),
  });
}

export async function addProviderAPIKey(providerId: string, payload: { api_key: string; label?: string }): Promise<{ warnings: string[] }> {
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider) throw new Error("供应商不存在");
  const key = { id: `${providerId}-${Date.now()}`, label: payload.label || "Key", api_key: payload.api_key };
  provider.api_keys.push(key);
  provider.selected_api_key_id = key.id;
  await saveModels(models);
  return { warnings: await discoverAllEndpoints(providerId, provider.endpoints) };
}

export async function selectProviderAPIKey(providerId: string, keyId: string): Promise<{ warnings: string[] }> {
  const models = await loadModels();
  const provider = models.providers.find((item) => item.id === providerId);
  if (!provider?.api_keys.some((key) => key.id === keyId)) throw new Error("密钥不存在");
  provider.selected_api_key_id = keyId;
  await saveModels(models);
  return { warnings: await discoverAllEndpoints(providerId, provider.endpoints) };
}
