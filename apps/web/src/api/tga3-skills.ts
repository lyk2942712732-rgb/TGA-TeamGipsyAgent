import { requestJson } from "./client";

export type SkillSummary = { name: string; description: string; file_count: number };
export type SkillMarkdownFile = { path: string; content: string };
export type SkillPackage = SkillSummary & { files: SkillMarkdownFile[] };

const url = (name: string) => `/api/v3/skills/${encodeURIComponent(name)}`;
const json = { "Content-Type": "application/json" };

export const tga3SkillsApi = {
  list: () => requestJson<SkillSummary[]>("/api/v3/skills"),
  read: (name: string) => requestJson<SkillPackage>(url(name)),
  create: (name: string, files: Record<string, string>) => requestJson<SkillPackage>(url(name), {
    method: "POST",
    headers: json,
    body: JSON.stringify({ files }),
  }),
  importArchive: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return requestJson<SkillPackage>("/api/v3/skills/import", { method: "POST", body });
  },
  save: (name: string, files: Record<string, string>) => requestJson<SkillPackage>(url(name), {
    method: "PUT",
    headers: json,
    body: JSON.stringify({ files }),
  }),
  remove: (name: string) => requestJson<void>(url(name), { method: "DELETE" }),
};
