import io
import pathlib

import pytest

from common.upload import Uploader, UploadError, extension


class File:
    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self.size = len(data)
        self._data = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._data.read(size)


@pytest.mark.parametrize(
    ("filename", "ext"),
    [("photo.png", ".png"), ("a.b.JPG", ".JPG"), ("noext", ""), ("dir.d/noext", ""), ("../../x.jpg", ".jpg"), ("", "")],
)
def test_extension_like_go_filepath_ext(filename: str, ext: str) -> None:
    assert extension(filename) == ext


async def test_upload_stores_the_file_under_a_random_name(tmp_path: pathlib.Path) -> None:
    path = await Uploader(str(tmp_path), 1024).upload(File("Photo.PNG", b"png-bytes"), "products")

    assert path.startswith("products/")
    assert path.endswith(".png")
    assert (tmp_path / path).read_bytes() == b"png-bytes"


@pytest.mark.parametrize(
    ("file", "message"),
    [
        (File("big.png", b"a" * 1025), "file size exceeds maximum allowed size of 1024 bytes"),
        (File("anim.gif", b"GIF89a"), "file extension .gif is not allowed"),
        (File("noext", b"x"), "file extension  is not allowed"),
    ],
    ids=["too large", "extension not allowed", "no extension"],
)
async def test_upload_rejects(tmp_path: pathlib.Path, file: File, message: str) -> None:
    with pytest.raises(UploadError) as error:
        await Uploader(str(tmp_path), 1024).upload(file, "products")
    assert str(error.value) == message


def test_delete_ignores_a_missing_file(tmp_path: pathlib.Path) -> None:
    Uploader(str(tmp_path), 1024).delete("products/missing.png")
