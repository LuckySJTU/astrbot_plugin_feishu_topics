"""Build an uploadable plugin ZIP from an explicit allowlist (no runtime data)."""

import hashlib
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r"^version: (\S+)$", (ROOT / "metadata.yaml").read_text(), re.M)[1]
destination = ROOT / "dist" / f"astrbot_plugin_feishu_topics-{version}.zip"
destination.parent.mkdir(exist_ok=True)
files = [
    ROOT / name
    for name in (
        "__init__.py",
        "main.py",
        "metadata.yaml",
        "_conf_schema.json",
        "requirements.txt",
        "README.md",
    )
]
files.extend(sorted((ROOT / "feishu_topics").glob("*.py")))
files.extend(sorted((ROOT / "docs").glob("*.md")))
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, Path("astrbot_plugin_feishu_topics") / path.relative_to(ROOT))
print(destination)
print(f"sha256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")
