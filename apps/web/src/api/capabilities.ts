import { runtimeApi } from "./runtime";

export const fetchCapabilities = () => runtimeApi.capabilities();
export const fetchMCPHealth = () => runtimeApi.toolHealth();
