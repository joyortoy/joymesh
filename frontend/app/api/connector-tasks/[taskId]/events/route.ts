const CONTROL_PLANE = process.env.JOYMESH_CONTROL_PLANE_URL?.replace(/\/$/, "") ?? "";

export async function GET(
  request: Request,
  context: { params: Promise<{ taskId: string }> },
) {
  const { taskId } = await context.params;
  if (!CONTROL_PLANE) {
    return Response.json({ detail: "Control plane URL is not configured" }, { status: 503 });
  }
  const url = new URL(request.url);
  const after = url.searchParams.get("after") ?? "0";
  const stream = url.searchParams.get("stream") === "1";
  const target = stream
    ? `${CONTROL_PLANE}/connector-tasks/${encodeURIComponent(taskId)}/events/stream`
    : `${CONTROL_PLANE}/connector-tasks/${encodeURIComponent(taskId)}/events?after=${encodeURIComponent(after)}`;
  const response = await fetch(target, {
    headers: { Accept: stream ? "text/event-stream" : "application/json" },
    cache: "no-store",
  });
  if (stream) {
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });
  }
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
