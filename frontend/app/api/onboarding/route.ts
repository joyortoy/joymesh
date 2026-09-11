import { getChatGPTUser } from "../../chatgpt-auth";
import {
  CONTROL_PLANE,
  controlPlaneConfigured,
  isConnectorId,
  proxyControlPlane,
  sameOriginWrite,
} from "../../../lib/control-plane";
import { getOnboardingProgress, saveOnboardingProgress } from "../../../db/onboarding";

const STATES = new Set([
  "ACCOUNT_READY",
  "NODE_PAIRING_REQUIRED",
  "ENVIRONMENT_CHECK",
  "HARNESS_SELECTION",
  "INSTALLATION_REVIEW",
  "INSTALLING",
  "AUTHENTICATION_REQUIRED",
  "VERIFYING_ACCOUNTS",
  "CERTIFICATION_REQUIRED",
  "CERTIFYING",
  "ROUTING_SETUP",
  "FIRECONNECT_SETUP",
  "FINAL_CHECK",
  "COMPLETE",
  "LIMITED_MODE",
  "FAILED",
  "BLOCKED",
  "NODE_OFFLINE",
]);
const POLICIES = new Set(["never", "ask", "limits", "allow_with_limits"]);
const MAX_SELECTED = 40;

function mapPolicy(value: string): "never" | "ask" | "limits" {
  if (value === "allow_with_limits" || value === "limits") return "limits";
  if (value === "never") return "never";
  return "ask";
}

function toBackendPolicy(value: string): string {
  if (value === "limits") return "allow_with_limits";
  return value;
}

export async function GET() {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (controlPlaneConfigured()) {
    const snapshot = await proxyControlPlane("/api/v1/onboarding/snapshot", {
      method: "GET",
      user,
    });
    if (!snapshot.ok) return snapshot;
    const body = await snapshot.json();
    return Response.json(await composeBrowserSnapshot(body));
  }
  // Preview-only path when control plane is unavailable — not an authority.
  const local = await getOnboardingProgress(user.email);
  return Response.json({
    ...local,
    selectedConnectorIds: local.selectedHarnesses,
    selectedNodeId: local.nodeOnline ? local.nodeName || null : null,
    revision: 0,
    authority: "preview-unsynchronised",
    synchronised: false,
    pairing: null,
    environment: null,
    connectors: [],
    activeTasks: [],
    availableActions: ["configure_control_plane"],
    blockingReasons: ["control_plane_unavailable"],
  });
}

export async function PUT(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (!sameOriginWrite(request)) {
    return Response.json({ detail: "Cross-origin write rejected" }, { status: 403 });
  }
  const body: unknown = await request.json().catch(() => null);
  if (!isProgress(body)) {
    return Response.json({ detail: "Invalid onboarding progress" }, { status: 422 });
  }
  if (controlPlaneConfigured()) {
    const paid = toBackendPolicy(body.paidPolicy);
    const response = await proxyControlPlane("/api/v1/onboarding", {
      method: "PUT",
      user,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        state: body.state,
        node_id: body.selectedNodeId || null,
        pairing_id: body.pairingId ?? null,
        selected_harnesses: body.selectedHarnesses,
        limited_mode_reason: body.state === "LIMITED_MODE" ? "explicit_user_choice" : null,
        paid_route_policy: paid,
        fireconnect_enabled: body.fireconnect,
        expected_revision: typeof body.revision === "number" && body.revision > 0 ? body.revision : null,
        clear_error: true,
      }),
    });
    if (!response.ok) return response;
    // Optional write-through projection — Python remains canonical.
    const progress = await response.json();
    await saveOnboardingProgress(user.email, {
      state: String(progress.state ?? body.state),
      selectedHarnesses: body.selectedHarnesses,
      nodeName: body.nodeName,
      nodeOnline: body.nodeOnline,
      environmentChecked: body.environmentChecked,
      paidPolicy: body.paidPolicy,
      fireconnect: body.fireconnect,
    }).catch(() => undefined);
    const snapshot = await proxyControlPlane("/api/v1/onboarding/snapshot", {
      method: "GET",
      user,
    });
    if (snapshot.ok) {
      return Response.json(await composeBrowserSnapshot(await snapshot.json()));
    }
    return Response.json({
      ...body,
      revision: progress.revision ?? body.revision ?? 1,
      authority: "python-control-plane",
      synchronised: true,
    });
  }
  const saved = await saveOnboardingProgress(user.email, body);
  return Response.json({
    ...saved,
    selectedConnectorIds: saved.selectedHarnesses,
    revision: 0,
    authority: "preview-unsynchronised",
    synchronised: false,
    blockingReasons: ["control_plane_unavailable"],
  });
}

async function composeBrowserSnapshot(raw: Record<string, unknown>) {
  const progress = (raw.progress as Record<string, unknown> | undefined) ?? {};
  const policy =
    ((raw.routing_preferences as { paid_route_policy?: string } | undefined)?.paid_route_policy) ||
    "ask";
  return {
    state: raw.state ?? progress.state ?? "ACCOUNT_READY",
    selectedHarnesses: (raw.selected_connector_ids as string[]) ?? [],
    selectedConnectorIds: (raw.selected_connector_ids as string[]) ?? [],
    selectedNodeId: (raw.selected_node_id as string | null) ?? null,
    nodeName: (progress as { node_id?: string }).node_id ?? "",
    nodeOnline: Boolean(raw.selected_node_id),
    environmentChecked: Boolean(raw.environment),
    paidPolicy: mapPolicy(String(policy)),
    fireconnect: Boolean(
      (raw.fireconnect_preferences as { enabled?: boolean } | undefined)?.enabled,
    ),
    revision: Number(raw.revision ?? 1),
    pairing: raw.pairing ?? null,
    environment: raw.environment ?? null,
    connectors: raw.connectors ?? [],
    activeTasks: raw.active_tasks ?? [],
    availableActions: raw.available_actions ?? [],
    blockingReasons: raw.blocking_reasons ?? [],
    limitedMode: Boolean(raw.limited_mode),
    authority: raw.authority ?? "python-control-plane",
    synchronised: raw.synchronised !== false,
    updatedAt: raw.updated_at ?? new Date().toISOString(),
    controlPlane: CONTROL_PLANE || null,
  };
}

function isProgress(value: unknown): value is {
  state: string;
  selectedHarnesses: string[];
  selectedNodeId?: string | null;
  nodeName: string;
  nodeOnline: boolean;
  environmentChecked: boolean;
  paidPolicy: "never" | "ask" | "limits";
  fireconnect: boolean;
  revision?: number;
  pairingId?: string | null;
} {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.state === "string" &&
    STATES.has(item.state) &&
    Array.isArray(item.selectedHarnesses) &&
    item.selectedHarnesses.length <= MAX_SELECTED &&
    item.selectedHarnesses.every(isConnectorId) &&
    typeof item.nodeName === "string" &&
    item.nodeName.length <= 200 &&
    typeof item.nodeOnline === "boolean" &&
    typeof item.environmentChecked === "boolean" &&
    typeof item.paidPolicy === "string" &&
    POLICIES.has(item.paidPolicy) &&
    typeof item.fireconnect === "boolean"
  );
}
