"""Lưu trạng thái từng bài báo.

Nội dung nằm trong SQLite (xem `db.py`); ảnh hình/bảng và file PDF gốc vẫn để
nguyên trên đĩa vì chúng là dữ liệu nhị phân lớn, nhét vào cơ sở dữ liệu chỉ làm
file phình ra mà chẳng được gì.

Module này giữ nguyên giao diện hàm cũ (`save`/`load`/`list_docs`…) để phần còn
lại của chương trình không phải đổi theo.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from . import db

DATA_DIR = db.DATA_DIR


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _check(doc_id: str) -> str:
    if not doc_id.isalnum():
        raise ValueError("doc id không hợp lệ")
    return doc_id


# ------------------------------------------------------------------ tài liệu

save = db.save_doc
load = db.load_doc
exists = db.doc_exists
list_docs = db.list_docs


def update(doc_id: str, **fields) -> dict:
    doc = db.load_doc(doc_id)
    doc.update(fields)
    db.save_doc(doc)
    return doc


def delete(doc_id: str) -> None:
    db.delete_doc(_check(doc_id))
    pdf = pdf_path(doc_id)
    if pdf is not None:
        pdf.unlink()
    d = DATA_DIR / f"{doc_id}-img"
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


# ------------------------------------------------------- PDF gốc và ảnh cắt


def save_pdf(doc_id: str, data: bytes) -> None:
    """Giữ file gốc để về sau còn cắt lại hình theo khung người dùng kéo."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{_check(doc_id)}.pdf").write_bytes(data)


def pdf_path(doc_id: str) -> Path | None:
    if not doc_id.isalnum():
        return None
    p = DATA_DIR / f"{doc_id}.pdf"
    return p if p.exists() else None


def img_dir(doc_id: str) -> Path:
    d = DATA_DIR / f"{_check(doc_id)}-img"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_images(doc_id: str, images: dict[str, bytes]) -> None:
    if not images:
        return
    d = img_dir(doc_id)
    for block_id, png in images.items():
        if block_id.isalnum():
            (d / f"{block_id}.png").write_bytes(png)


def copy_images(src_doc: str, dst_doc: str, skip: set[str] | None = None) -> None:
    """Dùng lại ảnh của bài đã nạp trước — cùng file PDF thì ảnh cũng y hệt.

    `skip`: mã khối đã có ảnh cắt lại rồi, đừng ghi đè lên.
    """
    src = DATA_DIR / f"{_check(src_doc)}-img"
    if not src.is_dir():
        return
    skip = skip or set()
    dst = img_dir(dst_doc)
    for f in src.iterdir():
        if f.suffix == ".png" and f.stem not in skip:
            (dst / f.name).write_bytes(f.read_bytes())


def image_path(doc_id: str, block_id: str) -> Path | None:
    if not block_id.isalnum() or not doc_id.isalnum():
        return None
    p = DATA_DIR / f"{doc_id}-img" / f"{block_id}.png"
    return p if p.exists() else None


def delete_image(doc_id: str, block_id: str) -> None:
    p = image_path(doc_id, block_id)
    if p is not None:
        p.unlink(missing_ok=True)


# ------------------------------------------------- chuyển dữ liệu JSON cũ


def migrate_json() -> int:
    """Nạp các file `<id>.json` của bản cũ vào cơ sở dữ liệu rồi đổi tên đánh dấu.

    Chạy một lần lúc khởi động. Không xoá file cũ — chỉ thêm hậu tố `.migrated`
    để nếu có gì sai còn quay lại được.
    """
    if not DATA_DIR.is_dir():
        return 0
    n = 0
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if not doc.get("id") or not doc.get("blocks"):
                continue
            if db.doc_exists(doc["id"]):
                p.rename(p.with_suffix(".json.migrated"))
                continue
            doc.setdefault("plain", {})
            doc.setdefault("notes", {})
            doc.setdefault("translations", {})
            db.save_doc(doc)
            p.rename(p.with_suffix(".json.migrated"))
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[migrate] bỏ qua {p.name}: {e}")
    return n
