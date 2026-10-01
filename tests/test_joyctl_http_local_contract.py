"""Real local HTTP/SQLite handler contract; not a hosted-dot execution test."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))

from joyctl_http_binding import JoyCtlHTTPBinding, VerifiedSession
from sam_dot_bridge import Bridge, TransportContext

hosted = pytest.importorskip("joyctl.hosted")
hosted_web = pytest.importorskip("joyctl.hosted_web")


def test_local_draft_roundtrip_and_viewer_denial(tmp_path):
    plane = hosted.HostedControlPlane.sqlite(tmp_path / "contract.sqlite3")
    user = plane.create_user("owner@example.test", "Test owner")
    org = plane.create_organisation(user["id"], "Test org")
    owner = plane.principal(user["id"], org["id"])
    workspace = plane.create_workspace(owner, "workspace")
    project = plane.create_project(owner, workspace["id"], "project", "main")
    ctx = TransportContext(org["id"], user["id"])
    # Existing development auth helper, disposable SQLite fixture only.
    token = plane.issue_human_access_token(owner)
    server = hosted_web.HostedWebServer(plane, host="127.0.0.1", port=0)
    server.start()
    try:
        endpoint = f"http://127.0.0.1:{server.port}"
        adapter = JoyCtlHTTPBinding(
            endpoint, lambda _: VerifiedSession(ctx, token), allow_loopback_http=True
        )
        bridge = Bridge(adapter)
        mission = bridge.call(
            ctx,
            "intent_submit",
            {
                "workspace_id": workspace["id"],
                "project_id": project["id"],
                "title": "Disposable safe draft",
            },
        )
        assert mission["status"] == "draft"
        assert mission["created_by_user_id"] == user["id"]
        assert bridge.call(ctx, "mission_fetch", {"mission_id": mission["id"]}) == mission

        viewer_user = plane.create_user("viewer@example.test", "Test viewer")
        plane.add_membership(owner, viewer_user["id"], "viewer")
        viewer = plane.principal(viewer_user["id"], org["id"])
        viewer_ctx = TransportContext(org["id"], viewer_user["id"])
        viewer_token = plane.issue_human_access_token(viewer)
        denied = Bridge(
            JoyCtlHTTPBinding(
                endpoint,
                lambda _: VerifiedSession(viewer_ctx, viewer_token),
                allow_loopback_http=True,
            )
        )
        with pytest.raises(PermissionError, match="denied"):
            denied.call(
                viewer_ctx,
                "intent_submit",
                {
                    "workspace_id": workspace["id"],
                    "project_id": project["id"],
                    "title": "Forbidden draft",
                },
            )
        assert len(plane.list_missions(owner)) == 1
    finally:
        server.stop()
