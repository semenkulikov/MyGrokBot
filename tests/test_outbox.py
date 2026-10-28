import os
import time
from pathlib import Path

from grok_bridge.services.outbox_watcher import archive, scan_outbox, split_text


def _touch(path: Path, content: str = "", age: float = 10.0) -> Path:
    path.write_text(content, encoding="utf-8")
    ts = time.time() - age
    os.utime(path, (ts, ts))
    return path


def test_scan_pairs_text_with_photo(tmp_path: Path) -> None:
    _touch(tmp_path / "task-1.png")
    _touch(tmp_path / "task-1.txt", "  ответ \n")
    _touch(tmp_path / "task-2.txt", "второй", age=5)

    items = scan_outbox(tmp_path)

    assert [i.task_id for i in items] == ["task-1", "task-2"]  # от старых к новым
    assert items[0].photo_path == tmp_path / "task-1.png"
    assert items[0].read_text() == "ответ"
    assert items[1].photo_path is None


def test_scan_ignores_photo_without_text_and_bad_names(tmp_path: Path) -> None:
    _touch(tmp_path / "only-photo.png")
    _touch(tmp_path / "bad name!.txt", "x")
    _touch(tmp_path / ".hidden.txt", "x")

    assert scan_outbox(tmp_path) == []


def test_scan_skips_fresh_files(tmp_path: Path) -> None:
    _touch(tmp_path / "fresh.txt", "x", age=0)
    assert scan_outbox(tmp_path, min_age_seconds=1.0) == []
    assert len(scan_outbox(tmp_path, min_age_seconds=0)) == 1


def test_scan_missing_dir(tmp_path: Path) -> None:
    assert scan_outbox(tmp_path / "nope") == []


def test_archive_moves_files(tmp_path: Path) -> None:
    outbox, sent = tmp_path / "outbox", tmp_path / "sent"
    outbox.mkdir()
    _touch(outbox / "t.png")
    _touch(outbox / "t.txt", "ok")
    (item,) = scan_outbox(outbox)

    archive(item, sent)

    assert not any(outbox.iterdir())
    assert sorted(p.name for p in sent.iterdir()) == ["t.png", "t.txt"]


def test_split_text() -> None:
    assert split_text("short", limit=10) == ["short"]
    assert split_text("") == []
    chunks = split_text("aaaa\nbbbb\ncccc", limit=10)
    assert chunks == ["aaaa\nbbbb", "cccc"]
    assert all(len(c) <= 10 for c in split_text("x" * 25, limit=10))
