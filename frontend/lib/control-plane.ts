/** Shared Sites BFF helpers for the JoyMesh Python control plane. */

import { createHash } from "node:crypto";

import type { ChatGPTUser } from "../app/chatgpt-auth";

export const CONTROL_PLANE = process.env.JOYMESH_CONTROL_PLANE_URL?.replace(/\/$/, "") ?? "";

export function controlPlaneConfigured(): boolean {
  return Boolean(CONTROL_PLANE);
}

export function identityHeaders(user: ChatGPTUser): Record<string, string> {
  const digest = createHash("sha256").update(user.email.toLowerCase()).digest("hex").slice(0, 32);
  return {
    "x-joymesh-user-id": `user-${digest}`,
    "x-joymesh-organisation-id": `org-${digest}`,
    "x-joymesh-workspace-id": `workspace-${digest}`,
    "x-joymesh-browser-session-id": `session-${digest}`,
    Accept: "application/json",
  };
}

export async function proxyControlPlane(
  path: string,
  init: RequestInit & { user: ChatGPTUser },
): Promise<Response> {
  if (!CONTROL_PLANE) {
    return Response.json(
      {
        detail: "Control plane URL is not configured",
        code: "control_plane_unavailable",
      },
      { status: 503 },
    );
  }
  const headers = new Headers(init.headers);
  for (const [key, value] of Object.entries(identityHeaders(init.user))) {
    headers.set(key, value);
  }
  const rest = { ...init };
  delete (rest as { user?: ChatGPTUser }).user;
  const response = await fetch(`${CONTROL_PLANE}${path}`, {
    ...rest,
    headers,
    cache: "no-store",
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
  });
}

export function sameOriginWrite(request: Request): boolean {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = request.headers.get("origin");
  if (!origin) return true;
  return new URL(origin).host === new URL(request.url).host;
}

export function isConnectorId(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9][a-z0-9-]*$/.test(value) && value.length <= 100;
}
