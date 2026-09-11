import { getChatGPTUser } from "../../../../../../chatgpt-auth";
import {
  isConnectorId,
  proxyControlPlane,
  sameOriginWrite,
} from "../../../../../../../lib/control-plane";

export async function POST(
  request: Request,
  context: { params: Promise<{ nodeId: string; connectorId: string }> },
) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (!sameOriginWrite(request)) {
    return Response.json({ detail: "Cross-origin write rejected" }, { status: 403 });
  }
  const { nodeId, connectorId } = await context.params;
  if (!nodeId || nodeId.length > 200 || !isConnectorId(connectorId)) {
    return Response.json({ detail: "Invalid node or connector id" }, { status: 422 });
  }
  const body = (await request.json().catch(() => ({}))) as {
    method_id?: string;
    platform?: string;
  };
  const query = new URLSearchParams();
  if (body.method_id) query.set("method_id", body.method_id);
  if (body.platform) query.set("platform", body.platform);
  const suffix = query.toString() ? `?${query}` : "";
  return proxyControlPlane(
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/connectors/${encodeURIComponent(connectorId)}/install/plan${suffix}`,
    { method: "POST", user },
  );
}
