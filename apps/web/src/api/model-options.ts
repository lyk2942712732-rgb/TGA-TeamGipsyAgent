import type { ModelsConfig, ProviderProtocol } from "./tga3-config";

export function enabledModelOptions(models: ModelsConfig | null) {
  return (models?.providers ?? []).flatMap((provider) =>
    Object.entries(provider.endpoints).flatMap(([protocol, endpoint]) =>
      endpoint.models.filter((model) => model.enabled && model.available).map((model) => ({
        provider, model, protocol: protocol as ProviderProtocol,
      })),
    ),
  );
}
