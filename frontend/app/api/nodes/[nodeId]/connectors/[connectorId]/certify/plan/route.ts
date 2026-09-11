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
  if (!nodeId || !isConnectorId(connectorId)) {
    return Response.json({ detail: "Invalid node or connector id" }, { status: 422 });
  }
  return proxyControlPlane(
    `/api/v1/nodes/${encodeURIComponent(nodeId)}/connectors/${encodeURIComponent(connectorId)}/certify/plan`,
    { method: "POST", user },
  );
}
