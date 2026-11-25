import { useQuery } from "@tanstack/react-query";
import { BookOpen, Download, ExternalLink, FileText, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiBase, requestJson } from "../api/client";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { ExperienceCards } from "../features/runtime/components/ExperienceCards";
import { TGA3ReportReader } from "../features/runtime/components/TGA3ReportReader";
import { sceneLabel } from "../modes";
import type { AOEOverview } from "../runtime/tga3-runtime";

type ReportRow = {
  id: string;
  taskId: string;
  title: string;
  taskName: string;
  scene: string;
  updatedAt: string;
  parentTaskId?: string;
};

type TaskRef = { id: string; title: string; scene_id: string; updated_at: string };

async function fetchReportsData(): Promise<{ reports: ReportRow[]; tasks: TaskRef[] }> {
  const tasks = await requestJson<TaskRef[]>("/api/v3/tasks");
  const expanded = (
    await Promise.all(
      tasks.map(async (task) => {
        if (task.scene_id !== "aoe") {
          return [{ ...task, reportTitle: `${task.title} Writeup`, parentTaskId: undefined as string | undefined }];
        }
        try {
          const overview = await requestJson<AOEOverview>(`/api/v3/tasks/${encodeURIComponent(task.id)}/questions`);
          return overview.questions
            .filter((question) => question.report_state === "completed")
            .map((question) => ({
              ...task,
              id: question.question_id,
              title: task.title,
              reportTitle: `${question.title} Writeup`,
              parentTaskId: task.id,
            }));
        } catch {
          return [];
        }
      }),
    )
  ).flat();

  const rows = await Promise.all(
    expanded.map(async (task): Promise<ReportRow | null> => {
      try {
        const writeup = await requestJson<{ id: string; created_at: string }>(
          `/api/v3/tasks/${encodeURIComponent(task.id)}/writeup`,
        );
        return {
          id: writeup.id,
          taskId: task.id,
          title: task.reportTitle,
          taskName: task.title,
          scene: task.scene_id,
          updatedAt: writeup.created_at,
          parentTaskId: task.parentTaskId,
        };
      } catch {
        return null;
      }
    }),
  );
  return {
    reports: rows.filter((row): row is ReportRow => row !== null),
    tasks,
  };
}

