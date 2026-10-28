import asyncio
from pathlib import Path

from grok_bridge.services.attachments import (
    AttachmentRef,
    download_attachments,
    extract_attachments,
    forward_source,
    safe_file_name,
    special_content_text,
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


def test_extract_photo_picks_largest_and_document() -> None:
    message = make_message(
        photo=[
            {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
            {"file_id": "big", "file_unique_id": "b", "width": 1280, "height": 1280, "file_size": 5000},
        ],
        caption="смотри",
    )
    (ref,) = extract_attachments(message)
    assert (ref.type, ref.file_id, ref.caption, ref.file_size) == ("photo", "big", "смотри", 5000)

    doc = make_message(document={"file_id": "d", "file_unique_id": "d", "file_name": "a.pdf",
                                 "mime_type": "application/pdf"})
    (ref,) = extract_attachments(doc)
    assert (ref.type, ref.file_name, ref.mime_type) == ("document", "a.pdf", "application/pdf")


def test_animation_is_not_duplicated_as_document() -> None:
    media = {"file_id": "g", "file_unique_id": "g", "width": 1, "height": 1, "duration": 1}
    message = make_message(animation=media, document={"file_id": "g", "file_unique_id": "g"})
    assert [ref.type for ref in extract_attachments(message)] == ["animation"]


def test_forward_source_and_special_content() -> None:
    forwarded = make_message(
        text="текст",
        forward_origin={"type": "user", "date": 0,
                        "sender_user": {"id": 5, "is_bot": False, "first_name": "Иван", "username": "ivan"}},
    )
    assert forward_source(forwarded) == "Иван (@ivan)"
    hidden = make_message(text="x", forward_origin={"type": "hidden_user", "date": 0, "sender_user_name": "Аноним"})
    assert forward_source(hidden) == "Аноним"
    channel = make_message(text="x", forward_origin={"type": "channel", "date": 0, "message_id": 1,
                                                     "chat": {"id": -1, "type": "channel", "title": "Новости"}})
    assert forward_source(channel) == "Новости"
    assert forward_source(make_message(text="x")) is None

    location = make_message(location={"latitude": 44.8, "longitude": 20.4})
    assert special_content_text(location) == "[Геолокация: 44.8, 20.4]"
    contact = make_message(contact={"phone_number": "+381", "first_name": "Ана"})
    assert special_content_text(contact) == "[Контакт: Ана, +381]"
