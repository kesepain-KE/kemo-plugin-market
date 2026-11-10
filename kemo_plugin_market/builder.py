"""扫描 packages/ 下的插件 ZIP，生成网关可直接消费的索引。

单一职责：把"磁盘上有什么包"变成"一份合法的 index.json"，
输出是确定性的（同样输入永远得到同样字节），便于在版本控制里 diff。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import Version

from . import config as config_module
from .manifest import PackageError, manifest_fields, read_manifest, sha256_of
from .schema import (
    DEFAULT_CATEGORY,
    DEFAULT_COMPATIBILITY,
    MAX_INDEX_BYTES,
    CatalogEntry,
    EntryError,
    build_document,
)


class BuildError(ValueError):
    """索引生成失败。"""


@dataclass
class BuildResult:
    entries: list[CatalogEntry]
    payload: str
    packages: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.payload.encode("utf-8"))

    def document(self) -> dict:
        return json.loads(self.payload)


def _entry_for(
    package: Path,
    *,
    base_url: str,
    overrides: dict[str, config_module.Override],
) -> CatalogEntry:
    manifest = read_manifest(package)
    fields = manifest_fields(manifest, package=package)
    override = overrides.get(fields["id"], config_module.Override())

    category = override.category or fields["category"] or DEFAULT_CATEGORY

    capabilities = override.capabilities
    if capabilities is None:
        # 清单没有声明能力标签；有 provider 的插件默认标 llm，
        # 没有的留空，避免在市场页显示不实信息。
        capabilities = ["llm"] if fields["provider_enabled"] else []

    summary = override.summary if override.summary is not None else fields["summary"]
    author = override.author if override.author is not None else fields["author"]
    compatibility = override.compatibility or fields["compatibility"] or DEFAULT_COMPATIBILITY

    url = f"{base_url}/packages/{package.name}"

    try:
        return CatalogEntry(
            id=fields["id"],
            name=fields["name"],
            version=fields["version"],
            url=url,
            sha256=sha256_of(package),
            summary=summary,
            author=author,
            category=category,
            capabilities=capabilities,
            compatibility=compatibility,
        ).validate()
    except EntryError as exc:
        raise PackageError(f"{package.name}: {exc}") from None


def _render(document: dict) -> str:
    # 保留非 ASCII 以便直接读懂；结尾换行便于 diff 与文本工具处理。
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def build(root: Path, *, config: config_module.Config | None = None) -> BuildResult:
    """生成索引但不写盘。"""
    root = root.resolve()
    cfg = config if config is not None else config_module.load(root)

    packages = sorted(
        (p for p in cfg.packages_dir.iterdir() if p.is_file() and p.suffix.lower() == ".zip"),
        key=lambda p: p.name,
    )

    entries: list[CatalogEntry] = []
    warnings: list[str] = []
    seen: dict[tuple[str, str], Path] = {}

    for package in packages:
        entry = _entry_for(package, base_url=cfg.base_url, overrides=cfg.overrides)
        key = entry.key()
        if key in seen:
            raise BuildError(
                f"重复条目 {entry.id} v{entry.version}："
                f"{seen[key].name} 与 {package.name} 同 id 同版本，"
                "网关会视为冲突，请只保留一份"
            )
        seen[key] = package
        entries.append(entry)

    # 稳定排序：id 升序，同 id 内版本升序（低版本在前，便于人工核对历史）
    entries.sort(key=lambda e: (e.id, Version(e.version)))

    unused = sorted(set(cfg.overrides) - {e.id for e in entries})
    for identity in unused:
        warnings.append(f"market.yaml 的 overrides.{identity} 没有对应包，已忽略")

    try:
        document = build_document(entries)
    except EntryError as exc:
        raise BuildError(str(exc)) from None

    payload = _render(document)
    size = len(payload.encode("utf-8"))
    if size > MAX_INDEX_BYTES:
        raise BuildError(
            f"索引 {size} 字节超过网关 2 MiB 上限，请减少条目或缩短 summary"
        )

    return BuildResult(entries=entries, payload=payload, packages=packages, warnings=warnings)


def write(root: Path, result: BuildResult, *, name: str = "index.json") -> Path:
    """原子写入索引：先写同目录临时文件再替换，避免网关读到半个 JSON。"""
    root = root.resolve()
    target = root / name
    temporary = target.with_name(f".{name}.tmp")
    temporary.write_text(result.payload, encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target