export function ReportsPage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ["tga3", "reports"], queryFn: fetchReportsData });
  const [vaultMode, setVaultMode] = useState<"writeups" | "experience">("writeups");
  const [selectedId, setSelectedId] = useState<string>("");
  const [expTaskId, setExpTaskId] = useState<string>("");
  const [search, setSearch] = useState("");

  const reports = query.data?.reports ?? [];
  const tasks = query.data?.tasks ?? [];

  const visibleReports = useMemo(
    () =>
      reports.filter((row) =>
        `${row.title} ${row.taskName} ${sceneLabel(row.scene)}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [reports, search],
  );

  useEffect(() => {
    if (!visibleReports.length) return;
    if (!selectedId || !visibleReports.some((r) => r.id === selectedId)) {
      setSelectedId(visibleReports[0].id);
    }
  }, [visibleReports, selectedId]);

  useEffect(() => {
    if (!tasks.length) return;
    if (!expTaskId || !tasks.some((t) => t.id === expTaskId)) {
      setExpTaskId(tasks[0].id);
    }
  }, [tasks, expTaskId]);

  const selected = visibleReports.find((r) => r.id === selectedId) ?? visibleReports[0] ?? null;

  const grouped = useMemo(() => {
    const map = new Map<string, ReportRow[]>();
    for (const row of visibleReports) {
      const key = row.parentTaskId ? `${row.taskName} (AOE)` : "单题报告";
      const list = map.get(key) ?? [];
      list.push(row);
      map.set(key, list);
    }
    return [...map.entries()];
  }, [visibleReports]);

  return (
    <div className="ref-page vault-page">
      <header className="ref-page-head">
        <div>
          <h1>复盘与经验库</h1>
          <p>沉浸式阅读 Reporter 生成的 Writeup，并按任务浏览沉淀的经验卡。</p>
        </div>
        <div className="button-row">
          <div className="tga3-segment" role="tablist" aria-label="复盘库视图">
            <button
              type="button"
              role="tab"
              aria-selected={vaultMode === "writeups"}
              onClick={() => setVaultMode("writeups")}
            >
              <FileText size={13} /> Writeup 报告 ({reports.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={vaultMode === "experience"}
              onClick={() => setVaultMode("experience")}
            >
              <BookOpen size={13} /> 经验卡库
            </button>
          </div>
          {vaultMode === "writeups" && selected ? (
            <a
              className="ref-secondary-button"
              href={`${apiBase}/api/v3/tasks/${encodeURIComponent(selected.taskId)}/writeup/download`}
            >
              <Download size={13} /> 下载 Markdown
            </a>
          ) : null}
        </div>
      </header>

      {query.isLoading ? (
        <LoadingSkeleton label="正在读取报告与经验库" rows={6} />
      ) : query.isError ? (
        <ErrorState
          description={query.error instanceof Error ? query.error.message : "无法读取报告"}
          actionLabel="重试"
          onAction={() => void query.refetch()}
        />
      ) : vaultMode === "writeups" ? (
        reports.length ? (
          <div className="vault-split-layout ref-fill">
            <aside className="vault-index-pane">
              <div className="vault-index-search">
                <label className="ref-search">
                  <Search size={14} />
                  <input
                    aria-label="搜索报告"
                    placeholder="搜索报告或任务"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
              </div>
              <div className="vault-index-scroll">
                {grouped.map(([groupName, items]) => (
                  <div key={groupName} className="vault-group">
                    <div className="vault-group-label">{groupName}</div>
                    {items.map((row) => (
                      <button
                        key={row.id}
                        type="button"
                        className={`vault-index-item${selected?.id === row.id ? " is-active" : ""}`}
                        onClick={() => setSelectedId(row.id)}
                      >
                        <strong>{row.title}</strong>
                        <small>
                          {sceneLabel(row.scene)} · {new Date(row.updatedAt).toLocaleDateString("zh-CN")}
                        </small>
                      </button>
                    ))}
                  </div>
                ))}
                {!visibleReports.length ? <EmptyState label="没有匹配的报告" /> : null}
              </div>
            </aside>

            <section className="vault-reader-pane">
              {selected ? (
                <>
                  <header className="vault-reader-head">
                    <div>
                      <h2>{selected.title}</h2>
                      <small>
                        任务：{selected.taskName} · {sceneLabel(selected.scene)} ·{" "}
                        {new Date(selected.updatedAt).toLocaleString("zh-CN")}
                      </small>
                    </div>
                    <button
                      type="button"
                      className="ref-secondary-button"
                      onClick={() =>
                        navigate(
                          selected.parentTaskId
                            ? `/tasks/${encodeURIComponent(selected.parentTaskId)}/runtime?question=${encodeURIComponent(selected.taskId)}&tab=report`
                            : `/tasks/${encodeURIComponent(selected.taskId)}/runtime`,
                        )
                      }
                    >
                      <ExternalLink size={13} /> 进入作战室
                    </button>
                  </header>
                  <div className="vault-reader-body">
                    <TGA3ReportReader taskId={selected.taskId} />
                  </div>
                </>
              ) : (
                <EmptyState title="请选择一份报告" description="点击左侧目录阅读对应 Writeup。" />
              )}
            </section>
          </div>
        ) : (
          <EmptyState title="还没有报告" description="Reporter 完成解题总结后，这里会出现对应的 Writeup。" />
        )
      ) : tasks.length ? (
        <div className="vault-split-layout ref-fill">
          <aside className="vault-index-pane">
            <div className="vault-group-label">按任务浏览经验卡 ({tasks.length})</div>
            <div className="vault-index-scroll">
              {tasks.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  className={`vault-index-item${expTaskId === task.id ? " is-active" : ""}`}
                  onClick={() => setExpTaskId(task.id)}
                >
                  <strong>{task.title}</strong>
                  <small>
                    {sceneLabel(task.scene_id)} · #{task.id.slice(0, 8)}
                  </small>
                </button>
              ))}
            </div>
          </aside>
          <section className="vault-reader-pane">
            <div className="vault-reader-body">
              {expTaskId ? <ExperienceCards taskId={expTaskId} /> : null}
            </div>
          </section>
        </div>
      ) : (
        <EmptyState title="暂无任务经验卡" description="完成解题后，Reporter 提炼的经验卡会集中沉淀在这里。" />
      )}
    </div>
  );
}
