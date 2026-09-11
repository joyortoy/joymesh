import { getChatGPTUser } from "../../../chatgpt-auth";
import { proxyControlPlane, sameOriginWrite } from "../../../../lib/control-plane";

function pkceChallenge(): string {
  // Control plane accepts a 43–128 char challenge; browser generates a random verifier-shaped value.
  const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_";
  let out = "";
  const bytes = crypto.getRandomValues(new Uint8Array(64));
  for (const value of bytes) out += alphabet[value % alphabet.length];
  return out;
}

export async function POST(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return Response.json({ detail: "Authentication required" }, { status: 401 });
  if (!sameOriginWrite(request)) {
    return Response.json({ detail: "Cross-origin write rejected" }, { status: 403 });
  }
  const body = (await request.json().catch(() => ({}))) as { code_challenge?: string };
  return proxyControlPlane("/api/v1/nodes/pairing/start", {
    method: "POST",
    user,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code_challenge: body.code_challenge || pkceChallenge() }),
  });
}
