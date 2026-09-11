import { getChatGPTUser } from "../../../../../chatgpt-auth";
import { proxyControlPlane, sameOriginWrite } from "../../../../../../lib/control-plane";

export async function POST(
  request: Request,
  context: { params: Promise<{ pairingId: string }> },
) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (!sameOriginWrite(request)) {
    return Response.json({ detail: "Cross-origin write rejected" }, { status: 403 });
  }
  const { pairingId } = await context.params;
  return proxyControlPlane(`/api/v1/nodes/pairing/${encodeURIComponent(pairingId)}/cancel`, {
    method: "POST",
    user,
  });
}
