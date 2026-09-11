"use client";

import { useEffect, useState } from "react";
import catalogueData from "./connector-catalogue.generated.json";

const STEPS = [
  { state: "ACCOUNT_READY", label: "Account" },
  { state: "NODE_PAIRING_REQUIRED", label: "Node" },
  { state: "ENVIRONMENT_CHECK", label: "Environment" },
  { state: "HARNESS_SELECTION", label: "Harnesses" },
  { state: "INSTALLATION_REVIEW", label: "Install" },
  { state: "INSTALLING", label: "Installing" },
  { state: "AUTHENTICATION_REQUIRED", label: "Accounts" },
  { state: "CERTIFICATION_REQUIRED", label: "Certify" },
  { state: "ROUTING_SETUP", label: "Routing" },
  { state: "FIRECONNECT_SETUP", label: "FireConnect" },
  { state: "FINAL_CHECK", label: "Ready" },
] as const;

type Connector = {
  harness_id: string;
  display_name: string;
  vendor: string;
  description: string;
  category: string;
  tier: "terminal" | "ide";
  open_source: boolean;
  maturity: string;
  executable_names: string[];
  remote_execution_supported: boolean;
  structured_output_supported: boolean;
  supported_platforms: string[];
  installation_options: {
    id: string;
    mechanism: string;
    argv: string[];
    executable: boolean;
    digest_required: boolean;
  }[];
  authentication_methods: { kind: string }[];
  provider_modes: {
    display_name: string;
    funding_source: string;
    separately_billed: boolean | null;
  }[];
  experimental: boolean;
  blocked_reason?: string | null;
};

type ConnectorReadiness = {
  connector_id: string;
  state: string;
  recommended_action: string | null;
  blocking_reason: string | null;
  active_task_id: string | null;
  installed_version: string | null;
  routing_eligible: boolean;
};

type InstallPlan = {
  connector_id: string;
  plan_id: string;
  plan_hash: string;
  argv: string[];
  risk?: string;
  expires_at?: string;
  approval_required: boolean;
  raw: Record<string, unknown>;
};

const HARNESSES = catalogueData as unknown as Connector[];
const FILTERS = ["All", "Ready", "Available to install", "Needs verification", "CLI", "IDE", "Open source", "Local models", "Subscription login", "API key"] as const;
const DEV_SIMULATE = process.env.NEXT_PUBLIC_JOYMESH_ONBOARDING_DEV_SIMULATE === "1";

type Progress = {
  state: string;
  selectedHarnesses: string[];
  selectedNodeId: string | null;
  nodeName: string;
  nodeOnline: boolean;
  environmentChecked: boolean;
  paidPolicy: "never" | "ask" | "limits";
  fireconnect: boolean;
  revision?: number;
  pairingId?: string | null;
  pairingCode?: string | null;
  synchronised?: boolean;
  authority?: string;
  blockingReasons?: string[];
  updatedAt?: string;
};

const INITIAL: Progress = {
  state: "ACCOUNT_READY",
  selectedHarnesses: [],
  selectedNodeId: null,
  nodeName: "",
  nodeOnline: false,
  environmentChecked: false,
  paidPolicy: "ask",
  fireconnect: false,
  revision: 0,
  pairingId: null,
  pairingCode: null,
  synchronised: false,
};

function indexFor(state: string) {
  const found = STEPS.findIndex((step) => step.state === state);
  return found < 0 ? 0 : found;
}

