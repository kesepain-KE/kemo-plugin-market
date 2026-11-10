"""静态服务行为。

这些断言对应网关侧的硬要求：任何 3xx 都会被判源失效，
所以"响应状态码原样透传、不补斜杠、不列目录"是可验证的合同，不是风格偏好。
"""

from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from kemo_plugin_market.server import (
    DEFAULT_INDEX_NAME,
    describe_index,
    iter_allowed_paths,
    resolve_path,
    serve,
)

from conftest import write_package


# ---------------------------------------------------------------- resolve_path


def test_resolve_index(market_root: Path):
    (market_root / "index.json").write_text("{}", encoding="utf-8")
    assert resolve_path(market_root, "/index.json") == (market_root / "index.json").resolve()


def test_resolve_package(market_root: Path):
    path = write_package(market_root / "packages")
    resolved = resolve_path(market_root, f"/packages/{path.name}")
    assert resolved == path.resolve()


@pytest.mark.parametrize(
    "raw",
    [
        "/",                    # 根目录不列表
        "/packages/",           # 目录不补斜杠
        "/index.json/",         # 结尾斜杠
        "/nope",                # 不存在
        "/packages/missing.zip",
        "/../etc/passwd",       # 路径穿越
        "/packages/../../x.zip",
        "/etc/passwd",          # 只放行两类路径
        "/market.yaml",         # 配置文件不可下载
        "/README.md",
        "/packages/sub/dir.zip",  # 只允许一层
        "/packages/notes.txt",    # 只允许 .zip
    ],
)
def test_resolve_rejects(market_root: Path, raw: str):
    (market_root / "index.json").write_text("{}", encoding="utf-8")
    write_package(market_root / "packages")
    assert resolve_path(market_root, raw) is None


def test_resolve_blocks_symlink_escape(market_root: Path, tmp_path: Path):
    outside = tmp_path / "secret.zip"
    outside.write_bytes(b"PK\x03\x04")
    link = market_root / "packages" / "evil.zip"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持符号链接")

    assert resolve_path(market_root, "/packages/evil.zip") is None


def test_resolve_ignores_query_string(market_root: Path):
    """网关自己不会带 query，但带 query 也一样要能命中。"""
    (market_root / "index.json").write_text("{}", encoding="utf-8")
    assert resolve_path(market_root, "/index.json?v=2") is not None


# ---------------------------------------------------------------- HTTP 行为


@pytest.fixture
def running_server(market_root: Path):
    (market_root / "index.json").write_text('{"schema_version": 1, "plugins": []}\n', encoding="utf-8")
    package = write_package(market_root / "packages")

    ready = threading.Event()
    thread = threading.Thread(
        target=serve,
        args=(market_root,),
        kwargs={"bind": "127.0.0.1", "port": 0, "quiet": True, "ready": ready},
        daemon=True,
    )
    # port=0 时由内核分配，需要拿到实际端口，这里改用显式工厂
    thread = threading.Thread(
        target=_serve_on_free_port,
        args=(market_root, ready),
        daemon=True,
    )
    thread.start()
    assert ready.wait(5), "服务未在 5 秒内就绪"

    server = _SERVERS.pop()
    host, port = server.server_address[:2]
    try:
        yield host, port, package
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_SERVERS: list[ThreadingHTTPServer] = []


def _serve_on_free_port(root: Path, ready: threading.Event) -> None:
    original_factory = ThreadingHTTPServer

    def factory(address, handler):
        server = original_factory(address, handler)
        _SERVERS.append(server)
        return server

    serve(root, bind="127.0.0.1", port=0, quiet=True, ready=ready, server_factory=factory)


def _request(host: str, port: int, path: str, method: str = "GET"):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_http_index_ok(running_server):
    host, port, _ = running_server
    status, headers, body = _request(host, port, "/index.json")
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert b"schema_version" in body


def test_http_package_ok(running_server):
    host, port, package = running_server
    status, headers, body = _request(host, port, f"/packages/{package.name}")
    assert status == 200
    assert headers["Content-Type"] == "application/zip"
    assert body == package.read_bytes()


def test_http_never_redirects(running_server):
    """网关 follow_redirects=False，3xx 会被判定源失效。"""
    host, port, _ = running_server
    for path in ("/", "/packages/", "/index.json", "/nope", "/packages/missing.zip"):
        status, headers, _ = _request(host, port, path)
        assert not (300 <= status < 400), f"{path} 返回了 {status}，网关会判源失效"
        assert "Location" not in headers


def test_http_directory_not_listed(running_server):
    host, port, _ = running_server
    for path in ("/", "/packages/"):
        status, _, body = _request(host, port, path)
        assert status == 404
        assert b"not found" in body


def test_http_unknown_path_404(running_server):
    host, port, _ = running_server
    status, headers, _ = _request(host, port, "/etc/passwd")
    assert status == 404
    assert headers["Content-Type"] == "application/json; charset=utf-8"


def test_http_head_matches_get(running_server):
    host, port, _ = running_server
    get_status, get_headers, get_body = _request(host, port, "/index.json")
    head_status, head_headers, head_body = _request(host, port, "/index.json", method="HEAD")
    assert head_status == get_status == 200
    assert head_headers["Content-Length"] == get_headers["Content-Length"]
    assert head_body == b""


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_http_write_methods_rejected(running_server, method):
    host, port, _ = running_server
    status, _, _ = _request(host, port, "/index.json", method=method)
    assert status == 405


# ---------------------------------------------------------------- 辅助函数


def test_describe_index_reports_count(market_root: Path):
    (market_root / "index.json").write_text(
        '{"schema_version": 1, "plugins": [{"id": "a"}]}', encoding="utf-8"
    )
    message = describe_index(market_root / "index.json")
    assert "条目数=1" in message


def test_describe_index_tolerates_missing(market_root: Path):
    message = describe_index(market_root / "index.json")
    assert "尚不存在" in message


def test_describe_index_tolerates_broken_json(market_root: Path):
    (market_root / "index.json").write_text("{not json", encoding="utf-8")
    assert "无法解析" in describe_index(market_root / "index.json")


def test_iter_allowed_paths(market_root: Path):
    (market_root / "index.json").write_text("{}", encoding="utf-8")
    write_package(market_root / "packages")
    paths = list(iter_allowed_paths(market_root))
    assert f"/{DEFAULT_INDEX_NAME}" in paths
    assert any(p.startswith("/packages/") for p in paths)
    assert len(paths) == 2


def test_iter_allowed_paths_empty(market_root: Path):
    assert list(iter_allowed_paths(market_root)) == []
