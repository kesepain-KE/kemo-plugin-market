"""kemo-plugin-market — Kemo 高级插件市场的索引生成与静态发布服务。"""

from __future__ import annotations

from .builder import BuildError, BuildResult, build, write
from .config import Config, ConfigError, Override, load as load_config
from .manifest import PackageError, read_manifest, sha256_of
from .schema import (
    MAX_ENTRIES,
    MAX_INDEX_BYTES,
    MAX_PACKAGE_BYTES,
    SCHEMA_VERSION,
    CatalogEntry,
    EntryError,
    build_document,
    validate_document,
)

__version__ = "1.0.0"

__all__ = [
    "BuildError",
    "BuildResult",
    "CatalogEntry",
    "Config",
    "ConfigError",
    "EntryError",
    "MAX_ENTRIES",
    "MAX_INDEX_BYTES",
    "MAX_PACKAGE_BYTES",
    "Override",
    "PackageError",
    "SCHEMA_VERSION",
    "build",
    "build_document",
    "load_config",
    "read_manifest",
    "sha256_of",
    "validate_document",
    "write",
    "__version__",
]
