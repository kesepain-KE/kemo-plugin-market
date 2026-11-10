"""market.yaml 配置解析。

配置只描述"这个市场的发布形态"，不描述任何插件本身的字段；
插件字段一律从 ZIP 内的 plugin.yaml 读取，避免同一份信息存两处而漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

CONFIG_NAME = "market.yaml"
DEFAULT_PACKAGES_DIR = "packages"


class ConfigError(ValueError):
    """market.yaml 不合法。"""


@dataclass(frozen=True)
class Override:
    """某个插件的市场元数据覆盖（可选）。"""

    category: str | None = None
    capabilities: list[str] | None = None
    summary: str | None = None
    author: str | None = None
    compatibility: str | None = None


@dataclass(frozen=True)
class Config:
    base_url: str
    packages_dir: Path
    overrides: dict[str, Override] = field(default_factory=dict)


def _check_base_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("base_url 必须是非空字符串")
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError("base_url 必须是 http 或 https 地址")
    if not parsed.hostname:
        raise ConfigError("base_url 缺少主机名")
    # 网关拒绝带凭据、查询、fragment 的地址；这里提前拦下
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("base_url 不能包含凭据、查询参数或 fragment")
    return url


def _parse_override(identity: str, raw: Any) -> Override:
    if raw is None:
        return Override()
    if not isinstance(raw, dict):
        raise ConfigError(f"overrides.{identity} 必须是映射")
    unknown = sorted(
        set(raw) - {"category", "capabilities", "summary", "author", "compatibility"}
    )
    if unknown:
        raise ConfigError(
            f"overrides.{identity} 包含未知字段：" + ", ".join(unknown)
        )
    capabilities = raw.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, list) or any(
            not isinstance(item, str) for item in capabilities
        ):
            raise ConfigError(
                f"overrides.{identity}.capabilities 必须是字符串数组"
            )
    for key in ("category", "summary", "author", "compatibility"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"overrides.{identity}.{key} 必须是字符串")
    return Override(
        category=raw.get("category"),
        capabilities=list(capabilities) if capabilities is not None else None,
        summary=raw.get("summary"),
        author=raw.get("author"),
        compatibility=raw.get("compatibility"),
    )


def load(root: Path, *, required: bool = True) -> Config:
    """读取 market.yaml。root 为市场内容根（index.json 所在目录）。"""
    path = root / CONFIG_NAME
    if not path.is_file():
        if required:
            raise ConfigError(f"{path} 不存在")
        raise ConfigError(f"{path} 不存在")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{CONFIG_NAME} 无法解析（{exc}）") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME} 顶层必须是映射")

    unknown = sorted(set(raw) - {"base_url", "packages_dir", "overrides"})
    if unknown:
        raise ConfigError(f"{CONFIG_NAME} 包含未知字段：" + ", ".join(unknown))

    base_url = _check_base_url(raw.get("base_url"))

    packages_raw = raw.get("packages_dir") or DEFAULT_PACKAGES_DIR
    if not isinstance(packages_raw, str) or not packages_raw.strip():
        raise ConfigError("packages_dir 必须是非空字符串")
    packages = Path(packages_raw.strip())
    if not packages.is_absolute():
        packages = root / packages
    if not packages.is_dir():
        raise ConfigError(f"packages_dir 指向的目录不存在：{packages}")

    overrides_raw = raw.get("overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise ConfigError("overrides 必须是映射")
    overrides = {
        str(key): _parse_override(str(key), value)
        for key, value in overrides_raw.items()
    }

    return Config(base_url=base_url, packages_dir=packages, overrides=overrides)
