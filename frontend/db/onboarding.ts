import { env } from "cloudflare:workers";

type SavedProgress = {
  state: string;
  selectedHarnesses: string[];
  nodeName: string;
  nodeOnline: boolean;
  environmentChecked: boolean;
  paidPolicy: "never" | "ask" | "limits";
  fireconnect: boolean;
  updatedAt: string;
};

const DEFAULTS: SavedProgress = {
  state: "ACCOUNT_READY",
  selectedHarnesses: [],
  nodeName: "",
  nodeOnline: false,
  environmentChecked: false,
  paidPolicy: "ask",
  fireconnect: false,
  updatedAt: new Date(0).toISOString(),
};

let schemaReady: Promise<void> | null = null;

async function ensureSchema() {
  if (!env.DB) throw new Error("D1 binding DB is unavailable");
  schemaReady ??= (async () => {
    await env.DB.prepare(`
      CREATE TABLE IF NOT EXISTS onboarding_progress (
        id TEXT PRIMARY KEY,
        owner_email TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL DEFAULT 'ACCOUNT_READY',
        selected_harnesses_json TEXT NOT NULL DEFAULT '[]',
        node_name TEXT NOT NULL DEFAULT '',
        node_online INTEGER NOT NULL DEFAULT 0,
        environment_checked INTEGER NOT NULL DEFAULT 0,
        paid_policy TEXT NOT NULL DEFAULT 'ask',
        fireconnect INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
      )
    `).run();
    await env.DB.prepare(`
      CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        owner_email TEXT NOT NULL,
        action TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
      )
    `).run();
    await env.DB.prepare(`
      CREATE INDEX IF NOT EXISTS audit_owner_created_idx
      ON audit_events (owner_email, created_at)
    `).run();
  })();
  await schemaReady;
}

export async function getOnboardingProgress(ownerEmail: string): Promise<SavedProgress> {
  await ensureSchema();
  const row = await env.DB.prepare(`
    SELECT state, selected_harnesses_json, node_name, node_online,
           environment_checked, paid_policy, fireconnect, updated_at
    FROM onboarding_progress
    WHERE owner_email = ?
  `).bind(ownerEmail).first<{
    state: string;
    selected_harnesses_json: string;
    node_name: string;
    node_online: number;
    environment_checked: number;
    paid_policy: "never" | "ask" | "limits";
    fireconnect: number;
    updated_at: string;
  }>();
  if (!row) return DEFAULTS;
  return {
    state: row.state,
    selectedHarnesses: safeHarnesses(row.selected_harnesses_json),
    nodeName: row.node_name,
    nodeOnline: Boolean(row.node_online),
    environmentChecked: Boolean(row.environment_checked),
    paidPolicy: row.paid_policy,
    fireconnect: Boolean(row.fireconnect),
    updatedAt: row.updated_at,
  };
}

export async function saveOnboardingProgress(
  ownerEmail: string,
  value: Omit<SavedProgress, "updatedAt">,
): Promise<SavedProgress> {
  await ensureSchema();
  const updatedAt = new Date().toISOString();
  const id = crypto.randomUUID();
  await env.DB.prepare(`
    INSERT INTO onboarding_progress (
      id, owner_email, state, selected_harnesses_json, node_name,
      node_online, environment_checked, paid_policy, fireconnect, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(owner_email) DO UPDATE SET
      state = excluded.state,
      selected_harnesses_json = excluded.selected_harnesses_json,
      node_name = excluded.node_name,
      node_online = excluded.node_online,
      environment_checked = excluded.environment_checked,
      paid_policy = excluded.paid_policy,
      fireconnect = excluded.fireconnect,
      updated_at = excluded.updated_at
  `).bind(
    id,
    ownerEmail,
    value.state,
    JSON.stringify(value.selectedHarnesses),
    value.nodeName,
    value.nodeOnline ? 1 : 0,
    value.environmentChecked ? 1 : 0,
    value.paidPolicy,
    value.fireconnect ? 1 : 0,
    updatedAt,
  ).run();
  await env.DB.prepare(`
    INSERT INTO audit_events (id, owner_email, action, metadata_json, created_at)
    VALUES (?, ?, ?, ?, ?)
  `).bind(
    crypto.randomUUID(),
    ownerEmail,
    "onboarding.progress.saved",
    JSON.stringify({ state: value.state }),
    updatedAt,
  ).run();
  return { ...value, updatedAt };
}

function safeHarnesses(value: string): string[] {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) && parsed.every((item) => typeof item === "string")
      ? parsed
      : [];
  } catch {
    return [];
  }
}
