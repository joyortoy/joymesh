import shlex
import sys

import pytest

from joymesh.verification import verify_commands

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS host verifier")


def command(code):
    return shlex.join([sys.executable, "-c", code])


def test_real_success_and_failure(tmp_path):
    receipts = verify_commands([command("print('passed')"), command("raise SystemExit(7)")], str(tmp_path), 5)
    assert [r["exit_code"] for r in receipts] == [0, 7]
    assert receipts[0]["stdout"]["tail"].strip() == "passed"


def test_repository_write_denied(tmp_path):
    receipt = verify_commands([command("open('forbidden', 'w').write('x')")], str(tmp_path), 5)[0]
    assert receipt["exit_code"] == -9
    assert not (tmp_path / "forbidden").exists()


def test_network_denied(tmp_path):
    receipt = verify_commands([command("import socket; socket.create_connection(('127.0.0.1', 9))")], str(tmp_path), 5)[0]
    assert receipt["exit_code"] == -9


def test_timeout(tmp_path):
    receipt = verify_commands([command("import time; time.sleep(10)")], str(tmp_path), 0.1)[0]
    assert receipt["failure"] == "timeout"
    assert receipt["exit_code"] != 0


def test_output_limit(tmp_path):
    receipt = verify_commands([command("import os; os.write(1,b'x'*5000000)")], str(tmp_path), 5)[0]
    assert receipt["failure"] == "output_limit"
    assert receipt["stdout"]["captured_bytes"] <= 4 * 1024 * 1024


@pytest.mark.parametrize("code", ["import os; os.setsid()", "import os; os.setpgid(0,0)",
    "import os; os.posix_spawn('/bin/sleep',['sleep','0.01'],{},setsid=True)"])
def test_session_escape_paths_killed(tmp_path, code):
    receipt = verify_commands([command(code)], str(tmp_path), 5)[0]
    assert receipt["exit_code"] == -9


def test_closed_pipes_do_not_bypass_timeout(tmp_path):
    receipt = verify_commands([command("import os,time; os.close(1); os.close(2); time.sleep(5)")], str(tmp_path), 0.2)[0]
    assert receipt["failure"] == "timeout"


@pytest.mark.parametrize("value", ["python -V", "/bin/echo ok && /bin/echo bad"])
def test_ambiguous_command_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        verify_commands([value], str(tmp_path), 5)
