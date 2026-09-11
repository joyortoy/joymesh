# Verifier isolation v2

The explicit local-verifier route now denies network and forbidden file writes
with a kernel kill modifier. CPython's optional /dev/dtracehelper startup probe
remains denied nonfatally. setsid, setpgid and posix_spawn are denied fatally:
posix_spawn can set session attributes without making a separate setsid syscall.
This intentionally excludes legitimate uses of these calls in verification
commands. Fork/exec children remain in the owned process group under the tested
paths. It does not claim protection against arbitrary kernel or host compromise.

A Python -I -S bootstrap loads only standard-library code, applies Seatbelt via
sandbox_init, then execs the requested absolute executable. Initialization failure
does not run the requested code. This avoids creating a posix_spawn exception for
sandbox-exec's launch. Both stdout and stderr are pipes with at most 4 MiB retained
per stream; excess output causes failure and group cleanup. Captured hashes refer
to the retained bytes, not an uncaptured oversized stream. Scratch disk remains a
separate unbounded resource; this is not a general filesystem quota.

Optional JoyMux reporting uses the parent's trusted socket configuration. Native
log collection reads only kernel Sandbox records for the stopped leader PID,
within its start/end interval, and removes resource targets. Worker stdout is not
an evidence source. No event means not_observed, not absence of a denial. Collector
failure is unavailable. JoyMux receives an acknowledged parent fact and the
receipt retains the attributed event. Observation happens after the kernel stop;
it is not a guarantee of streaming telemetry or coverage of every descendant.

The local gateway rechecks executable path, inode, mode and content in addition to
the source hash, which now includes modes. Ignored dependencies and symlink target
contents outside the source set still need a hermetic dependency snapshot. Host
read access and unrestricted same-UID interference remain outside this boundary.
