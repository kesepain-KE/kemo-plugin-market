"""索引条目定义与校验。

字段与约束对齐 kemo-adapter-api 的 ``gateway.plugins.schema.CatalogEntry``：
该模型是 ``extra="forbid"``，多一个字段会让整个索引源被判为无效，
所以这里的校验刻意保持同等严格，宁可在本地拒掉，也不让线上市场整体失效。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from packaging.version import InvalidVersion, Version

SCHEMA_VERSION = 1

# 网关侧硬上限（gateway/plugins/manager.py + schema.py）
MAX_ENTRIES = 500
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024

CATEGORIES = ("model_account", "cli_provider", "provider_extension")
DEFAULT_CATEGORY = "provider_extension"
DEFAULT_COMPATIBILITY = ">=2.0.0,<3.0.0"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# 字段长度上限，与 CatalogEntry 的 Field(max_length=...) 一致
LIMITS = {
    "name": 100,
    "version": 60,
    "url": 2000,
    "summary": 1000,
    "author": 200,
    "compatibility": 100,
}
MAX_CAPABILITIES = 32
MAX_CAPABILITY_CHARS = 100


class EntryError(ValueError):
    """单条索引不合法。"""


def validate_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise EntryError(f"id 必须匹配 ^[a-z0-9][a-z0-9_-]{{0,63}}$，实际为 {value!r}")
    return value


def validate_version(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise EntryError(f"version 必须是非空字符串，实际为 {value!r}")
    if len(value) > LIMITS["version"]:
        raise EntryError(f"version 超过 {LIMITS['version']} 字符")
    try:
        Version(value)
    except InvalidVersion:
        raise EntryError(f"version {value!r} 不是合法 PEP440") from None
    return value


def validate_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA_RE.match(value):
        raise EntryError("sha256 必须是 64 位小写十六进制（ZIP 文件字节摘要）")
    return value


def validate_text(value: Any, name: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise EntryError(f"{name} 必须是字符串，实际为 {type(value).__name__}")
    if not value and not allow_empty:
        raise EntryError(f"{name} 不能为空")
    limit = LIMITS[name]
    if len(value) > limit:
        raise EntryError(f"{name} 超过 {limit} 字符（当前 {len(value)}）")
    return value


def validate_capabilities(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise EntryError("capabilities 必须是数组")
    if len(value) > MAX_CAPABILITIES:
        raise EntryError(f"capabilities 最多 {MAX_CAPABILITIES} 项")
    result = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise EntryError("capabilities 每项必须是非空字符串")
        if len(item) > MAX_CAPABILITY_CHARS:
            raise EntryError(f"capabilities 每项最多 {MAX_CAPABILITY_CHARS} 字符")
        result.append(item)
    return result


@dataclass(frozen=True)
class CatalogEntry:
    """索引中的一条插件记录。字段顺序即输出顺序。"""

    id: str
    name: str
    version: str
    url: str
    sha256: str
    summary: str = ""
    author: str = ""
    category: str = DEFAULT_CATEGORY
    capabilities: list[str] = field(default_factory=list)
    compatibility: str = DEFAULT_COMPATIBILITY

    def validate(self) -> "CatalogEntry":
        validate_id(self.id)
        validate_text(self.name, "name", allow_empty=False)
        validate_version(self.version)
        validate_text(self.url, "url", allow_empty=False)
        validate_sha256(self.sha256)
        validate_text(self.summary, "summary")
        validate_text(self.author, "author")
        if self.category not in CATEGORIES:
            raise EntryError(
                f"category 必须是 {', '.join(CATEGORIES)} 之一，实际为 {self.category!r}"
            )
        validate_capabilities(self.capabilities)
        validate_text(self.compatibility, "compatibility", allow_empty=False)
        return self

    def key(self) -> tuple[str, str]:
        return (self.id, self.version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "CatalogEntry":
        if not isinstance(raw, dict):
            raise EntryError("索引条目必须是对象")
        unknown = sorted(set(raw) - {f for f in cls.__dataclass_fields__})
        if unknown:
            # 网关会因未知字段整源失效，这里提前拦下
            raise EntryError("条目包含网关不接受的字段：" + ", ".join(unknown))
        # 不要用 list(...) 兜底：字符串会被拆成单字符列表而静默通过，
        # 必须原样交给 validate_capabilities 做类型判断。
        capabilities = raw.get("capabilities")
        if capabilities is None:
            capabilities = []
        elif not isinstance(capabilities, list):
            raise EntryError("capabilities 必须是数组")
        else:
            capabilities = list(capabilities)
        try:
            entry = cls(
                id=raw["id"],
                name=raw["name"],
                version=raw["version"],
                url=raw["url"],
                sha256=raw["sha256"],
                summary=raw.get("summary", ""),
                author=raw.get("author", ""),
                category=raw.get("category", DEFAULT_CATEGORY),
                capabilities=capabilities,
                compatibility=raw.get("compatibility", DEFAULT_COMPATIBILITY),
            )
        except KeyError as exc:
            raise EntryError(f"条目缺少必填字段：{exc.args[0]}") from None
        return entry.validate()


def build_document(entries: list[CatalogEntry]) -> dict[str, Any]:
    if len(entries) > MAX_ENTRIES:
        raise EntryError(f"条目数 {len(entries)} 超过网关上限 {MAX_ENTRIES}")
    return {"schema_version": SCHEMA_VERSION, "plugins": [e.to_dict() for e in entries]}


def validate_document(value: Any) -> list[CatalogEntry]:
    """校验一份完整索引；返回解析后的条目。"""
    if not isinstance(value, dict):
        raise EntryError("索引顶层必须是对象")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise EntryError(f"schema_version 必须是 {SCHEMA_VERSION}")
    plugins = value.get("plugins")
    if not isinstance(plugins, list):
        raise EntryError("plugins 必须是数组")
    if len(plugins) > MAX_ENTRIES:
        raise EntryError(f"条目数 {len(plugins)} 超过网关上限 {MAX_ENTRIES}")
    entries = [CatalogEntry.from_dict(item) for item in plugins]
    seen: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(entries):
        if entry.key() in seen:
            raise EntryError(
                f"重复条目 {entry.id} v{entry.version}"
                f"（第 {seen[entry.key()] + 1} 条与第 {index + 1} 条）"
            )
        seen[entry.key()] = index
    return entries
