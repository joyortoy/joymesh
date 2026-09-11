"use client";

import { useEffect, useState } from "react";

type Candidate = {
  node_id: string;
  connector_id: string;
  policy_profile: string;
  certified_capabilities: string[];
  score: number;
  eligible: boolean;
  rejection_reasons: string[];
};

type RuntimeTask = {
  task_id: string;
  status: string;
  policy_profile: string;
  requested_capabilities: string[];
  expanded_capabilities: string[];
  selected_node_id: string | null;
  selected_connector_id: string | null;
  detail: string | null;
};

const DEFAULT_CAPS = [
  "repository.read",
  "repository.summarise",
  "structured_output",
  "streaming_output",
];

export default function RuntimeWorkspacePage() {
  const [prompt, setPrompt] = useState("Summarise the architecture of this repository.");
  const [task, setTask] = useState<RuntimeTask | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [lease, setLease] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function createTask() {
    setError(null);
    const response = await fetch("/api/runtime/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_id: "local-workspace",
        prompt,
        policy_profile: "read_only",
        requested_capabilities: DEFAULT_CAPS,
        preferred_connectors: [],
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      setError(body.detail || "Failed to create runtime task");
      return;
    }
    setTask(body);
    await refresh(body.task_id);
  }

  async function refresh(taskId: string) {
    const [taskRes, candRes, eventRes, leaseRes] = await Promise.all([
      fetch(`/api/runtime/tasks/${taskId}`),
      fetch(`/api/runtime/tasks/${taskId}/candidates`),
      fetch(`/api/runtime/tasks/${taskId}/events`),
      fetch(`/api/runtime/tasks/${taskId}/lease`),
    ]);
    if (taskRes.ok) setTask(await taskRes.json());
    if (candRes.ok) setCandidates(await candRes.json());
    if (eventRes.ok) setEvents(await eventRes.json());
    if (leaseRes.ok) setLease(await leaseRes.json());
  }

  useEffect(() => {
    if (!task?.task_id) return;
    const handle = window.setInterval(() => {
      void refresh(task.task_id);
    }, 2000);
    return () => window.clearInterval(handle);
  }, [task?.task_id]);

  return (
    <main className="runtimeShell">
      <header className="runtimeHeader">
        <p className="eyebrow">JoyMesh Runtime</p>
        <h1>Capability-first task workspace</h1>
        <p>Tasks request capabilities. Policy and certification choose the route.</p>
      </header>

      <section className="runtimePanel">
        <h2>Request</h2>
        <label>
          Prompt
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={4} />
        </label>
        <p>Policy: <strong>read_only</strong></p>
        <ul>
          {DEFAULT_CAPS.map((item) => <li key={item}>{item}</li>)}
        </ul>
        <button className="primaryButton" onClick={() => void createTask()}>
          Create runtime task
        </button>
        {error && <p className="errorText">{error}</p>}
      </section>

      {task && (
        <section className="runtimePanel">
          <h2>Task {task.task_id}</h2>
          <p>Status: <strong>{task.status}</strong></p>
          <p>Policy: {task.policy_profile}</p>
          <p>Expanded: {task.expanded_capabilities.join(", ")}</p>
          {task.selected_connector_id && (
            <p>
              Route: {task.selected_connector_id} on {task.selected_node_id}
            </p>
          )}
          {task.detail && <p>{task.detail}</p>}
        </section>
      )}

      {candidates.length > 0 && (
        <section className="runtimePanel">
          <h2>Candidates</h2>
          {candidates.map((candidate) => (
            <article key={`${candidate.node_id}:${candidate.connector_id}`} className="candidateCard">
              <strong>
                {candidate.connector_id} on {candidate.node_id}
              </strong>
              <p>{candidate.eligible ? "Eligible" : "Rejected"} · Score: {candidate.score}</p>
              <p>Policy: {candidate.policy_profile}</p>
              {candidate.eligible ? (
                <ul>
                  {candidate.certified_capabilities.map((cap) => (
                    <li key={cap}>✓ {cap}</li>
                  ))}
                </ul>
              ) : (
                <ul>
                  {candidate.rejection_reasons.map((reason) => (
                    <li key={reason}>Reason: {reason}</li>
                  ))}
                </ul>
              )}
            </article>
          ))}
        </section>
      )}

      {lease && (
        <section className="runtimePanel">
          <h2>Lease</h2>
          <p>Status: {String(lease.status)}</p>
          <p>Fencing token: {String(lease.fencing_token)}</p>
          <p>Attempt: {String(lease.attempt_id)}</p>
        </section>
      )}

      {events.length > 0 && (
        <section className="runtimePanel">
          <h2>Events</h2>
          <ol>
            {events.map((event) => (
              <li key={String(event.sequence)}>
                {String(event.event_type)} · seq {String(event.sequence)}
              </li>
            ))}
          </ol>
        </section>
      )}
    </main>
  );
}
