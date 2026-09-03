from slugify import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  Multiple   spaces  ") == "multiple-spaces"
