import { Download, ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

export function ArtifactPreview({ title, kind, metadata, children, truncated = false, previewUrl, downloadUrl }: { title: string; kind: string; metadata?: ReactNode; children?: ReactNode; truncated?: boolean; previewUrl?: string; downloadUrl?: string }) {
  return <article className="artifact-preview-v2"><header><div><span>{kind}</span><h3>{title}</h3></div><nav>{previewUrl ? <a href={previewUrl} target="_blank" rel="noreferrer" aria-label="打开预览"><ExternalLink size={15} /></a> : null}{downloadUrl ? <a href={downloadUrl} aria-label="下载"><Download size={15} /></a> : null}</nav></header>{metadata ? <div className="artifact-preview-meta">{metadata}</div> : null}<div className="artifact-preview-body">{children ?? "暂无可预览内容"}</div>{truncated ? <footer>预览已截断，请按需读取或下载原始文件。</footer> : null}</article>;
}
