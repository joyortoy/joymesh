# Platform installation

Plans distinguish macOS, Linux, native Windows, WSL, and containers. WSL discovery never proves
a native Windows installation. Package-manager selection is explicit and uses reviewed package
identifiers.

JoyMesh never requests administrator privileges automatically, silently changes shell profiles,
overwrites credentials, or invokes `shell=True`. Official scripts require allowlisted origins,
download-before-execute review, and a plan-bound digest. Uninstall is exposed only for a known,
bounded ownership mechanism.
