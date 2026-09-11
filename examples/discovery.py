"""Run a credential-free JoyMesh discovery demo."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from joymesh import JoyMesh


async def main() -> None:
    """Print a compact view of the catalogue and locally detected harnesses."""

    with TemporaryDirectory(prefix="joymesh-demo-") as directory:
        database_path = Path(directory) / "joymesh.db"
        mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{database_path}")
        await mesh.initialize()
        try:
            catalogue = mesh.list_harnesses()
            detected = await mesh.detect_harnesses()
        finally:
            await mesh.close()

    available = [item for item in detected if item.executable is not None]
    print(f"JoyMesh catalogue: {len(catalogue)} harnesses")
    print(f"Detected locally: {len(available)}")
    if not available:
        print("  No supported harness executables detected yet.")
        return

    for item in available:
        print(
            f"  {item.manifest.display_name:<24} "
            f"status={item.support_status.value:<12} "
            f"resume={'yes' if item.manifest.supports_resume else 'no'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
