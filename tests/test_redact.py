from harness.policy import approval_hash
from harness.redact import Redactor


def test_payload_masks_nested_values_and_preserves_command_identity():
    raw = {
        "cmd": ["python", "-c", "print('TOPSECRET-ABC')"],
        "message": "TOPSECRET-ABC",
        "nested": ["TOPSECRET-ABC"],
    }

    safe = Redactor(patterns=(r"TOPSECRET-[A-Z]+",)).payload(raw)

    assert raw["message"] == "TOPSECRET-ABC"
    assert "TOPSECRET-ABC" not in str(safe)
    assert safe["cmd_identity"] == approval_hash(raw["cmd"])


def test_environment_secret_values_are_builtin_defaults(monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_SECRET", "ENV-SECRET-123")
    monkeypatch.setenv("MAX_TOKENS", "1")

    redactor = Redactor()
    assert "ENV-SECRET-123" not in redactor.text("value=ENV-SECRET-123")
    assert redactor.text("count=1") == "count=1"


def test_untrusted_identity_and_credential_key_values_are_masked():
    redactor = Redactor(patterns=(r"TOPSECRET-[A-Z]+",))

    safe = redactor.data(
        {
            "TOPSECRET-KEY": "value",
            "cmd_identity": "TOPSECRET-ABC",
            "password": "hunter2",
            "tokens_in": 1,
        }
    )

    assert "TOPSECRET-ABC" not in str(safe)
    assert "TOPSECRET-KEY" not in str(safe)
    assert safe["password"] != "hunter2"
    assert safe["tokens_in"] == 1
