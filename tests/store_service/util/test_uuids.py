import uuid

import pytest

from store_service.util import uuids

ID = uuid.UUID("0b9f2c4e-a1b2-4c3d-8e9f-001122334455")


@pytest.mark.parametrize(
    "value",
    [
        "0b9f2c4e-a1b2-4c3d-8e9f-001122334455",
        "0B9F2C4E-A1B2-4C3D-8E9F-001122334455",
        "{0b9f2c4e-a1b2-4c3d-8e9f-001122334455}",
        "(0b9f2c4e-a1b2-4c3d-8e9f-001122334455)",
        "urn:uuid:0b9f2c4e-a1b2-4c3d-8e9f-001122334455",
        "URN:UUID:0b9f2c4e-a1b2-4c3d-8e9f-001122334455",
        "0b9f2c4ea1b24c3d8e9f001122334455",
    ],
)
def test_accepted_like_go_uuid_parse(value: str) -> None:
    assert uuids.parse(value) == ID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1-1-1-1-1",
        "not-a-uuid",
        "0b9f2c4e-a1b2-4c3d-8e9f-00112233445",
        "0b9f2c4e_a1b2_4c3d_8e9f_001122334455",
        "0b9f2c4e-a1b2-4c3d-8e9f-00112233445g",
        "urn:uuix:0b9f2c4e-a1b2-4c3d-8e9f-001122334455",
        "0b9f2c4e-a1b2-4c3d-8e9f-٠٠1122334455",
    ],
)
def test_rejected(value: str) -> None:
    assert uuids.parse(value) is None
