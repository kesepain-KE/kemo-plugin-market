"""静态索引服务。

严格对齐 kemo-adapter-api 获取插件源的实现（gateway/plugins/manager.py
的 ``fetch`` 与 ``catalog``，配合 gateway/transport.py）：

1. 网关的 ``Transport`` 固定 ``follow_redirects=False``，任何 3xx 都会被
   判为源不可用，因此本服务永不返回重定向，也不做补斜杠；
2. 只做精确文件命中，目录不列表；
3. 只放行索引文件与 ``packages/*.zip``，其余一律 404；
4. 响应带 ``no-store``，索引改了立刻生效，不需要等缓存过期。

这些不是"额外加固"，而是让网关那边的拉取路径能走通的必要条件。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

DEFAULT_INDEX_NAME = "index.json"
DEFAULT_PACKAGE_DIR = "packages"
CHUNK_BYTES = 64 * 1024

INDEX_CONTENT_TYPE = "application/json; charset=utf-8"
PACKAGE_CONTENT_TYPE = "application/zip"
ERROR_CONTENT_TYPE = "application/json; charset=utf-8"


class RouteError(ValueError):
    """请求路径不合法。"""


def resolve_path(
    root: Path,
    raw_path: str,
    *,
    index_name: str = DEFAULT_INDEX_NAME,
    package_dir: str = DEFAULT_PACKAGE_DIR,
) -> Path | None:
    """把请求路径映射到 root 下的真实文件；不合法或不存在返回 None。

    这是整个服务的访问控制边界，任何放行判断都必须经过这里。
    """
    path = unquote(urlsplit(raw_path).path)
    if not path.startswith("/"):
        return None

    relative = path[1:]
    if not relative or relative.endswith("/"):
        # 目录请求：不列表、不补斜杠
        return None

    parts = relative.split("/")
    if any(
        not part or part in {".", ".."} or "\\" in part or "\x00" in part
        for part in parts
    ):
        return None

    if relative != index_name:
        # 除索引文件外，只允许 <package_dir>/<name>.zip 这一层
        if not (
            len(parts) == 2
            and parts[0] == package_dir
            and parts[1].lower().endswith(".zip")
        ):
            return None

    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        # 符号链接指向 root 之外
        return None
    if not candidate.is_file():
        return None
    return candidate


def _content_type(path: Path, *, index_name: str) -> str:
    return INDEX_CONTENT_TYPE if path.name == index_name else PACKAGE_CONTENT_TYPE


class MarketHandler(BaseHTTPRequestHandler):
    """只读文件服务。禁用一切写方法，避免出现隐式上传口。"""

    server_version = "kemo-plugin-market"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # 由 serve() 注入
    root: Path = Path(".")
    index_name: str = DEFAULT_INDEX_NAME
    package_dir: str = DEFAULT_PACKAGE_DIR
    quiet: bool = False

    def _send_plain(self, status: int, body: bytes, *, head: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ERROR_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _method_not_allowed(self) -> None:
        self._send_plain(405, b'{"error":"method not allowed"}', head=False)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def _serve(self, *, head: bool) -> None:
        target = resolve_path(
            self.root,
            self.path,
            index_name=self.index_name,
            package_dir=self.package_dir,
        )
        if target is None:
            self._send_plain(404, b'{"error":"not found"}', head=head)
            return

        try:
            size = target.stat().st_size
        except OSError:
            self._send_plain(404, b'{"error":"not found"}', head=head)
            return

        self.send_response(200)
        self.send_header("Content-Type", _content_type(target, index_name=self.index_name))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if head:
            return

        try:
            with target.open("rb") as handle:
                while chunk := handle.read(CHUNK_BYTES):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开属于正常现象，不污染日志
            pass

    def log_message(self, fmt: str, *args) -> None:
        if self.quiet:
            return
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def describe_index(path: Path) -> str:
    """启动时对索引做一次非致命自检，方便直接在日志里看到问题。"""
    if not path.is_file():
        return f"提示：{path} 尚不存在，服务照常启动"
    try:
        import yaml  # noqa: F401  # 仅用于确认依赖可用
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        return f"警告：索引无法解析（{exc}），服务照常启动"
    plugins = value.get("plugins") if isinstance(value, dict) else None
    if not isinstance(plugins, list):
        return "警告：索引缺少 plugins 数组，服务照常启动"
    return f"索引就绪：schema_version={value.get('schema_version')} 条目数={len(plugins)}"


def serve(
    root: Path,
    *,
    bind: str = "127.0.0.1",
    port: int = 8099,
    index_name: str = DEFAULT_INDEX_NAME,
    package_dir: str = DEFAULT_PACKAGE_DIR,
    quiet: bool = False,
    ready: threading.Event | None = None,
    server_factory: type[ThreadingHTTPServer] = ThreadingHTTPServer,
) -> None:
    """前台运行服务，直到收到 KeyboardInterrupt。"""
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"内容根不存在：{root}")

    handler = type(
        "BoundMarketHandler",
        (MarketHandler,),
        {
            "root": root,
            "index_name": index_name,
            "package_dir": package_dir,
            "quiet": quiet,
        },
    )

    httpd = server_factory((bind, port), handler)
    httpd.daemon_threads = True
    try:
        print(describe_index(root / index_name), flush=True)
        print(f"kemo-plugin-market 监听 {bind}:{port} 内容根={root}", flush=True)
        if ready is not None:
            ready.set()
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def iter_allowed_paths(
    root: Path,
    *,
    index_name: str = DEFAULT_INDEX_NAME,
    package_dir: str = DEFAULT_PACKAGE_DIR,
) -> Iterable[str]:
    """列出当前实际上会被放行的 URL 路径，供自检与测试复用。"""
    if (root / index_name).is_file():
        yield "/" + index_name
    directory = root / package_dir
    if directory.is_dir():
        for item in sorted(directory.iterdir()):
            if item.is_file() and item.suffix.lower() == ".zip":
                yield f"/{package_dir}/{item.name}"
