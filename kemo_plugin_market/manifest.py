"""从插件 ZIP 中读取 plugin.yaml。

不依赖 kemo-adapter-api 的源码包：这里只解析索引需要的几个字段，
并保留网关在安装时才做的那部分约束（id 格式、版本可解析、清单在 ZIP 根目录）。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

import yaml

from .schema import MAX_PACKAGE_BYTES, EntryError, validate_id, validate_version

MANIFEST_NAME = "plugin.yaml"


class PackageError(ValueError):
    """插件包不合法。"""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, Any]:
    """读取 ZIP 根目录的 plugin.yaml；拒绝嵌套目录与非 ZIP 文件。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackageError(f"{path.name}: 无法读取文件（{exc}）") from None
    if size > MAX_PACKAGE_BYTES:
        limit_mb = MAX_PACKAGE_BYTES // (1024 * 1024)
        raise PackageError(f"{path.name}: {size} 字节超过网关 {limit_mb} MiB 上限")

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if MANIFEST_NAME not in names:
                nested = next(
                    (n for n in names if n.endswith("/" + MANIFEST_NAME)), None
                )
                hint = (
                    f"（发现 {nested}，说明包内多套了一层目录）" if nested else ""
                )
                raise PackageError(
                    f"{path.name}: ZIP 根目录缺少 {MANIFEST_NAME}{hint}"
                )
            raw = archive.read(MANIFEST_NAME)
    except zipfile.BadZipFile:
        raise PackageError(f"{path.name}: 不是有效的 ZIP") from None

    try:
        value = yaml.safe_load(raw.decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PackageError(f"{path.name}: {MANIFEST_NAME} 无法解析（{exc}）") from None
    if not isinstance(value, dict):
        raise PackageError(f"{path.name}: {MANIFEST_NAME} 顶层必须是映射")
    return value


def manifest_fields(manifest: dict[str, Any], *, package: Path) -> dict[str, Any]:
    """取出并校验索引需要的清单字段。"""
    try:
        identity = validate_id(manifest.get("id"))
    except EntryError as exc:
        raise PackageError(f"{package.name}: 清单 id 非法（{exc}）") from None

    try:
        version = validate_version(str(manifest.get("version") or ""))
    except EntryError as exc:
        raise PackageError(f"{package.name}: {exc}") from None

    name = manifest.get("name") or identity
    if not isinstance(name, str) or not name.strip():
        raise PackageError(f"{package.name}: 清单 name 必须是非空字符串")

    summary = manifest.get("summary") or ""
    author = manifest.get("author") or ""
    for label, value in (("summary", summary), ("author", author)):
        if not isinstance(value, str):
            raise PackageError(f"{package.name}: 清单 {label} 必须是字符串")

    category = manifest.get("category") or "provider_extension"
    if not isinstance(category, str):
        raise PackageError(f"{package.name}: 清单 category 必须是字符串")

    provider = manifest.get("provider") or {}
    provider_enabled = True
    if isinstance(provider, dict) and "enabled" in provider:
        provider_enabled = bool(provider.get("enabled"))

    compatibility: str | None = None
    declared = manifest.get("compatibility")
    if isinstance(declared, dict):
        value = declared.get("gateway")
        if isinstance(value, str) and value:
            compatibility = value

    return {
        "id": identity,
        "version": version,
        "name": name.strip(),
        "summary": summary,
        "author": author,
        "category": category,
        "provider_enabled": provider_enabled,
        "compatibility": compatibility,
    }
