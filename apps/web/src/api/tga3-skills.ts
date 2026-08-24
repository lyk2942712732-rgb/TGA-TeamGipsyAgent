import { requestJson } from "./client";

export type SkillSummary = { name: string; description: string };
export type SkillDocument = SkillSummary & { content: string };

const url = (name: string) => `/api/v3/skills/${encodeURIComponent(name)}`;
const json = { "Content-Type": "application/json" };

export const tga3SkillsApi = {
  list: () => requestJson<SkillSummary[]>("/api/v3/skills"),
  read: (name: string) => requestJson<{ name: string; content: string }>(url(name)),
  save: (name: string, content: string) => requestJson<{ name: string; content: string }>(url(name), {
    method: "PUT",
    headers: json,
    body: JSON.stringify({ content }),
  }),
  remove: (name: string) => requestJson<void>(url(name), { method: "DELETE" }),
};
