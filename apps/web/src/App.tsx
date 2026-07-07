import { FormEvent, useState } from "react";
import { createTask, TGATask } from "./api";

function newTaskId() {
  return `task_${Math.random().toString(16).slice(2, 12)}`;
}

export default function App() {
  const [status, setStatus] = useState("Ready");
  const [task, setTask] = useState<TGATask>({
    id: newTaskId(),
    name: "local-web-audit-demo",
    mode: "web_audit",
    target: "http://127.0.0.1:8080",
    scope: ["127.0.0.1:8080"],
    intensity: "normal",
    allow_active_scan: true,
    goal: "Find and prove common web vulnerabilities in scope.",
    flag_format: null,
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setStatus("Submitting...");
    try {
      const result = await createTask(task);
      setStatus(`Accepted: ${result.task_id}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <main className="shell">
      <section className="panel">
        <h1>TGA</h1>
        <p>Independent task console for authorized security review and CTF labs.</p>
        <form onSubmit={submit}>
          <label>
            Name
            <input value={task.name} onChange={(e) => setTask({ ...task, name: e.target.value })} />
          </label>
          <label>
            Mode
            <select value={task.mode} onChange={(e) => setTask({ ...task, mode: e.target.value as TGATask["mode"] })}>
              <option value="web_audit">Web Audit</option>
              <option value="ctf">CTF</option>
              <option value="code_audit">Code Audit</option>
              <option value="binary_ctf">Binary CTF</option>
            </select>
          </label>
          <label>
            Target
            <input value={task.target} onChange={(e) => setTask({ ...task, target: e.target.value })} />
          </label>
          <label>
            Scope
            <input
              value={task.scope.join(",")}
              onChange={(e) => setTask({ ...task, scope: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
            />
          </label>
          <label>
            Goal
            <textarea value={task.goal} onChange={(e) => setTask({ ...task, goal: e.target.value })} />
          </label>
          <button type="submit">Create Task</button>
        </form>
        <output>{status}</output>
      </section>
    </main>
  );
}

