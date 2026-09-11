import { NextResponse } from "next/server";

const CONTROL_PLANE = process.env.JOYMESH_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

type Segment = "candidates" | "events" | "lease" | "attempts" | "audit";

export async function GET(
  _request: Request,
  context: { params: Promise<{ taskId: string; segment: Segment }> },
) {
  const { taskId, segment } = await context.params;
  const response = await fetch(
    `${CONTROL_PLANE}/runtime/tasks/${encodeURIComponent(taskId)}/${segment}`,
  );
  const payload = await response.json();
  return NextResponse.json(payload, { status: response.status });
}
