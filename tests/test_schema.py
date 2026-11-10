"""索引条目与文档的校验规则。

重点覆盖那些"网关会因此整源失效"的情形：
未知字段、大写摘要、非法版本、重复条目。
"""

from __future__ import annotations

import pytest

from kemo_plugin_market.schema import (
    CatalogEntry,
    EntryError,
    build_document,
    validate_document,
)

GOOD = {
    "id": "demo_plugin",
    "name": "演示插件",
    "version": "1.0.0",
    "url": "https://plugins.example.com/packages/demo-1.0.0.zip",
    "sha256": "a" * 64,
    "summary": "说明",
    "author": "作者",
    "category": "provider_extension",
    "capabilities": ["llm"],
    "compatibility": ">=2.0.0,<3.0.0",
}


def test_valid_entry_roundtrips():
    entry = CatalogEntry.from_dict(GOOD)
    assert entry.to_dict() == GOOD
    assert entry.key() == ("demo_plugin", "1.0.0")


def test_optional_fields_have_defaults():
    minimal = {
        "id": "minimal",
        "name": "最小",
        "version": "2.3.4",
        "url": "https://x/y.zip",
        "sha256": "b" * 64,
    }
    entry = CatalogEntry.from_dict(minimal)
    assert entry.summary == ""
    assert entry.author == ""
    assert entry.category == "provider_extension"
    assert entry.capabilities == []
    assert entry.compatibility == ">=2.0.0,<3.0.0"


def test_unknown_field_rejected():
    """网关的 CatalogEntry 是 extra=forbid，多一个字段整源就废掉。"""
    with pytest.raises(EntryError, match="网关不接受的字段"):
        CatalogEntry.from_dict({**GOOD, "icon": "x.png"})


def test_uppercase_sha_rejected():
    with pytest.raises(EntryError, match="小写"):
        CatalogEntry.from_dict({**GOOD, "sha256": "A" * 64})


def test_short_sha_rejected():
    with pytest.raises(EntryError, match="小写"):
        CatalogEntry.from_dict({**GOOD, "sha256": "abc"})


@pytest.mark.parametrize("bad", ["1.0-beta-x", "not-a-version", "1..2"])
def test_invalid_version_rejected(bad):
    with pytest.raises(EntryError, match="PEP440"):
        CatalogEntry.from_dict({**GOOD, "version": bad})


def test_pep440_variants_accepted():
    for version in ("1.0.0", "1.0.0rc1", "2.0.0.post1", "1.0.0.dev2"):
        CatalogEntry.from_dict({**GOOD, "version": version})


@pytest.mark.parametrize("bad_id", ["Demo", "_demo", "demo plugin", "demo.plugin", ""])
def test_invalid_id_rejected(bad_id):
    with pytest.raises(EntryError, match="id"):
        CatalogEntry.from_dict({**GOOD, "id": bad_id})


def test_invalid_category_rejected():
    with pytest.raises(EntryError, match="category"):
        CatalogEntry.from_dict({**GOOD, "category": "app"})


def test_missing_required_field_reported():
    for field in ("id", "name", "version", "url", "sha256"):
        payload = dict(GOOD)
        payload.pop(field)
        with pytest.raises(EntryError, match="缺少必填字段"):
            CatalogEntry.from_dict(payload)


def test_name_length_limit():
    with pytest.raises(EntryError, match="name"):
        CatalogEntry.from_dict({**GOOD, "name": "x" * 101})


def test_capabilities_shape():
    with pytest.raises(EntryError, match="capabilities"):
        CatalogEntry.from_dict({**GOOD, "capabilities": "llm"})
    with pytest.raises(EntryError, match="capabilities"):
        CatalogEntry.from_dict({**GOOD, "capabilities": ["x" * 101]})
    with pytest.raises(EntryError, match="capabilities"):
        CatalogEntry.from_dict({**GOOD, "capabilities": [f"c{i}" for i in range(33)]})


def test_document_roundtrip():
    entry = CatalogEntry.from_dict(GOOD)
    document = build_document([entry])
    assert document["schema_version"] == 1
    assert validate_document(document) == [entry]


def test_document_rejects_wrong_schema_version():
    entry = CatalogEntry.from_dict(GOOD)
    document = build_document([entry])
    document["schema_version"] = 2
    with pytest.raises(EntryError, match="schema_version"):
        validate_document(document)


def test_document_rejects_duplicate_entries():
    entry = CatalogEntry.from_dict(GOOD)
    document = build_document([entry, entry])
    with pytest.raises(EntryError, match="重复条目"):
        validate_document(document)


def test_document_allows_same_id_different_versions():
    first = CatalogEntry.from_dict(GOOD)
    second = CatalogEntry.from_dict({**GOOD, "version": "1.1.0"})
    entries = validate_document(build_document([first, second]))
    assert [e.version for e in entries] == ["1.0.0", "1.1.0"]
