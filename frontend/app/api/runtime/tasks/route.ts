import { NextRequest, NextResponse } from "next/server";

const CONTROL_PLANE = process.env.JOYMESH_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

export async function POST(request: NextRequest) {
  const body = await request.json();
  const response = await fetch(`${CONTROL_PLANE}/runtime/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  return NextResponse.json(payload, { status: response.status });
}
