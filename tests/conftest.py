"""测试共用夹具。

刻意不依赖 kemo-adapter-api：这里直接按 SDK v1 的包结构造最小插件包，
以便在任何机器上都能跑，不需要先装网关。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import yaml

MINIMAL_MANIFEST = {
    "schema_version": 1,
    "id": "demo_plugin",
    "name": "演示插件",
    "version": "1.0.0",
    "author": "测试作者",
    "category": "provider_extension",
    "summary": "用于测试的演示插件",
    "compatibility": {"gateway": ">=2.0.0,<3.0.0"},
    "runtime": {
        "kind": "process",
        "entry": "backend/main.py",
        "protocol": "kemo-plugin-rpc-v1",
        "timeout": 120,
        "concurrency": 2,
    },
    "provider": {"enabled": True, "provider_id": "demo-plugin", "model_discovery": "dynamic"},
}


def make_package_bytes(
    manifest: dict | None = None,
    *,
    extra_files: dict[str, str] | None = None,
    nest_under: str | None = None,
) -> bytes:
    """构造一个最小可用的插件 ZIP。"""
    data = dict(MINIMAL_MANIFEST if manifest is None else manifest)
    prefix = f"{nest_under}/" if nest_under else ""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + "plugin.yaml", yaml.safe_dump(data, allow_unicode=True))
        archive.writestr(prefix + "backend/main.py", "def create_plugin():\n    pass\n")
        for name, content in (extra_files or {}).items():
            archive.writestr(prefix + name, content)
    return buffer.getvalue()


def write_package(
    directory: Path,
    manifest: dict | None = None,
    *,
    filename: str | None = None,
    nest_under: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = dict(MINIMAL_MANIFEST if manifest is None else manifest)
    name = filename or f"{data.get('id', 'demo_plugin')}-{data.get('version', '1.0.0')}.zip"
    path = directory / name
    path.write_bytes(make_package_bytes(data, nest_under=nest_under))
    return path


@pytest.fixture
def market_root(tmp_path: Path) -> Path:
    """一个已配置好的市场内容根。"""
    root = tmp_path / "market"
    (root / "packages").mkdir(parents=True)
    (root / "market.yaml").write_text(
        "base_url: https://plugins.example.com\n"
        "packages_dir: packages\n",
        encoding="utf-8",
    )
    return root
