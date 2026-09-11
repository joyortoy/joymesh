import { getChatGPTUser } from "../../../../chatgpt-auth";
import { proxyControlPlane } from "../../../../../lib/control-plane";

export async function GET(
  _request: Request,
  context: { params: Promise<{ nodeId: string }> },
) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const { nodeId } = await context.params;
  if (!nodeId || nodeId.length > 200) {
    return Response.json({ detail: "Invalid node id" }, { status: 422 });
  }
  return proxyControlPlane(`/api/v1/nodes/${encodeURIComponent(nodeId)}/environment`, {
    method: "GET",
    user,
  });
}
