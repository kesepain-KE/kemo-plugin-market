"""命令行入口。

四个子命令，各自只做一件事：

    build    扫描 packages/ 生成 index.json
    check    只校验，不写盘
    serve    启动静态索引服务
    verify   对一个已发布的源做端到端校验（无需网关在跑）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from . import config as config_module
from .builder import BuildError, build, write
from .config import ConfigError
from .manifest import PackageError
from .schema import MAX_INDEX_BYTES, EntryError, validate_document
from .server import DEFAULT_INDEX_NAME, DEFAULT_PACKAGE_DIR, serve

DEFAULT_ROOT = Path("/srv/kemo-plugin-market")


def _print_result(result, *, verbose: bool = False) -> None:
    print(f"条目数 {len(result.entries)}，索引 {result.size} 字节")
    for entry in result.entries:
        if verbose:
            print(
                f"  {entry.id} v{entry.version}  {entry.sha256}  "
                f"{entry.name}  [{entry.category}]  {entry.url}"
            )
        else:
            print(f"  {entry.id} v{entry.version}  {entry.sha256[:16]}…  {entry.name}")
    for warning in result.warnings:
        print(f"  提示：{warning}")


def _cmd_build(args) -> int:
    root = Path(args.root).resolve()
    try:
        result = build(root)
    except (ConfigError, PackageError, BuildError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    _print_result(result, verbose=args.verbose)

    if args.check:
        print("校验通过（未写入文件）")
        return 0
    if args.dry_run:
        print("--- 索引内容 ---")
        sys.stdout.write(result.payload)
        return 0

    target = write(root, result, name=args.name)
    print(f"已写入 {target}")
    return 0


def _cmd_serve(args) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"失败：内容根不存在 {root}", file=sys.stderr)
        return 1
    try:
        serve(
            root,
            bind=args.bind,
            port=args.port,
            index_name=args.name,
            package_dir=DEFAULT_PACKAGE_DIR,
            quiet=args.quiet,
        )
    except OSError as exc:
        print(f"失败：无法监听 {args.bind}:{args.port}（{exc}）", file=sys.stderr)
        return 1
    return 0


def _fetch(url: str, *, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kemo-plugin-market-verify"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        content = bytearray()
        while chunk := response.read(64 * 1024):
            content.extend(chunk)
            if len(content) > limit:
                raise RuntimeError(f"响应超过 {limit} 字节")
        return bytes(content)


def _cmd_verify(args) -> int:
    url = args.url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        print("失败：--url 必须是 http(s) 地址", file=sys.stderr)
        return 1
    if parsed.query or parsed.fragment or parsed.username:
        print("失败：--url 不能包含查询参数、fragment 或凭据", file=sys.stderr)
        return 1

    index_url = url if url.endswith(".json") else f"{url}/{DEFAULT_INDEX_NAME}"

    try:
        raw = _fetch(index_url, limit=MAX_INDEX_BYTES)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        print(f"[1] 索引不可达：{exc}", file=sys.stderr)
        return 1
    print(f"[1] 索引可达 {len(raw)} 字节  {index_url}")

    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[2] 索引不是合法 JSON：{exc}", file=sys.stderr)
        return 1

    try:
        entries = validate_document(document)
    except EntryError as exc:
        print(f"[2] 索引校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"[2] 结构合法，条目数 {len(entries)}")

    failures = 0
    for entry in entries:
        try:
            blob = _fetch(entry.url, limit=64 * 1024 * 1024)
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            print(f"[3] {entry.id} 包下载失败：{exc}", file=sys.stderr)
            failures += 1
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if digest != entry.sha256:
            print(
                f"[3] {entry.id} 摘要不一致：索引 {entry.sha256} 实际 {digest}",
                file=sys.stderr,
            )
            failures += 1
            continue
        if blob[:2] != b"PK":
            print(f"[3] {entry.id} 下载内容不是 ZIP", file=sys.stderr)
            failures += 1
            continue
        print(f"[3] {entry.id} v{entry.version} {len(blob)} 字节 摘要一致")

    if failures:
        print(f"\n失败：{failures} 个条目未通过校验", file=sys.stderr)
        return 1
    print(f"\n通过：{len(entries)} 个条目全部可用")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kemo-plugin-market",
        description="Kemo 高级插件市场的索引生成与静态发布服务",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="扫描插件包并生成索引")
    build_parser.add_argument(
        "--root", default=str(DEFAULT_ROOT), help=f"市场内容根（默认 {DEFAULT_ROOT}）"
    )
    build_parser.add_argument("--name", default=DEFAULT_INDEX_NAME, help="索引文件名")
    build_parser.add_argument("--check", action="store_true", help="只校验，不写盘")
    build_parser.add_argument("--dry-run", action="store_true", help="打印索引，不写盘")
    build_parser.add_argument("--verbose", "-v", action="store_true", help="输出完整 URL")
    build_parser.set_defaults(handler=_cmd_build)

    serve_parser = subparsers.add_parser("serve", help="启动静态索引服务")
    serve_parser.add_argument("--root", default=str(DEFAULT_ROOT), help="市场内容根")
    serve_parser.add_argument("--bind", default="127.0.0.1", help="监听地址")
    serve_parser.add_argument("--port", type=int, default=8099, help="监听端口")
    serve_parser.add_argument("--name", default=DEFAULT_INDEX_NAME, help="索引文件名")
    serve_parser.add_argument("--quiet", action="store_true", help="不记录访问日志")
    serve_parser.set_defaults(handler=_cmd_serve)

    verify_parser = subparsers.add_parser("verify", help="校验一个已发布的插件源")
    verify_parser.add_argument("--url", required=True, help="索引地址或站点根地址")
    verify_parser.set_defaults(handler=_cmd_verify)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
