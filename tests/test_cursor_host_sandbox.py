import signal
import subprocess
import sys
from pathlib import Path

import pytest

from joymesh.cursor_sandbox import write_confinement_profile


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel sandbox")
def test_sandbox_can_signal_self_and_own_child(tmp_path: Path) -> None:
    profile = write_confinement_profile(tmp_path, tmp_path, [], read_only=False)
    script = (
        "import os,signal,subprocess,sys; "
        "signal.signal(signal.SIGUSR1, signal.SIG_IGN); "
        "os.kill(os.getpid(), signal.SIGUSR1); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
        "child.terminate(); assert child.wait(timeout=3)==-signal.SIGTERM"
    )
    result = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-c", script],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel sandbox")
def test_cannot_signal_host_sentinel(tmp_path: Path) -> None:
    """A child must not be able to disable a trusted host monitor with signals."""
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    profile = write_confinement_profile(workspace, scratch, [], read_only=False)
    with subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import signal,time; signal.signal(signal.SIGUSR1, signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(15)",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    ) as sentinel:
        try:
            assert sentinel.stdout.readline() == b"ready\n"
            probe = [
                sys.executable,
                "-c",
                f"import os,signal; os.kill({sentinel.pid}, signal.SIGUSR1)",
            ]
            # Prove the target is reachable and the signal itself is valid.
            assert subprocess.run(probe, timeout=5).returncode == 0
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", profile, *probe],
                capture_output=True,
                timeout=5,
            )
            assert result.returncode != 0
            assert b"Operation not permitted" in result.stderr
            assert sentinel.poll() is None
        finally:
            sentinel.send_signal(signal.SIGTERM)
            sentinel.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel sandbox")
@pytest.mark.parametrize("read_only", [False, True])
def test_kernel_denies_outside_and_readonly_writes(tmp_path: Path, read_only: bool) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    outside = tmp_path / "outside"
    profile = write_confinement_profile(workspace, scratch, [], read_only=read_only)

    def write(path):
        return subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                profile,
                "/bin/sh",
                "-c",
                'printf proof > "$1"',
                "probe",
                str(path),
            ],
            capture_output=True,
            timeout=5,
        ).returncode

    assert write(outside) != 0
    assert not outside.exists()
    assert (write(workspace / "inside") == 0) is (not read_only)
    assert write(scratch / "temp") == 0
    (workspace / "escape").symlink_to(outside)
    assert write(workspace / "escape") != 0
    assert not outside.exists()


def test_runtime_workspace_overlap_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlaps"):
        write_confinement_profile(tmp_path, tmp_path / "scratch", [tmp_path], read_only=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel sandbox")
def test_local_broker_connections_are_denied(tmp_path: Path) -> None:
    import socket
    import tempfile

    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    profile = write_confinement_profile(workspace, scratch, [], read_only=False)
    with (
        tempfile.TemporaryDirectory(prefix="jm-", dir="/tmp") as short,
        socket.socket(socket.AF_UNIX) as unix,
        socket.socket() as tcp,
    ):
        path = str(Path(short) / "broker.sock")
        unix.bind(path)
        unix.listen()
        tcp.bind(("127.0.0.1", 0))
        tcp.listen()
        for script in (
            f"import socket; s=socket.socket(socket.AF_UNIX); s.connect({path!r})",
            f"import socket; s=socket.socket(); s.connect({tcp.getsockname()!r})",
        ):
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-c", script],
                capture_output=True,
                timeout=5,
            )
            assert result.returncode != 0
            assert b"Operation not permitted" in result.stderr


@pytest.mark.parametrize("workspace_relative", [".", "runtime", "runtime/child"])
def test_workspace_must_not_enclose_or_enter_runtime(
    tmp_path: Path, workspace_relative: str
) -> None:
    with pytest.raises(ValueError, match="overlaps"):
        write_confinement_profile(
            tmp_path / workspace_relative,
            tmp_path / "scratch",
            [tmp_path / "runtime"],
            read_only=False,
        )


def test_runtime_symlink_cannot_expand_write_exception(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    runtime = tmp_path / "runtime"
    runtime.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="redirected"):
        write_confinement_profile(
            tmp_path / "workspace", tmp_path / "scratch", [runtime], read_only=True
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel sandbox")
def test_nonloopback_host_broker_is_denied(tmp_path: Path) -> None:
    import socket

    # Selecting a route sends no packet and never contacts the documentation IP.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        try:
            route.connect(("192.0.2.1", 9))
        except OSError:
            pytest.skip("No IPv4 route for local interface fixture")
        host_ip = route.getsockname()[0]
    if host_ip.startswith("127."):
        pytest.skip("No non-loopback IPv4 interface")
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    profile = write_confinement_profile(workspace, scratch, [], read_only=False)
    with socket.socket() as broker:
        broker.bind((host_ip, 0))
        broker.listen(2)
        broker.settimeout(2)
        # Positive control: the fixed test endpoint is reachable outside the sandbox.
        with socket.create_connection(broker.getsockname(), timeout=2):
            connection, _ = broker.accept()
            connection.close()
        script = f"import socket; socket.create_connection({broker.getsockname()!r}, timeout=2)"
        result = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-c", script],
            capture_output=True,
            timeout=5,
        )
        assert result.returncode != 0
        assert b"Operation not permitted" in result.stderr
