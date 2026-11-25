import { useQuery } from "@tanstack/react-query";
import { Copy, Plus, RotateCcw, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { deleteTask, listTasks, type TGA3TaskListItem } from "../api/tga3-tasks";
import { Pagination, usePage } from "../components/ui/CatalogTable";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { sceneLabel, TASK_MODES } from "../modes";
import { statusLabel } from "../shared/status";

const STATES = ["created", "scouting", "starting", "running", "finalizing", "reporting", "completed", "failed", "cancelled"];
const STATE_TONES: Record<string, string> = {
  scouting: "tone-info",
  running: "tone-accent",
  completed: "tone-ok",
  finalizing: "tone-info",
  reporting: "tone-info",
  failed: "tone-danger",
  cancelled: "tone-muted",
  starting: "tone-info",
  created: "tone-muted",
};

function taskStateBadge(task: TGA3TaskListItem): { label: string; tone: string; progress: number } {
  const meta = (task as TGA3TaskListItem & { metadata?: Record<string, unknown> }).metadata;
  const aoePhase = typeof meta?.aoe_phase === "string" ? meta.aoe_phase : "";
  if (task.scene_id === "aoe" && task.state === "running") {
    if (aoePhase === "awaiting_finish_confirm") return { label: "待确认结束", tone: "tone-warn", progress: 90 };
    if (aoePhase === "standby") return { label: "站岗候题", tone: "tone-info", progress: 85 };
  }
  const progress =
    task.state === "completed"
      ? 100
      : task.state === "reporting" || task.state === "finalizing"
        ? 85
        : task.state === "running"
          ? 55
          : task.state === "scouting" || task.state === "starting"
            ? 25
            : task.state === "failed"
              ? 35
              : 10;
  return { label: statusLabel(task.state), tone: STATE_TONES[task.state] ?? "tone-muted", progress };
}

export function TaskListPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [removeTask, setRemoveTask] = useState<TGA3TaskListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [message, setMessage] = useState("");
  const query = params.get("query") ?? "";
  const scene = params.get("scene") ?? "";
  const state = params.get("state") ?? "";

  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const allRows = tasks.data ?? [];
  const rows = useMemo(
    () =>
      allRows.filter(
        (task) =>
          (!query || `${task.title} ${task.id}`.toLowerCase().includes(query.toLowerCase())) &&
          (!scene || task.scene_id === scene) &&
          (!state ||
            (state === "active"
              ? ["scouting", "starting", "running", "finalizing", "reporting"].includes(task.state)
              : task.state === state)),
      ),
    [allRows, query, scene, state],
  );
  const visible = usePage(rows, pageSize, page);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
    setPage(1);
  };
  const open = (task: TGA3TaskListItem) => navigate(`/tasks/${encodeURIComponent(task.id)}/runtime`);
  const openRestart = (task: TGA3TaskListItem) => navigate(`/tasks/${encodeURIComponent(task.id)}/runtime?restart=1`);
  const openClone = (task: TGA3TaskListItem) => navigate(`/tasks/new?clone=${encodeURIComponent(task.id)}`);
  const isTerminal = (task: TGA3TaskListItem) => ["completed", "failed", "cancelled", "stopped"].includes(task.state);

  async function confirmDelete() {
    if (!removeTask || deleting) return;
    setDeleting(true);
    setMessage("");
    try {
      await deleteTask(removeTask.id);
      setRemoveTask(null);
      setMessage(`任务“${removeTask.title}”已彻底删除。`);
      await tasks.refetch();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "删除任务失败");
    } finally {
      setDeleting(false);
    }
  }

  const activeCount = allRows.filter((t) => ["scouting", "starting", "running", "finalizing", "reporting"].includes(t.state)).length;
  const doneCount = allRows.filter((t) => t.state === "completed").length;
  const errCount = allRows.filter((t) => t.state === "failed").length;

  return (
    <div className="ref-page">
      <div className="page-frame is-lg">
        <div className="table-toolbar">
        <div className="table-toolbar-left">
          <h1 style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>任务调度档案</h1>
          <div className="proto-seg" role="group" aria-label="快捷状态筛选">
            <button type="button" className={!state ? "is-active" : ""} onClick={() => update("state", "")}>
              全部 ({allRows.length})
            </button>
            <button type="button" className={state === "active" ? "is-active" : ""} onClick={() => update("state", "active")}>
              运行中 ({activeCount})
            </button>
            <button type="button" className={state === "completed" ? "is-active" : ""} onClick={() => update("state", "completed")}>
              已完成 ({doneCount})
            </button>
            <button type="button" className={state === "failed" ? "is-active" : ""} onClick={() => update("state", "failed")}>
              异常 ({errCount})
            </button>
          </div>
          <select
            aria-label="场景筛选"
            value={scene}
            onChange={(event) => update("scene", event.target.value)}
            style={{ height: 28, padding: "0 8px", border: "1px solid var(--c-border)", borderRadius: "var(--r-md)", background: "var(--c-input-bg)", fontSize: "12px" }}
          >
            <option value="">所有场景</option>
            {TASK_MODES.map((value) => (
              <option key={value} value={value}>
                {sceneLabel(value)}
              </option>
            ))}
          </select>
          <select
            aria-label="状态筛选"
            value={STATES.includes(state) ? state : ""}
            onChange={(event) => update("state", event.target.value)}
            style={{ height: 28, padding: "0 8px", border: "1px solid var(--c-border)", borderRadius: "var(--r-md)", background: "var(--c-input-bg)", fontSize: "12px" }}
          >
            <option value="">精细状态</option>
            {STATES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
        </div>

        <div className="table-toolbar-right">
          <label className="ref-search" style={{ width: 220, height: 28, minHeight: 28 }}>
            <Search size={14} />
            <input
              aria-label="搜索任务"
              placeholder="搜索任务名称或 ID"
              value={query}
              onChange={(event) => update("query", event.target.value)}
            />
          </label>
          <button type="button" className="ref-primary-button" onClick={() => navigate("/tasks/new")}>
            <Plus size={14} /> 创建任务
          </button>
        </div>
      </div>

      {message ? (
        <p className="settings-message" style={{ borderRadius: 0, borderLeft: "none", borderRight: "none" }} role="status">
          {message}
        </p>
      ) : null}

      <div className="proto-table-scroll">
        {tasks.isLoading ? (
          <div style={{ padding: 20 }}>
            <LoadingSkeleton label="正在读取任务" rows={6} />
          </div>
        ) : tasks.isError ? (
          <div style={{ padding: 20 }}>
            <ErrorState
              description={tasks.error instanceof Error ? tasks.error.message : "无法读取任务"}
              actionLabel="重试"
              onAction={() => void tasks.refetch()}
            />
          </div>
        ) : visible.length ? (
          <table className="proto-table" aria-label="任务列表">
            <thead>
              <tr>
                <th style={{ width: 120 }}>状态</th>
                <th>任务名称 / ID</th>
                <th style={{ width: 120 }}>场景</th>
                <th style={{ width: 200 }}>动态与进度</th>
                <th style={{ width: 165 }}>最近更新</th>
                <th style={{ width: 190, textAlign: "right" }}>快捷操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task) => {
                const badge = taskStateBadge(task);
                return (
                  <tr key={task.id} onClick={() => open(task)}>
                    <td>
                      <span className={`ref-chip ${badge.tone}`}>{badge.label}</span>
                    </td>
                    <td>
                      <strong style={{ fontSize: "13px" }}>{task.title}</strong>
                      <div className="mono subtle" style={{ fontSize: "11px" }}>
                        #{task.id}
                      </div>
                    </td>
                    <td>
                      <span className="ref-chip">{sceneLabel(task.scene_id)}</span>
                    </td>
                    <td>
                      <div className="mono muted" style={{ fontSize: "11px", marginBottom: 4 }}>
                        黑板 #{task.blackboard_seq ?? 0} · 事件 #{task.event_seq ?? 0}
                      </div>
                      <div className="proto-progress" style={{ maxWidth: 150 }}>
                        <div
                          className={`proto-progress-fill${badge.tone === "tone-ok" ? " is-ok" : badge.tone === "tone-danger" || badge.tone === "tone-warn" ? " is-warn" : ""}`}
                          style={{ width: `${badge.progress}%` }}
                        />
                      </div>
                    </td>
                    <td className="mono subtle">{formatDate(task.updated_at)}</td>
                    <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                      <div className="proto-row-actions">
                        <button type="button" className="ref-secondary-button" style={{ height: 24, padding: "0 8px", fontSize: "11px" }} onClick={() => open(task)}>
                          作战室
                        </button>
                        {isTerminal(task) ? (
                          <button
                            type="button"
                            className="ref-secondary-button"
                            style={{ height: 24, padding: "0 6px" }}
                            aria-label={`重新启动 ${task.title}`}
                            title="原地重新启动（断点续跑）"
                            onClick={() => openRestart(task)}
                          >
                            <RotateCcw size={12} />
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="ref-secondary-button"
                          style={{ height: 24, padding: "0 6px" }}
                          aria-label={`基于此任务新建 ${task.title}`}
                          title="基于此任务配置新建任务"
                          onClick={() => openClone(task)}
                        >
                          <Copy size={12} />
                        </button>
                        <button
                          type="button"
                          className="tga3-danger-button"
                          style={{ height: 24, padding: "0 6px" }}
                          aria-label={`删除任务 ${task.title}`}
                          title="删除任务"
                          onClick={() => setRemoveTask(task)}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <EmptyState label="没有匹配的任务" />
        )}
      </div>

      {rows.length > pageSize ? (
        <div style={{ padding: "8px 16px", borderTop: "1px solid var(--c-border)", background: "var(--c-surface)" }}>
          <Pagination
            total={rows.length}
            pageSize={pageSize}
            page={page}
            onPage={setPage}
            onPageSize={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(removeTask)}
        title={`删除任务 ${removeTask?.title ?? ""}`}
        description="将停止仍在运行的 Agent，并永久删除该任务的数据库记录、工作区、输入、产物和报告。"
        confirmLabel="彻底删除"
        danger
        busy={deleting}
        onCancel={() => setRemoveTask(null)}
        onConfirm={() => void confirmDelete()}
      />
      </div>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}
