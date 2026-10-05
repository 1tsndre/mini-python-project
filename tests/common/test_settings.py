import pathlib

import pytest

from common.settings import Settings, to_int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42", 42),
        ("-7", -7),
        ("0x10", 16),
        ("010", 8),
        ("0o17", 15),
        ("0b101", 5),
        ("1_000", 1000),
        ("12.0", 12),
        ("12.5", 0),
        ("08", 0),
        ("many", 0),
        ("", 0),
        (" 1", 0),
        ("9223372036854775808", 0),
    ],
)
def test_to_int_reads_numbers_like_viper(value: str, expected: int) -> None:
    assert to_int(value) == expected


def test_lookup_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SETTINGS_TEST_A=file\nsettings_test_b=lower\nSETTINGS_TEST_C=\n")
    monkeypatch.setenv("SETTINGS_TEST_A", "env")
    monkeypatch.setenv("SETTINGS_TEST_C", "")
    monkeypatch.delenv("SETTINGS_TEST_B", raising=False)
    monkeypatch.delenv("SETTINGS_TEST_D", raising=False)

    s = Settings(str(env_file))

    assert s.string("SETTINGS_TEST_A", "default") == "env"
    assert s.string("SETTINGS_TEST_B", "default") == "lower"
    assert s.string("SETTINGS_TEST_C", "default") == ""
    assert s.string("SETTINGS_TEST_D", "default") == "default"
    assert s.integer("SETTINGS_TEST_C", 5) == 0
    assert s.integer("SETTINGS_TEST_D", 5) == 5
