import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Boxes,
  BrainCircuit,
  ChevronRight,
  CirclePlus,
  ListTodo,
  LoaderCircle,
  PanelLeft,
  ServerCog,
} from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { tga3Api } from "./api";
import { RuntimePage } from "./RuntimePage";
import type { SkillInfo, TaskRun } from "./types";

export function App() {
  return <Shell>
    <Routes>
      <Route path="/" element={<Navigate to="/tasks" replace />} />
      <Route path="/tasks" element={<TaskList />} />
      <Route path="/tasks/new" element={<NewTask />} />
      <Route path="/tasks/:taskId" element={<RuntimePage />} />
      <Route path="/models" element={<Models />} />
      <Route path="/skills" element={<Skills />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  </Shell>;
}

function Shell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const nav = [
    { path: "/tasks", label: "任务", icon: ListTodo },
    { path: "/tasks/new", label: "新建任务", icon: CirclePlus },
    { path: "/models", label: "模型绑定", icon: BrainCircuit },
    { path: "/skills", label: "Skills", icon: Boxes },
  ];
  return <div className={`tga3-shell ${open ? "nav-open" : ""}`}>
    <aside className="tga3-sidebar">
      <Link className="tga3-brand" to="/tasks" onClick={() => setOpen(false)}>
        <span><Bot size={21} /></span>
        <strong>TGA3</strong>
      </Link>
      <nav aria-label="主导航">
        <small>工作台</small>
        {nav.map((item) => {
          const Icon = item.icon;
          const active = location.pathname === item.path
            || (item.path === "/tasks" && /^\/tasks\/[^/]+$/.test(location.pathname));
          return <Link key={item.path} className={active ? "active" : ""} to={item.path} onClick={() => setOpen(false)}>
            <Icon size={18} />
            <span>{item.label}</span>
          </Link>;
        })}
      </nav>
      <footer>
        <ServerCog size={16} />
        <span>Blackboard Runtime</span>
      </footer>
    </aside>
    {open ? <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setOpen(false)} /> : null}
    <div className="tga3-body">
      <header className="tga3-topbar">
        <button className="mobile-nav" aria-label="打开导航" onClick={() => setOpen(true)}><PanelLeft /></button>
        <div><b>双 Agent 协作控制台</b><small>OpenAI Agents SDK · Claude Agent SDK · PostgreSQL Blackboard</small></div>
        <span className="control-plane-state"><i /> 控制面</span>
      </header>
      <main>{children}</main>
    </div>
  </div>;
}

function TaskList() {
  const query = useQuery({ queryKey: ["tga3", "tasks"], queryFn: tga3Api.listTasks, refetchInterval: 3000 });
  return <section className="page-stack">
    <PageHead eyebrow="TASKS" title="任务" description="每个任务动态启动两个相互独立的 Worker 容器。">
      <Link className="primary-button" to="/tasks/new"><CirclePlus size={16} /> 新建任务</Link>
    </PageHead>
    {query.isLoading ? <Loading label="正在读取任务" /> : query.error ? <ErrorBox error={query.error} />
      : query.data?.length ? <div className="task-list">
        {query.data.map((task) => <TaskCard task={task} key={task.id} />)}
      </div> : <Empty title="还没有任务" detail="创建任务后，控制面会按需拉起 OpenAI 和 Claude Worker。" />}
  </section>;
}

function TaskCard({ task }: { task: TaskRun }) {
  return <Link className="task-card" to={`/tasks/${encodeURIComponent(task.id)}`}>
    <div className="task-card-main">
      <StateDot state={task.state} />
      <span><strong>{task.title}</strong><small>{shortId(task.id)} · {formatTime(task.created_at)}</small></span>
    </div>
    <div className="task-card-meta">
      <span><small>黑板序号</small><b>{task.blackboard_seq}</b></span>
      <span><small>对话序号</small><b>{task.dialogue_seq}</b></span>
      <StateBadge state={task.state} />
      <ChevronRight size={18} />
    </div>
  </Link>;
}

