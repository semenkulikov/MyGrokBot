import asyncio
from pathlib import Path

from grok_bridge.services.attachments import (
    AttachmentRef,
    download_attachments,
    extract_attachments,
    safe_file_name,
)
from tests.helpers import FakeDownloader, make_message


def test_extract_voice() -> None:
    message = make_message(voice={"file_id": "v1", "file_unique_id": "u1", "duration": 7, "mime_type": "audio/ogg"})
    (ref,) = extract_attachments(message)
    assert ref.type == "voice"
    assert ref.file_id == "v1"
    assert ref.duration == 7
    assert ref.mime_type == "audio/ogg"


def test_extract_audio_with_caption() -> None:
    message = make_message(
        audio={"file_id": "a1", "file_unique_id": "u1", "duration": 60, "file_name": "song.mp3"},
        caption="послушай",
    )
    (ref,) = extract_attachments(message)
    assert (ref.type, ref.caption, ref.file_name) == ("audio", "послушай", "song.mp3")


def test_text_message_has_no_attachments() -> None:
    assert extract_attachments(make_message(text="привет")) == []


def test_safe_file_name() -> None:
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("Отчёт за май.pdf") == "Отчёт_за_май.pdf"
    assert safe_file_name("...") == "file"


def test_download_saves_files_and_reports_errors(tmp_path: Path) -> None:
    bot = FakeDownloader(paths={"v1": "voice/file_1.oga"}, broken={"bad"})
    refs = [
        AttachmentRef(type="voice", file_id="v1", duration=3),
        AttachmentRef(type="audio", file_id="bad"),
        AttachmentRef(type="audio", file_id="big", file_size=10_000),
    ]

    files = asyncio.run(download_attachments(bot, refs, tmp_path / "task", max_bytes=1_000))

    assert files[0]["path"] == str((tmp_path / "task" / "01-voice.oga").resolve())
    assert Path(files[0]["path"]).read_bytes() == b"data"
    assert files[0]["duration"] == 3 and "error" not in files[0]
    assert files[1]["path"] is None and "OSError" in files[1]["error"]
    assert files[2]["path"] is None and "лимита" in files[2]["error"]


def test_download_keeps_original_name(tmp_path: Path) -> None:
    bot = FakeDownloader(paths={"a": "music/file_9.mp3"})
    (info,) = asyncio.run(
        download_attachments(bot, [AttachmentRef(type="audio", file_id="a", file_name="трек")], tmp_path)
    )
    assert Path(info["path"]).name == "01-трек.mp3"
