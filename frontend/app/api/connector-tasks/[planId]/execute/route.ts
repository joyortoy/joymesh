import { getChatGPTUser } from "../../../../chatgpt-auth";
import { proxyControlPlane, sameOriginWrite } from "../../../../../lib/control-plane";

export async function POST(
  request: Request,
  context: { params: Promise<{ planId: string }> },
) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (!sameOriginWrite(request)) {
    return Response.json({ detail: "Cross-origin write rejected" }, { status: 403 });
  }
  const { planId } = await context.params;
  const body = (await request.json().catch(() => null)) as {
    plan_hash?: string;
    approved?: boolean;
  } | null;
  if (!body || typeof body.plan_hash !== "string" || typeof body.approved !== "boolean") {
    return Response.json({ detail: "plan_hash and approved are required" }, { status: 422 });
  }
  return proxyControlPlane(`/connector-tasks/${encodeURIComponent(planId)}/execute`, {
    method: "POST",
    user,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plan_hash: body.plan_hash,
      approved: body.approved,
    }),
  });
}