export default function OnboardingClient({
  user,
}: {
  user: { name: string; email: string };
}) {
  const [progress, setProgress] = useState<Progress>(INITIAL);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [showPlan, setShowPlan] = useState(false);
  const [readiness, setReadiness] = useState<Record<string, ConnectorReadiness>>({});
  const [plans, setPlans] = useState<InstallPlan[]>([]);
  const [environment, setEnvironment] = useState<Record<string, unknown> | null>(null);
  const currentIndex = indexFor(progress.state);
  const current = STEPS[currentIndex] ?? STEPS[0];

  useEffect(() => {
    fetch("/api/onboarding", { credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((saved: Progress & { connectors?: ConnectorReadiness[]; environment?: Record<string, unknown> | null }) => {
        setProgress({ ...INITIAL, ...saved });
        if (Array.isArray(saved.connectors)) {
          const next: Record<string, ConnectorReadiness> = {};
          for (const row of saved.connectors) next[row.connector_id] = row;
          setReadiness(next);
        }
        if (saved.environment) setEnvironment(saved.environment);
      })
      .catch(() => setNotice("Progress storage is unavailable. Changes will not be saved."))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    const nodeId = progress.selectedNodeId;
    if (!nodeId) return;
    let cancelled = false;
    async function load() {
      try {
        const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/connectors/readiness`, {
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!response.ok) return;
        const rows = (await response.json()) as ConnectorReadiness[];
        if (cancelled) return;
        const next: Record<string, ConnectorReadiness> = {};
        for (const row of rows) next[row.connector_id] = row;
        setReadiness(next);
        const derived = deriveStateFromReadiness(progress, next);
        if (derived && derived !== progress.state) {
          void save({ ...progress, state: derived }, "Synced from node readiness");
        }
      } catch {
        /* keep last known readiness */
      }
    }
    void load();
    const interval = window.setInterval(() => void load(), progress.state === "INSTALLING" ? 2500 : 8000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [progress.selectedNodeId, progress.state, progress.selectedHarnesses.join(",")]);

  useEffect(() => {
    if (!progress.pairingId || progress.selectedNodeId) return;
    let cancelled = false;
    async function poll() {
      const response = await fetch(`/api/pairing/${encodeURIComponent(progress.pairingId!)}`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok || cancelled) return;
      const body = await response.json();
      if (body.status === "paired" && body.node?.id) {
        void save(
          {
            ...progress,
            pairingId: progress.pairingId,
            selectedNodeId: body.node.id,
            nodeName: body.node.name || progress.nodeName,
            nodeOnline: true,
            state: "ENVIRONMENT_CHECK",
          },
          "Node paired",
        );
      }
    }
    void poll();
    const interval = window.setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [progress.pairingId, progress.selectedNodeId]);

  async function save(next: Progress, message = "Progress saved") {
    setProgress(next);
    setSaving(true);
    try {
      const response = await fetch("/api/onboarding", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(next),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "save failed");
      }
      const body = await response.json();
      setProgress({ ...next, ...body });
      setNotice(message);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save progress.");
    } finally {
      setSaving(false);
    }
  }

  async function startPairing() {
    setSaving(true);
    try {
      const response = await fetch("/api/pairing/start", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "pairing unavailable");
      }
      const body = await response.json();
      await save(
        {
          ...progress,
          state: "NODE_PAIRING_REQUIRED",
          pairingId: body.pairing?.id,
          pairingCode: body.pairing?.user_code,
          nodeOnline: false,
          selectedNodeId: null,
        },
        "Pairing code created",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Pairing failed");
    } finally {
      setSaving(false);
    }
  }

  async function runEnvironment() {
    if (!progress.selectedNodeId) {
      setNotice("Pair a node before running diagnostics.");
      return;
    }
    const response = await fetch(`/api/nodes/${encodeURIComponent(progress.selectedNodeId)}/environment`, {
      credentials: "same-origin",
    });
    if (!response.ok) {
      setNotice("Environment diagnostics unavailable");
      return;
    }
    const body = await response.json();
    setEnvironment(body);
    await save({ ...progress, environmentChecked: true }, "Diagnostics recorded");
  }

  async function requestInstallPlans() {
    if (!progress.selectedNodeId) {
      setNotice("A paired node is required before planning installs.");
      return;
    }
    setSaving(true);
    try {
      const nextPlans: InstallPlan[] = [];
      for (const connectorId of progress.selectedHarnesses) {
        const ready = readiness[connectorId];
        if (ready?.state === "ready" || ready?.routing_eligible) continue;
        const response = await fetch(
          `/api/nodes/${encodeURIComponent(progress.selectedNodeId)}/connectors/${encodeURIComponent(connectorId)}/install/plan`,
          { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" },
        );
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || `Plan failed for ${connectorId}`);
        }
        const body = await response.json();
        const plan = body.plan || body;
        nextPlans.push({
          connector_id: connectorId,
          plan_id: String(plan.plan_id || plan.id || body.task_id || ""),
          plan_hash: String(plan.plan_hash || ""),
          argv: Array.isArray(plan.argv) ? plan.argv.map(String) : [],
          risk: plan.risk,
          expires_at: plan.expires_at,
          approval_required: body.approval_required !== false,
          raw: body,
        });
      }
      setPlans(nextPlans);
      await save({ ...progress, state: "INSTALLATION_REVIEW" }, nextPlans.length ? "Install plans ready" : "Selected connectors already ready");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Install planning failed");
    } finally {
      setSaving(false);
    }
  }

  async function approvePlans() {
    if (!plans.length) {
      setNotice("No install plans to approve.");
      return;
    }
    setSaving(true);
    try {
      for (const plan of plans) {
        const response = await fetch(`/api/connector-tasks/${encodeURIComponent(plan.plan_id)}/execute`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan_hash: plan.plan_hash, approved: true }),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || `Approval failed for ${plan.connector_id}`);
        }
      }
      await save({ ...progress, state: "INSTALLING" }, "Installation approved and executing");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Approval failed");
    } finally {
      setSaving(false);
    }
  }

  async function startCertification() {
    if (!progress.selectedNodeId) return;
    setSaving(true);
    try {
      for (const connectorId of progress.selectedHarnesses) {
        const ready = readiness[connectorId];
        if (ready?.state === "ready" || ready?.state === "certified") continue;
        const response = await fetch(
          `/api/nodes/${encodeURIComponent(progress.selectedNodeId)}/connectors/${encodeURIComponent(connectorId)}/certify/plan`,
          { method: "POST", credentials: "same-origin" },
        );
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || `Certification plan failed for ${connectorId}`);
        }
        const body = await response.json();
        const plan = body.plan || body;
        const execute = await fetch(`/api/connector-tasks/${encodeURIComponent(plan.plan_id || plan.id)}/execute`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan_hash: plan.plan_hash, approved: true }),
        });
        if (!execute.ok) {
          const detail = await execute.json().catch(() => ({}));
          throw new Error(detail.detail || `Certification failed for ${connectorId}`);
        }
      }
      await save({ ...progress, state: "CERTIFYING" }, "Certification started");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Certification failed");
    } finally {
      setSaving(false);
    }
  }

  function move(delta: number) {
    if (delta > 0 && !canContinue(progress, readiness, plans)) {
      setNotice("Backend conditions are not met for this step yet.");
      return;
    }
    if (delta > 0 && progress.state === "HARNESS_SELECTION") {
      void requestInstallPlans();
      return;
    }
    if (delta > 0 && progress.state === "INSTALLATION_REVIEW") {
      void approvePlans();
      return;
    }
    if (delta > 0 && progress.state === "CERTIFICATION_REQUIRED") {
      void startCertification();
      return;
    }
    const nextIndex = Math.max(0, Math.min(STEPS.length - 1, currentIndex + delta));
    void save({ ...progress, state: STEPS[nextIndex].state }, delta > 0 ? "Step completed" : "Returned to previous step");
  }

  const readyHarnesses = progress.selectedHarnesses.filter(
    (id) => readiness[id]?.state === "ready" || readiness[id]?.routing_eligible,
  );
  const completion = Math.round(((currentIndex + 1) / STEPS.length) * 100);

  return (
    <div className="appShell">
      <header className="topbar">
        <div className="brand"><span className="brandMark">J</span><span>JoyMesh</span></div>
        <div className="topStatus">
          <span className={`nodeDot ${progress.nodeOnline ? "online" : ""}`} />
          {progress.nodeOnline ? progress.nodeName || progress.selectedNodeId || "Node online" : "No node connected"}
        </div>
        <div className="account">
          <span>{user.name}</span>
          <a href="/signout-with-chatgpt?return_to=/">Sign out</a>
        </div>
      </header>

      <div className="workspace">
        <aside className="rail" aria-label="Onboarding progress">
          <div className="railIntro">
            <p className="eyebrow">Set up your mesh</p>
            <strong>{completion}% complete</strong>
            <div className="progressTrack"><i style={{ width: `${completion}%` }} /></div>
          </div>
          <ol>
            {STEPS.map((step, index) => (
              <li key={step.state} className={index === currentIndex ? "active" : index < currentIndex ? "done" : ""}>
                <button onClick={() => void save({ ...progress, state: step.state }, `Opened ${step.label}`)}>
                  <span>{index < currentIndex ? "✓" : index + 1}</span>
                  {step.label}
                </button>
              </li>
            ))}
          </ol>
          <div className="securityCard">
            <span>Security boundary</span>
            <p>The node initiates every connection. Credentials and workspace files never move to the browser. Authority: {progress.authority || "loading"}.</p>
          </div>
        </aside>

        <main className="main">
          {!loaded ? (
            <div className="loading" role="status">Restoring your setup…</div>
          ) : (
            <>
              <div className="stepHeader">
                <div>
                  <p className="eyebrow">Step {currentIndex + 1} of {STEPS.length}</p>
                  <h1>{heading(current.state)}</h1>
                  <p>{subheading(current.state)}</p>
                </div>
                <button className="quietButton" onClick={() => void save(progress)}>Save &amp; exit</button>
              </div>

              <section className="stepCard">
                {current.state === "ACCOUNT_READY" && <AccountStep user={user} />}
                {current.state === "NODE_PAIRING_REQUIRED" && (
                  <NodeStep
                    progress={progress}
                    onStartPairing={() => void startPairing()}
                    onChangeName={(nodeName) => void save({ ...progress, nodeName })}
                    onDevSimulate={
                      DEV_SIMULATE
                        ? () =>
                            void save(
                              {
                                ...progress,
                                nodeOnline: true,
                                selectedNodeId: progress.selectedNodeId || "dev-local-node",
                                nodeName: progress.nodeName || "Dev node",
                                state: "ENVIRONMENT_CHECK",
                              },
                              "Dev simulation only",
                            )
                        : undefined
                    }
                  />
                )}
                {current.state === "ENVIRONMENT_CHECK" && (
                  <EnvironmentStep
                    progress={progress}
                    environment={environment}
                    onCheck={() => void runEnvironment()}
                  />
                )}
                {current.state === "HARNESS_SELECTION" && (
                  <HarnessStep selected={progress.selectedHarnesses} readiness={readiness} onToggle={(id) => {
                    const selected = progress.selectedHarnesses.includes(id)
                      ? progress.selectedHarnesses.filter((item) => item !== id)
                      : [...progress.selectedHarnesses, id];
                    void save({ ...progress, selectedHarnesses: selected }, "Harness selection saved");
                  }} />
                )}
                {(current.state === "INSTALLATION_REVIEW" || current.state === "INSTALLING") && (
                  <InstallationStep
                    selected={progress.selectedHarnesses}
                    readiness={readiness}
                    plans={plans}
                    showPlan={showPlan}
                    setShowPlan={setShowPlan}
                    installing={current.state === "INSTALLING"}
                    onRequestPlans={() => void requestInstallPlans()}
                    onApprove={() => void approvePlans()}
                  />
                )}
                {current.state === "AUTHENTICATION_REQUIRED" && <AuthenticationStep selected={progress.selectedHarnesses} readiness={readiness} />}
                {current.state === "CERTIFICATION_REQUIRED" && (
                  <CertificationStep selected={progress.selectedHarnesses} readiness={readiness} onCertify={() => void startCertification()} />
                )}
                {current.state === "ROUTING_SETUP" && (
                  <RoutingStep value={progress.paidPolicy} onChange={(paidPolicy) => void save({ ...progress, paidPolicy }, "Routing policy saved")} />
                )}
                {current.state === "FIRECONNECT_SETUP" && (
                  <FireConnectStep enabled={progress.fireconnect} onChange={(fireconnect) => void save({ ...progress, fireconnect }, "FireConnect preference saved")} />
                )}
                {current.state === "FINAL_CHECK" && <FinalStep progress={progress} readyHarnesses={readyHarnesses} />}
              </section>

              {notice && <div className="notice" role="status">{notice}</div>}
              {(progress.blockingReasons?.length ?? 0) > 0 && (
                <div className="notice" role="status">Blocking: {progress.blockingReasons?.join(", ")}</div>
              )}
              <div className="stepActions">
                <button className="secondaryButton" disabled={currentIndex === 0 || saving} onClick={() => move(-1)}>Back</button>
                <span>{saving ? "Saving…" : progress.updatedAt ? `Saved ${new Date(progress.updatedAt).toLocaleTimeString()}` : "Saved securely"}</span>
                <button
                  className="primaryButton"
                  disabled={saving || (current.state === "HARNESS_SELECTION" && progress.selectedHarnesses.length === 0)}
                  onClick={() => currentIndex === STEPS.length - 1
                    ? void save(
                        {
                          ...progress,
                          state: readyHarnesses.length ? "COMPLETE" : "LIMITED_MODE",
                        },
                        readyHarnesses.length ? "Mesh ready" : "Limited mode saved",
                      )
                    : move(1)}
                >
                  {currentIndex === STEPS.length - 1
                    ? (readyHarnesses.length ? "Enter JoyMesh" : "Continue in limited mode")
                    : continueLabel(current.state)}
                </button>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function canContinue(progress: Progress, readiness: Record<string, ConnectorReadiness>, plans: InstallPlan[]) {
  if (progress.state === "NODE_PAIRING_REQUIRED") return Boolean(progress.selectedNodeId);
  if (progress.state === "ENVIRONMENT_CHECK") return progress.environmentChecked;
  if (progress.state === "HARNESS_SELECTION") return progress.selectedHarnesses.length > 0;
  if (progress.state === "INSTALLATION_REVIEW") return plans.length > 0 || progress.selectedHarnesses.every((id) => readiness[id]?.state === "ready");
  if (progress.state === "INSTALLING") {
    return progress.selectedHarnesses.every((id) => {
      const state = readiness[id]?.state;
      return state && state !== "installing" && state !== "available_to_install";
    });
  }
  return true;
}

function deriveStateFromReadiness(progress: Progress, readiness: Record<string, ConnectorReadiness>) {
  const selected = progress.selectedHarnesses;
  if (!selected.length || !progress.selectedNodeId) return null;
  const rows = selected.map((id) => readiness[id]).filter(Boolean);
  if (rows.some((row) => row.state === "installing")) return "INSTALLING";
  if (rows.some((row) => row.state === "authentication_required" || row.state === "authentication_in_progress")) {
    return rowState(rows, "authentication_in_progress") ? "VERIFYING_ACCOUNTS" : "AUTHENTICATION_REQUIRED";
  }
  if (rows.some((row) => row.state === "certification_required" || row.state === "certification_in_progress")) {
    return rowState(rows, "certification_in_progress") ? "CERTIFYING" : "CERTIFICATION_REQUIRED";
  }
  if (rows.length === selected.length && rows.every((row) => row.state === "ready" || row.routing_eligible)) {
    if (["ROUTING_SETUP", "FIRECONNECT_SETUP", "FINAL_CHECK", "COMPLETE", "LIMITED_MODE"].includes(progress.state)) {
      return null;
    }
    return "ROUTING_SETUP";
  }
  return null;
}

function rowState(rows: ConnectorReadiness[], state: string) {
  return rows.some((row) => row.state === state);
}

function continueLabel(state: string) {
  if (state === "HARNESS_SELECTION") return "Plan installation";
  if (state === "INSTALLATION_REVIEW") return "Approve & install";
  if (state === "CERTIFICATION_REQUIRED") return "Start certification";
  return "Continue";
}

function AccountStep({ user }: { user: { name: string; email: string } }) {
  return <div className="accountReady"><div className="successIcon">✓</div><h2>Account ready</h2><p>You’re signed in as <strong>{user.email}</strong>. Browser sessions use secure, server-managed identity—no access token is stored in the browser.</p><div className="infoGrid"><Info label="Session" value="Protected" /><Info label="Organisation" value="Personal workspace" /><Info label="Step-up" value="Required for high-risk actions" /></div></div>;
}

function NodeStep({
  progress,
  onStartPairing,
  onChangeName,
  onDevSimulate,
}: {
  progress: Progress;
  onStartPairing: () => void;
  onChangeName: (value: string) => void;
  onDevSimulate?: () => void;
}) {
  return (
    <div>
      <div className="choiceTabs"><button className="selected">Desktop pairing</button><button>Headless device code</button></div>
      <div className="pairingPanel">
        <div>
          <p className="eyebrow">Run on your machine</p>
          <code>{progress.pairingCode ? `joymesh node pair --code ${progress.pairingCode}` : "Create a pairing code to continue"}</code>
          <p>The code expires in 10 minutes. Your node creates its signing key locally and opens an outbound TLS WebSocket.</p>
        </div>
        <div className="pairingCode">MESH<br /><strong>{progress.pairingCode || "····"}</strong></div>
      </div>
      <label className="field">Node name<input value={progress.nodeName} placeholder="Joy’s MacBook" onChange={(event) => onChangeName(event.target.value)} /></label>
      <button className="testButton" onClick={onStartPairing}>{progress.pairingCode ? "Regenerate pairing code" : "Create pairing code"}</button>
      {onDevSimulate && (
        <button className="testButton" onClick={onDevSimulate}>Dev-only simulate paired node</button>
      )}
      <p className="demoWarning">Production pairing uses the control plane. The browser never invents node readiness.</p>
    </div>
  );
}

function EnvironmentStep({
  progress,
  environment,
  onCheck,
}: {
  progress: Progress;
  environment: Record<string, unknown> | null;
  onCheck: () => void;
}) {
  const checks = [
    ["Node online", environment?.node_online ? "Online" : progress.nodeOnline ? "Online" : "Offline"],
    ["Operating system", String(environment?.operating_system || "Unknown")],
    ["Node version", String(environment?.version || "Unknown")],
    ["Architecture", String(environment?.architecture || "Unknown")],
  ];
  return (
    <div>
      {!progress.selectedNodeId && <Warning title="Node offline">Pair a node before running diagnostics. The browser cannot inspect your machine directly.</Warning>}
      <div className="checkList">
        {checks.map(([name, detail]) => (
          <div key={name}>
            <span className={progress.environmentChecked ? "check good" : "check"}>{progress.environmentChecked ? "✓" : "—"}</span>
            <div><strong>{name}</strong><p>{detail}</p></div>
            <b>{progress.environmentChecked ? "Checked" : "Not checked"}</b>
          </div>
        ))}
      </div>
      <button className="testButton" disabled={!progress.selectedNodeId} onClick={onCheck}>Run node diagnostics</button>
    </div>
  );
}

function HarnessStep({ selected, readiness, onToggle }: { selected: string[]; readiness: Record<string, ConnectorReadiness>; onToggle: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("All");
  const visible = HARNESSES.filter((connector) => {
    const terms = [
      connector.display_name,
      connector.vendor,
      ...connector.executable_names,
      ...connector.provider_modes.flatMap((mode) => [mode.display_name, mode.funding_source]),
      connector.category,
    ].join(" ").toLowerCase();
    if (!terms.includes(query.trim().toLowerCase())) return false;
    const nodeState = readiness[connector.harness_id]?.state;
    if (filter === "Ready") return nodeState === "ready" || connector.maturity === "certified" || connector.maturity === "production_ready";
    if (filter === "Available to install") return (nodeState === "available_to_install" || connector.installation_options.length > 0) && connector.maturity !== "blocked";
    if (filter === "Needs verification") return nodeState === "verification_required" || nodeState === "certification_required" || (connector.remote_execution_supported && !["certified", "production_ready"].includes(connector.maturity));
    if (filter === "CLI") return connector.tier === "terminal";
    if (filter === "IDE") return connector.tier === "ide";
    if (filter === "Open source") return connector.open_source;
    if (filter === "Local models") return connector.provider_modes.some((mode) => mode.funding_source === "local");
    if (filter === "Subscription login") return connector.provider_modes.some((mode) => mode.funding_source === "subscription");
    if (filter === "API key") return connector.authentication_methods.some((method) => ["environment_variable", "provider_key", "local_api_key"].includes(method.kind));
    return true;
  });
  const groups = [
    ["Terminal harnesses", visible.filter((item) => item.tier === "terminal" && !item.open_source)],
    ["Open-source harnesses", visible.filter((item) => item.tier === "terminal" && item.open_source)],
    ["IDE integrations", visible.filter((item) => item.tier === "ide")],
  ] as const;
  return <div><Warning title="Nothing is selected by default">Catalogue presence is not a support claim. Computer status comes from node evidence, not catalogue maturity. Selection does not install.</Warning><div className="catalogueTools"><input aria-label="Search connectors" placeholder="Search harness, vendor, executable, or provider" value={query} onChange={(event) => setQuery(event.target.value)} /><div className="filterRow">{FILTERS.map((item) => <button key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}>{item}</button>)}</div></div>{groups.map(([title, connectors]) => connectors.length > 0 && <section className="connectorSection" key={title}><div className="sectionTitle"><h2>{title}</h2><span>{connectors.length}</span></div><div className="harnessGrid">{connectors.map((connector) => { const blocked = connector.maturity === "blocked" || readiness[connector.harness_id]?.state === "blocked"; const isSelected = selected.includes(connector.harness_id); const node = readiness[connector.harness_id]; return <button key={connector.harness_id} disabled={blocked} className={`harnessCard ${isSelected ? "selected" : ""} ${blocked ? "blocked" : ""}`} onClick={() => onToggle(connector.harness_id)} aria-pressed={isSelected}><div className="harnessTop"><span className="harnessLogo">{connector.display_name.slice(0, 1)}</span><div><strong>{connector.display_name}</strong><small>Implementation: {maturityLabel(connector)}</small></div><i>{blocked ? "—" : isSelected ? "✓" : "+"}</i></div><p>{connector.description}</p><dl><div><dt>Computer status</dt><dd>{nodeStateLabel(node)}</dd></div><div><dt>Version</dt><dd>{node?.installed_version || "Not discovered"}</dd></div><div><dt>Next action</dt><dd>{actionLabel(node?.recommended_action)}</dd></div><div><dt>Providers</dt><dd>{connector.provider_modes.map((mode) => mode.display_name).slice(0, 2).join(", ") || "Vendor managed"}</dd></div></dl>{(node?.blocking_reason || connector.blocked_reason) && <span className="blockedReason">{node?.blocking_reason || connector.blocked_reason}</span>}</button>; })}</div></section>)}{visible.length === 0 && <div className="emptyCatalogue">No connectors match these filters.</div>}</div>;
}

function InstallationStep({
  selected,
  readiness,
  plans,
  showPlan,
  setShowPlan,
  installing,
  onRequestPlans,
  onApprove,
}: {
  selected: string[];
  readiness: Record<string, ConnectorReadiness>;
  plans: InstallPlan[];
  showPlan: boolean;
  setShowPlan: (value: boolean) => void;
  installing: boolean;
  onRequestPlans: () => void;
  onApprove: () => void;
}) {
  const connectors = HARNESSES.filter((item) => selected.includes(item.harness_id));
  return (
    <div>
      <Warning title="Review before execution">The browser submits connector IDs only. The control plane returns the authoritative plan. Explicit approval is required.</Warning>
      <div className="reviewList">
        {connectors.map((connector) => {
          const plan = plans.find((item) => item.connector_id === connector.harness_id);
          const node = readiness[connector.harness_id];
          return (
            <article key={connector.harness_id}>
              <span className="harnessLogo">{connector.display_name[0]}</span>
              <div>
                <strong>{connector.display_name}</strong>
                <p>{plan ? plan.argv.join(" ") || "Backend plan ready" : "No backend plan yet"}</p>
                <small>Computer status: {nodeStateLabel(node)}</small>
              </div>
              <b>{installing ? "Installing…" : plan ? "Approval required" : node?.state === "ready" ? "Already ready" : "Plan required"}</b>
            </article>
          );
        })}
      </div>
      <div className="stepActions">
        <button className="secondaryButton" onClick={onRequestPlans}>Refresh plans</button>
        <button className="primaryButton" disabled={!plans.length || installing} onClick={onApprove}>Approve selected plans</button>
      </div>
      {plans[0] && (
        <>
          <button className="textButton" onClick={() => setShowPlan(!showPlan)}>{showPlan ? "Hide" : "Inspect"} exact execution plan</button>
          {showPlan && (
            <pre className="plan">
              {JSON.stringify(plans[0].raw, null, 2)}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

function AuthenticationStep({ selected, readiness }: { selected: string[]; readiness: Record<string, ConnectorReadiness> }) {
  return <div><p className="sectionLead">Login launch and authentication verification are separate. Secrets stay on the node.</p><div className="reviewList">{HARNESSES.filter((item) => selected.includes(item.harness_id)).map((connector) => { const node = readiness[connector.harness_id]; return <article key={connector.harness_id}><span className="harnessLogo">{connector.display_name[0]}</span><div><strong>{connector.display_name}</strong><p>{connector.authentication_methods.map((method) => method.kind.replaceAll("_", " ")).join(" · ") || "Local IDE configuration"}</p><small>{nodeStateLabel(node)}{node?.blocking_reason ? ` · ${node.blocking_reason}` : ""}</small></div><b>{authBadge(node)}</b></article>; })}</div></div>;
}

function CertificationStep({ selected, readiness, onCertify }: { selected: string[]; readiness: Record<string, ConnectorReadiness>; onCertify: () => void }) {
  return <div><p className="sectionLead">Adapter conformance and real-binary certification are separate evidence steps.</p><div className="reviewList">{HARNESSES.filter((item) => selected.includes(item.harness_id)).map((connector) => { const node = readiness[connector.harness_id]; return <article key={connector.harness_id}><span className="harnessLogo">{connector.display_name[0]}</span><div><strong>{connector.display_name}</strong><p>Implementation: {maturityLabel(connector)} · Computer: {nodeStateLabel(node)}</p></div><b>{node?.state === "ready" || node?.state === "certified" ? "Verified" : node?.state === "certification_required" || node?.state === "verification_required" ? "Approval required" : connector.remote_execution_supported ? "Pending evidence" : "Not routable"}</b></article>; })}</div><button className="primaryButton" onClick={onCertify}>Start certification</button></div>;
}

function RoutingStep({ value, onChange }: { value: Progress["paidPolicy"]; onChange: (value: Progress["paidPolicy"]) => void }) {
  const options = [["never", "Never", "Reject every paid API fallback."], ["ask", "Ask every time", "Recommended. Show cost and account before routing."], ["limits", "Allow with limits", "Permit only inside configured budget and concurrency limits."]] as const;
  return <div><p className="sectionLead">Installing a harness does not grant permission to spend money. Pick the policy used when a subscription route is unavailable.</p><div className="policyList">{options.map(([id, title, detail]) => <label key={id} className={value === id ? "selected" : ""}><input type="radio" name="policy" checked={value === id} onChange={() => onChange(id)} /><span><strong>{title}</strong><small>{detail}</small></span></label>)}</div></div>;
}

function FireConnectStep({ enabled, onChange }: { enabled: boolean; onChange: (value: boolean) => void }) {
  return <div><div className="optionalBadge">Optional</div><h2>Provider routing through FireConnect</h2><p className="sectionLead">FireConnect changes provider configuration for compatible harnesses. It is separately planned, approved, reversible, and invalidates affected certifications.</p><label className="switchRow"><span><strong>Prepare FireConnect routing</strong><small>Preference is stored in control-plane onboarding progress.</small></span><input type="checkbox" checked={enabled} onChange={(event) => onChange(event.target.checked)} /></label></div>;
}

function FinalStep({ progress, readyHarnesses }: { progress: Progress; readyHarnesses: string[] }) {
  const full = readyHarnesses.length > 0;
  return <div className="final"><div className={`finalIcon ${full ? "ready" : ""}`}>{full ? "✓" : "!"}</div><h2>{full ? "Your mesh is ready" : "Limited mode only"}</h2><p>{full ? "At least one node-connected harness is certified and routable." : "No harness is both certified and connected to a real node. You can enter the workspace to review configuration, but remote execution remains disabled."}</p><div className="finalGrid"><Info label="Node" value={progress.nodeOnline ? progress.nodeName || progress.selectedNodeId || "Connected" : "Offline"} /><Info label="Harnesses selected" value={String(progress.selectedHarnesses.length)} /><Info label="Paid fallback" value={progress.paidPolicy === "ask" ? "Ask every time" : progress.paidPolicy} /><Info label="FireConnect" value={progress.fireconnect ? "Planned" : "Skipped"} /></div></div>;
}

function Info({ label, value }: { label: string; value: string }) { return <div className="info"><span>{label}</span><strong>{value}</strong></div>; }
function Warning({ title, children }: { title: string; children: React.ReactNode }) { return <div className="warning"><span>!</span><div><strong>{title}</strong><p>{children}</p></div></div>; }
function maturityLabel(connector: Connector) {
  if (connector.tier === "ide") return "IDE only";
  if (connector.maturity === "blocked") return "Temporarily blocked";
  if (connector.maturity === "certified" || connector.maturity === "production_ready") return "Verified";
  if (connector.maturity === "adapter_conformant") return "Adapter conformant";
  if (connector.maturity === "authenticatable") return "Authenticatable";
  if (connector.maturity === "installable") return "Installable";
  if (connector.maturity === "discoverable") return "Discoverable";
  return connector.remote_execution_supported ? "Experimental" : "Available";
}

function nodeStateLabel(node: ConnectorReadiness | undefined) {
  if (!node) return "Unknown";
  return ({
    available_to_install: "Available to install",
    authentication_required: "Authentication required",
    authentication_in_progress: "Connecting account…",
    authenticated: "Account connected",
    verification_required: "Verification required",
    verification_in_progress: "Verifying…",
    certification_required: "Certification required",
    certification_in_progress: "Certifying…",
    routing_disabled: "Certified · routing disabled",
    ready: "Ready for read-only routed tasks",
    needs_repair: "Needs repair",
    executable_broken: "Executable broken",
    ide_only: "IDE only",
    blocked: "Blocked",
    installing: "Installing…",
  } as Record<string, string>)[node.state] ?? node.state.replaceAll("_", " ");
}

function actionLabel(action: string | null | undefined) {
  if (!action || action === "none") return "None";
  return action.replaceAll("_", " ");
}

function authBadge(node: ConnectorReadiness | undefined) {
  if (!node) return "Not verified";
  if (node.state === "authentication_in_progress") return "Waiting for login";
  if (["authenticated", "verification_required", "certification_required", "routing_disabled", "ready"].includes(node.state)) {
    return "Verified";
  }
  if (node.state === "authentication_failed") return "Failed · retry";
  return "Not verified";
}

function heading(state: string) {
  return ({ ACCOUNT_READY: "Welcome to JoyMesh", NODE_PAIRING_REQUIRED: "Connect this machine", ENVIRONMENT_CHECK: "Check the local environment", HARNESS_SELECTION: "Choose your harnesses", INSTALLATION_REVIEW: "Review installation plans", INSTALLING: "Installing selected connectors", AUTHENTICATION_REQUIRED: "Connect harness accounts", CERTIFICATION_REQUIRED: "Prove each harness works", ROUTING_SETUP: "Set your spending boundary", FIRECONNECT_SETUP: "Configure FireConnect", FINAL_CHECK: "Final readiness check" } as Record<string, string>)[state] ?? "Set up JoyMesh";
}
function subheading(state: string) {
  return ({ ACCOUNT_READY: "Your identity is already established for this browser session.", NODE_PAIRING_REQUIRED: "Pair a JoyMesh Node so the control plane can plan connector installs.", ENVIRONMENT_CHECK: "Diagnostics come from the paired node, not the browser.", HARNESS_SELECTION: "Selecting a connector saves intent. It does not install anything.", INSTALLATION_REVIEW: "Approve the exact backend plan before execution.", INSTALLING: "Task progress and readiness are owned by the control plane.", AUTHENTICATION_REQUIRED: "Complete account login on the node, then wait for readiness.", CERTIFICATION_REQUIRED: "Certification is a separate approved task.", ROUTING_SETUP: "Spending policy is independent from installation.", FIRECONNECT_SETUP: "Optional provider routing preference.", FINAL_CHECK: "Completion requires backend readiness evidence." } as Record<string, string>)[state] ?? "";
}
