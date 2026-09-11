const CONTROL_PLANE = process.env.JOYMESH_CONTROL_PLANE_URL?.replace(/\/$/, "") ?? "";

export async function GET(
  _request: Request,
  context: { params: Promise<{ nodeId: string }> },
) {
  const { nodeId } = await context.params;
  if (!nodeId || nodeId.length > 200) {
    return Response.json({ detail: "Invalid node id" }, { status: 422 });
  }
  if (!CONTROL_PLANE) {
    return Response.json([]);
  }
  const { getChatGPTUser } = await import("../../../../chatgpt-auth");
  const { identityHeaders } = await import("../../../../../lib/control-plane");
  const user = await getChatGPTUser();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (user) Object.assign(headers, identityHeaders(user));
  const response = await fetch(
    `${CONTROL_PLANE}/api/v1/nodes/${encodeURIComponent(nodeId)}/connectors/readiness`,
    {
      headers,
      cache: "no-store",
    },
  );
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
