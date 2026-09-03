"""hidden grader — seed 에도 컨텍스트 조립에도 들어가지 않는다 (docs/11)."""

import pytest

from slugify import slugify


def test_r001_basic():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  Multiple   spaces  ") == "multiple-spaces"
    assert slugify("foo_bar.baz") == "foo-bar-baz"
    assert slugify("A--B") == "a-b"
    assert slugify("Version 2.0.1") == "version-2-0-1"
    assert slugify("--already-slug--") == "already-slug"


def test_r002_unicode():
    assert slugify("Crème Brûlée") == "creme-brulee"
    assert slugify("naïve café") == "naive-cafe"
    assert slugify("Señor Müller") == "senor-muller"
    assert slugify("日本語 text") == "text"
    assert slugify("emoji 🎉 party") == "emoji-party"


def test_r003_max_length():
    assert slugify("the quick brown fox", max_length=9) == "the-quick"
    assert slugify("the quick brown fox", max_length=10) == "the-quick"
    assert slugify("the quick brown fox", max_length=8) == "the"
    assert slugify("abcdefghij", max_length=4) == "abcd"
    assert slugify("hello world", max_length=6) == "hello"
    assert slugify("hello world", max_length=5) == "hello"
    assert slugify("Hello World", max_length=11) == "hello-world"
    assert slugify("Hello World", max_length=100) == "hello-world"
    with pytest.raises(ValueError):
        slugify("hello", max_length=0)
    with pytest.raises(ValueError):
        slugify("hello", max_length=-3)


def test_r004_empty():
    assert slugify("") == ""
    assert slugify("   ") == ""
    assert slugify("!!!") == ""
    assert slugify("!!!", max_length=3) == ""
    assert slugify("日本語") == ""
