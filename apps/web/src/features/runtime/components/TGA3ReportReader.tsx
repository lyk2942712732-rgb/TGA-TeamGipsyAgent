import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import { ErrorState } from "../../../components/ui/ErrorState";
import { LoadingSkeleton } from "../../../components/ui/LoadingSkeleton";
import { Markdown } from "../../../components/ui/Markdown";

export function TGA3ReportReader({ taskId }: { taskId: string }) {
  const report = useQuery({ queryKey: ["tga3", "report-body", taskId], queryFn: async () => {
    const response = await fetch(tga3RuntimeApi.reportUrl(taskId));
    if (!response.ok) throw new Error(`报告读取失败（${response.status}）`);
    return response.text();
  } });
  if (report.isPending) return <LoadingSkeleton label="正在读取解题报告" rows={8} />;
  if (report.isError) return <ErrorState description={report.error.message} actionLabel="重试" onAction={() => void report.refetch()} />;
  return <article className="workbench-report"><div className="workbench-report-meta"><span>Reporter / Markdown Writeup</span><a className="ref-secondary-button" href={tga3RuntimeApi.reportUrl(taskId)}><Download size={14} />下载 Markdown</a></div><Markdown content={report.data} /></article>;
}
