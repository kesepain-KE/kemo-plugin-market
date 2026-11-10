"""索引生成：从 ZIP 到 index.json 的完整链路。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kemo_plugin_market import config as config_module
from kemo_plugin_market.builder import BuildError, build, write
from kemo_plugin_market.manifest import PackageError, read_manifest, sha256_of
from kemo_plugin_market.schema import validate_document

from conftest import MINIMAL_MANIFEST, make_package_bytes, write_package


def test_build_single_package(market_root: Path):
    package = write_package(market_root / "packages")
    result = build(market_root)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == "demo_plugin"
    assert entry.version == "1.0.0"
    assert entry.name == "演示插件"
    assert entry.url == "https://plugins.example.com/packages/" + package.name
    # 摘要必须等于文件字节摘要
    assert entry.sha256 == sha256_of(package)
    assert entry.sha256 == hashlib.sha256(package.read_bytes()).hexdigest()


def test_build_output_is_deterministic(market_root: Path):
    write_package(market_root / "packages")
    first = build(market_root).payload
    second = build(market_root).payload
    assert first == second


def test_build_payload_is_valid_json_with_utf8(market_root: Path):
    write_package(market_root / "packages")
    result = build(market_root)
    document = json.loads(result.payload)
    assert document["schema_version"] == 1
    # 中文不被转义，便于人工核对与 diff
    assert "演示插件" in result.payload
    assert validate_document(document)


def test_build_empty_packages_dir(market_root: Path):
    result = build(market_root)
    assert result.entries == []
    assert json.loads(result.payload) == {"schema_version": 1, "plugins": []}


def test_build_sorts_by_id_then_version(market_root: Path):
    write_package(
        market_root / "packages",
        {**MINIMAL_MANIFEST, "id": "zeta", "version": "1.0.0"},
    )
    write_package(
        market_root / "packages",
        {**MINIMAL_MANIFEST, "id": "alpha", "version": "2.0.0"},
    )
    write_package(
        market_root / "packages",
        {**MINIMAL_MANIFEST, "id": "alpha", "version": "1.0.0"},
    )

    result = build(market_root)
    assert [(e.id, e.version) for e in result.entries] == [
        ("alpha", "1.0.0"),
        ("alpha", "2.0.0"),
        ("zeta", "1.0.0"),
    ]


def test_build_rejects_duplicate_id_version(market_root: Path):
    data = make_package_bytes()
    (market_root / "packages" / "a.zip").write_bytes(data)
    (market_root / "packages" / "b.zip").write_bytes(data)

    with pytest.raises(BuildError, match="重复条目"):
        build(market_root)


def test_build_rejects_package_without_manifest(market_root: Path):
    import zipfile

    path = market_root / "packages" / "broken.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backend/main.py", "")

    with pytest.raises(PackageError, match="根目录缺少 plugin.yaml"):
        build(market_root)


def test_build_reports_nested_manifest(market_root: Path):
    path = market_root / "packages" / "nested.zip"
    path.write_bytes(make_package_bytes(nest_under="demo"))

    with pytest.raises(PackageError, match="多套了一层目录"):
        build(market_root)


def test_build_rejects_non_zip(market_root: Path):
    (market_root / "packages" / "fake.zip").write_bytes(b"not a zip")
    with pytest.raises(PackageError, match="不是有效的 ZIP"):
        build(market_root)


def test_build_ignores_non_zip_files(market_root: Path):
    write_package(market_root / "packages")
    (market_root / "packages" / "README.txt").write_text("忽略我", encoding="utf-8")
    assert len(build(market_root).entries) == 1


def test_overrides_apply(market_root: Path):
    (market_root / "market.yaml").write_text(
        "base_url: https://plugins.example.com\n"
        "packages_dir: packages\n"
        "overrides:\n"
        "  demo_plugin:\n"
        "    category: model_account\n"
        "    capabilities: [llm, tools]\n"
        "    summary: 覆盖后的说明\n"
        "    author: 覆盖作者\n"
        "    compatibility: '>=2.1.0,<3.0.0'\n",
        encoding="utf-8",
    )
    write_package(market_root / "packages")

    entry = build(market_root).entries[0]
    assert entry.category == "model_account"
    assert entry.capabilities == ["llm", "tools"]
    assert entry.summary == "覆盖后的说明"
    assert entry.author == "覆盖作者"
    assert entry.compatibility == ">=2.1.0,<3.0.0"


def test_unused_override_warns(market_root: Path):
    (market_root / "market.yaml").write_text(
        "base_url: https://plugins.example.com\n"
        "packages_dir: packages\n"
        "overrides:\n"
        "  not_installed:\n"
        "    category: model_account\n",
        encoding="utf-8",
    )
    result = build(market_root)
    assert any("not_installed" in w for w in result.warnings)


def test_provider_disabled_has_no_capabilities(market_root: Path):
    write_package(
        market_root / "packages",
        {**MINIMAL_MANIFEST, "provider": {"enabled": False, "provider_id": "noop"}},
    )
    entry = build(market_root).entries[0]
    assert entry.capabilities == []


def test_write_is_atomic_and_creates_file(market_root: Path):
    write_package(market_root / "packages")
    result = build(market_root)
    target = write(market_root, result)

    assert target == market_root / "index.json"
    assert target.read_text(encoding="utf-8") == result.payload
    # 不留下临时文件
    assert not (market_root / ".index.json.tmp").exists()


def test_build_requires_market_yaml(market_root: Path):
    (market_root / "market.yaml").unlink()
    with pytest.raises(config_module.ConfigError, match="不存在"):
        build(market_root)


def test_manifest_rejects_oversized_package(market_root: Path):
    from kemo_plugin_market.schema import MAX_PACKAGE_BYTES

    path = market_root / "packages" / "huge.zip"
    path.write_bytes(b"\0" * (MAX_PACKAGE_BYTES + 1))
    with pytest.raises(PackageError, match="超过网关"):
        read_manifest(path)


def test_url_uses_filename_not_id(market_root: Path):
    """url 取实际文件名，改名后索引必须跟着变，否则下载 404。"""
    path = write_package(market_root / "packages", filename="renamed-package.zip")
    entry = build(market_root).entries[0]
    assert entry.url.endswith("/packages/renamed-package.zip")
    assert path.name in entry.url
