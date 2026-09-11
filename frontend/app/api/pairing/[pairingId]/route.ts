import { getChatGPTUser } from "../../../../chatgpt-auth";
import { proxyControlPlane } from "../../../../../lib/control-plane";

export async function GET(
  _request: Request,
  context: { params: Promise<{ pairingId: string }> },
) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const { pairingId } = await context.params;
  if (!pairingId || pairingId.length > 100) {
    return Response.json({ detail: "Invalid pairing id" }, { status: 422 });
  }
  return proxyControlPlane(`/api/v1/nodes/pairing/${encodeURIComponent(pairingId)}`, {
    method: "GET",
    user,
  });
}