function NewTask() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const task = await tga3Api.createTask(title, prompt);
      if (files.length) {
        const uploaded = await Promise.all(files.map((file) => tga3Api.uploadFile(task.id, file)));
        await tga3Api.addPrompt(
          task.id,
          `用户随任务补充了 ${files.length} 个文件，请读取 /inputs 并结合原始目标继续。`,
          [],
          uploaded.map((item) => item.file.id),
        );
      }
      navigate(`/tasks/${encodeURIComponent(task.id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建任务失败");
    } finally {
      setBusy(false);
    }
  };
  return <section className="page-stack narrow-page">
    <PageHead eyebrow="NEW TASK" title="新建任务" description="任务创建后，两个 Worker 容器会自动启动，无需手工 docker compose up。" />
    <form className="new-task-form" onSubmit={(event) => void submit(event)}>
      <label>任务名称<input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={500} placeholder="例如：分析附件并找到 flag" /></label>
      <label>初始提示<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} required rows={9} placeholder="说明目标、边界以及已知条件……" /></label>
      <label className="file-drop">多模态文件<input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} /><span>{files.length ? `已选择 ${files.length} 个文件` : "图片、音频、压缩包、二进制或文本"}</span></label>
      {files.length ? <ul className="file-list">{files.map((file) => <li key={`${file.name}-${file.size}`}>{file.name}<small>{formatBytes(file.size)}</small></li>)}</ul> : null}
      {error ? <p className="form-error">{error}</p> : null}
      <button className="primary-button submit-button" disabled={busy || !title.trim() || !prompt.trim()}>{busy ? <LoaderCircle className="spin" size={17} /> : <CirclePlus size={17} />}{busy ? "正在启动两个 Worker…" : "创建并启动任务"}</button>
    </form>
  </section>;
}

function Models() {
  const query = useQuery({ queryKey: ["tga3", "models"], queryFn: tga3Api.models });
  return <section className="page-stack">
    <PageHead eyebrow="MODELS" title="模型与 Agent 绑定" description="密钥只保存在 Ubuntu 的 models.json；浏览器仅获取脱敏目录。" />
    {query.isLoading ? <Loading label="读取模型目录" /> : query.error ? <ErrorBox error={query.error} /> : <>
      <div className="provider-grid">{query.data?.providers.map((provider) => <article className="provider-card" key={provider.id}>
        <header><span>{provider.name.slice(0, 1)}</span><div><strong>{provider.name}</strong><small>{provider.protocol}</small></div></header>
        <ul>{provider.models.map((model) => <li key={model.id}><b>{model.name}</b><small>{model.id} · {model.max_output_tokens} 输出 Token</small></li>)}</ul>
      </article>)}</div>
      <section className="binding-table"><h2>默认绑定</h2>{Object.entries(query.data?.bindings ?? {}).map(([agent, binding]) => <div key={agent}><b>{agent}</b><span>{binding.runtime}</span><code>{binding.provider_id}/{binding.model_id}</code><small>{binding.max_turns_per_cycle} 轮/周期</small></div>)}</section>
    </>}
  </section>;
}

function Skills() {
  const index = useQuery({ queryKey: ["tga3", "skills"], queryFn: tga3Api.skills });
  const [selected, setSelected] = useState<string | null>(null);
  const document = useQuery({
    queryKey: ["tga3", "skill", selected],
    queryFn: () => tga3Api.skill(selected!),
    enabled: Boolean(selected),
  });
  return <section className="page-stack">
    <PageHead eyebrow="SKILLS" title="通用 Skills" description="不按角色或场景预分配；每个 Agent 先看名称，再按需读取完整内容。" />
    {index.isLoading ? <Loading label="读取 Skills" /> : index.error ? <ErrorBox error={index.error} />
      : index.data?.length ? <div className="skills-layout">
        <div className="skills-index">{index.data.map((skill: SkillInfo) => <button className={selected === skill.name ? "active" : ""} onClick={() => setSelected(skill.name)} key={skill.name}><Boxes size={17} /><span><b>{skill.name}</b><small>{skill.description}</small></span></button>)}</div>
        <article className="skill-document">{selected ? document.isLoading ? <Loading label="读取 Skill" /> : <pre>{document.data?.content}</pre> : <Empty title="选择一个 Skill" detail="这里展示 Agent 通过按名读取获得的完整 SKILL.md。" />}</article>
      </div> : <Empty title="尚未配置 Skill" detail="在 tga3/config/skills/<name>/SKILL.md 中添加即可。" />}
  </section>;
}

function PageHead({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) {
  return <header className="page-head"><div><small>{eyebrow}</small><h1>{title}</h1><p>{description}</p></div>{children ? <div>{children}</div> : null}</header>;
}

export function StateBadge({ state }: { state: string }) {
  return <span className={`state-badge state-${state}`}><i />{stateLabel(state)}</span>;
}

function StateDot({ state }: { state: string }) { return <span className={`state-dot state-${state}`}><i /></span>; }
function Loading({ label }: { label: string }) { return <div className="loading-box"><LoaderCircle className="spin" />{label}</div>; }
function ErrorBox({ error }: { error: unknown }) { return <div className="error-box">{error instanceof Error ? error.message : "请求失败"}</div>; }
function Empty({ title, detail }: { title: string; detail: string }) { return <div className="empty-box"><Bot size={30} /><b>{title}</b><p>{detail}</p></div>; }
function NotFound() { return <section className="page-stack narrow-page"><Empty title="页面不存在" detail="该入口不属于 TGA3 控制面。" /><Link className="primary-button" to="/tasks">返回任务</Link></section>; }

export function stateLabel(value: string): string {
  return ({ created: "已创建", starting: "启动中", running: "运行中", waiting_user: "等待用户", finalizing: "收敛中", reporting: "生成报告", completed: "已完成", failed: "失败", cancelled: "已取消", paused: "已暂停", pause_requested: "暂停中", stopping: "停止中", stopped: "已停止" } as Record<string, string>)[value] ?? value;
}
export function shortId(value: string) { return value.slice(0, 8); }
export function formatTime(value: string) { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`; }
