"""Trusted, workspace-scoped attachment publication for Weiwo conversations.

Workspace code may create arbitrary files, but the browser must never be given
an arbitrary local path.  This module is the narrow bridge between those two
trust domains: it reads one file through the existing workspace boundary,
validates a narrow raster-image or document allowlist, copies immutable bytes
into Console's private media store, and returns only an opaque attachment ID
plus safe metadata.
"""
from __future__ import annotations

import hashlib
import io
import csv
import json
import os
import re
import stat
import threading
import uuid
import zipfile
import zlib
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Dict, Tuple

from guanlan_v2.channels.security import TELEGRAM_TOKEN_SCAN_RE


MAX_MEDIA_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 40_000_000

_MEDIA_ID_RE = re.compile(r"^mi_[0-9a-f]{32}$")
_DOCUMENT_ID_RE = re.compile(r"^do_[0-9a-f]{32}$")
_ATTACHMENT_ID_RE = re.compile(r"^(?:mi|do)_[0-9a-f]{32}$")
_SESSION_ID_RE = re.compile(r"^cs_[0-9a-f]{12}$")
_WORKSPACE_ID_RE = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_SOURCE_EXTENSIONS = {
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/webp": frozenset({".webp"}),
}
_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ZIP_DOCUMENT_REQUIREMENTS = {
    ".xlsx": frozenset({"[Content_Types].xml", "xl/workbook.xml"}),
    ".docx": frozenset({"[Content_Types].xml", "word/document.xml"}),
}
_MAX_ZIP_ENTRIES = 20_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_WRITE_LOCK = threading.RLock()
_OOXML_FORMULA_RE = re.compile(rb"<(?:[a-z0-9_]+:)?f(?:\s|>)", re.IGNORECASE)
_OOXML_EXTERNAL_REL_RE = re.compile(
    rb"targetmode\s*=\s*['\"]external['\"]", re.IGNORECASE,
)
_SAFE_SIGNED_NUMBER_RE = re.compile(
    r"^[+-](?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


class MediaError(ValueError):
    """Safe publication error suitable for the local API or Agent."""


class MediaNotFound(MediaError):
    """Opaque id is invalid, missing, or no longer internally consistent."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _inside(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([
            os.path.normcase(str(root)), os.path.normcase(str(candidate)),
        ]) == os.path.normcase(str(root))
    except ValueError:
        return False


def _is_reparse(path: Path) -> bool:
    """Reject both POSIX symlinks and Windows junction/reparse points."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attrs = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
        return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))
    except OSError:
        return True


def _read_regular_file(path: Path, *, max_bytes: int, label: str = "图片") -> bytes:
    """Read a stable regular file without accepting a link swap or oversize."""
    if _is_reparse(path):
        raise MediaError(f"{label}路径不能包含符号链接或联接点")
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise MediaError(f"{label}不存在或不可访问") from exc
    if not stat.S_ISREG(before.st_mode):
        raise MediaError(f"只能发布普通{label}")
    if before.st_size > max_bytes:
        raise MediaError(f"{label}超过 {max_bytes // (1024 * 1024)}MB 上限")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise MediaError(f"{label}不存在或不可访问") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise MediaError(f"只能发布普通{label}")
        if opened.st_size > max_bytes:
            raise MediaError(f"{label}超过 {max_bytes // (1024 * 1024)}MB 上限")
        chunks = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)

    try:
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise MediaError(f"{label}在发布过程中发生变化") from exc
    before_id = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    opened_id = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    after_id = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_id != opened_id or opened_id != after_id or _is_reparse(path):
        raise MediaError(f"{label}在发布过程中发生变化")
    if len(data) > max_bytes:
        raise MediaError(f"{label}超过 {max_bytes // (1024 * 1024)}MB 上限")
    if len(data) != opened.st_size:
        raise MediaError(f"{label}在发布过程中发生变化")
    return data


def _png_dimensions(data: bytes) -> Tuple[int, int]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise MediaError("PNG 签名不正确")
    pos = len(signature)
    width = height = 0
    saw_idat = saw_iend = False
    chunk_index = 0
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if length > MAX_MEDIA_BYTES or end > len(data):
            raise MediaError("PNG 结构不完整")
        payload = data[pos + 8:pos + 8 + length]
        expected_crc = int.from_bytes(data[pos + 8 + length:end], "big")
        if (zlib.crc32(kind + payload) & 0xFFFFFFFF) != expected_crc:
            raise MediaError("PNG 校验和不正确")
        if chunk_index == 0:
            if kind != b"IHDR" or length != 13:
                raise MediaError("PNG 缺少有效 IHDR")
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
        elif kind == b"IHDR":
            raise MediaError("PNG 含重复 IHDR")
        if kind == b"IDAT":
            saw_idat = True
        if kind == b"IEND":
            if length != 0 or end != len(data):
                raise MediaError("PNG IEND 或尾部数据不合法")
            saw_iend = True
            break
        pos = end
        chunk_index += 1
    if not width or not height or not saw_idat or not saw_iend:
        raise MediaError("PNG 结构不完整")
    return width, height


def _jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise MediaError("JPEG 签名或结束标记不正确")
    sof = frozenset({
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    })
    pos = 2
    width = height = 0
    while pos < len(data) - 2:
        if data[pos] != 0xFF:
            raise MediaError("JPEG 标记流不合法")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            raise MediaError("JPEG 结构不完整")
        marker = data[pos]
        pos += 1
        if marker == 0xD9:
            break
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if pos + 2 > len(data):
            raise MediaError("JPEG 结构不完整")
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > len(data):
            raise MediaError("JPEG 段长度不合法")
        if marker in sof:
            if length < 7:
                raise MediaError("JPEG SOF 不完整")
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
        if marker == 0xDA:  # compressed scan; only the terminal EOI is structural here
            break
        pos += length
    if not width or not height:
        raise MediaError("JPEG 缺少有效尺寸")
    return width, height


def _webp_dimensions(data: bytes) -> Tuple[int, int]:
    if (len(data) < 20 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)):
        raise MediaError("WebP RIFF 签名或长度不正确")
    pos = 12
    width = height = 0
    while pos + 8 <= len(data):
        kind = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        start = pos + 8
        end = start + size
        padded_end = end + (size & 1)
        if end > len(data) or padded_end > len(data):
            raise MediaError("WebP 块结构不完整")
        payload = data[start:end]
        if kind == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
        elif kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        elif kind == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        pos = padded_end
    if pos != len(data) or not width or not height:
        raise MediaError("WebP 缺少有效图像尺寸")
    return width, height


def inspect_image(data: bytes) -> Tuple[str, int, int]:
    """Return canonical MIME and dimensions after structural validation."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
        width, height = _png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
        width, height = _jpeg_dimensions(data)
    elif data.startswith(b"RIFF"):
        mime = "image/webp"
        width, height = _webp_dimensions(data)
    else:
        raise MediaError("只支持真实 PNG、JPEG 或 WebP 图片")
    if (width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION
            or width * height > MAX_IMAGE_PIXELS):
        raise MediaError("图片尺寸或像素总量超过安全上限")
    return mime, width, height


def _reject_active_pdf_objects(reader: Any) -> None:
    """Walk reachable PDF dictionaries and reject executable/embedded actions."""
    try:
        from pypdf.generic import IndirectObject
    except Exception as exc:
        raise MediaError("PDF 结构无法安全解析") from exc
    banned = {
        "/javascript", "/js", "/openaction", "/aa", "/launch",
        "/embeddedfile", "/embeddedfiles", "/filespec", "/richmedia",
        "/submitform", "/importdata",
    }
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0

    def walk(value: Any, depth: int = 0) -> None:
        nonlocal visited
        if depth > 80:
            raise MediaError("PDF 对象嵌套超过安全上限")
        visited += 1
        if visited > 200_000:
            raise MediaError("PDF 对象数量超过安全上限")
        if isinstance(value, IndirectObject):
            key = (int(value.idnum), int(value.generation))
            if key in seen_indirect:
                return
            seen_indirect.add(key)
            walk(value.get_object(), depth + 1)
            return
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen_direct:
                return
            seen_direct.add(identity)
            for key, child in value.items():
                if str(key).strip().lower() in banned:
                    raise MediaError("PDF 包含脚本、启动动作或嵌入文件")
                walk(child, depth + 1)
            return
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen_direct:
                return
            seen_direct.add(identity)
            for child in value:
                walk(child, depth + 1)
            return
        if isinstance(value, str) and value.strip().lower() in banned:
            raise MediaError("PDF 包含脚本、启动动作或嵌入文件")

    walk(reader.trailer)


def inspect_document(data: bytes, suffix: str) -> str:
    """Return a closed-list MIME after lightweight structural validation."""
    normalized = str(suffix or "").lower()
    mime = _DOCUMENT_TYPES.get(normalized)
    if mime is None:
        raise MediaError("仅支持 PDF、Markdown、TXT、CSV、TSV、JSON、XLSX 或 DOCX 文件")
    if not data:
        raise MediaError("文件不能为空")
    if normalized == ".pdf":
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
            raise MediaError("PDF 签名或结束标记不正确")
        raw_text = data.decode("latin-1", errors="ignore")
        if TELEGRAM_TOKEN_SCAN_RE.search(raw_text):
            raise MediaError("文件中检测到 Telegram 凭据形状，已拒绝发布")
        lowered_pdf = data.lower()
        if any(
            marker in lowered_pdf
            for marker in (
                b"/javascript", b"/openaction", b"/launch", b"/embeddedfile",
                b"/richmedia", b"/submitform", b"/importdata",
            )
        ):
            raise MediaError("PDF 包含脚本、启动动作或嵌入文件")
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise MediaError("不支持加密 PDF")
            if len(reader.pages) > 2_000:
                raise MediaError("PDF 页数超过安全上限")
            _reject_active_pdf_objects(reader)
            extracted_chars = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                extracted_chars += len(text)
                if extracted_chars > 5_000_000:
                    raise MediaError("PDF 可提取文本超过安全上限")
                if TELEGRAM_TOKEN_SCAN_RE.search(text):
                    raise MediaError("文件中检测到 Telegram 凭据形状，已拒绝发布")
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError("PDF 结构无法安全解析") from exc
        return mime
    if normalized in _ZIP_DOCUMENT_REQUIREMENTS:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > _MAX_ZIP_ENTRIES:
                    raise MediaError("Office 文件结构或条目数量不合法")
                names: set[str] = set()
                lowered_names: set[str] = set()
                total = 0
                for info in infos:
                    name = str(info.filename or "").replace("\\", "/")
                    path = PurePath(name)
                    if (
                        not name
                        or name.startswith("/")
                        or any(part in {"", ".", ".."} for part in path.parts)
                        or info.flag_bits & 0x1
                    ):
                        raise MediaError("Office 文件包含不安全的压缩条目")
                    total += int(info.file_size)
                    if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
                        raise MediaError("Office 文件解压后超过安全上限")
                    if info.file_size and (
                        info.compress_size <= 0
                        or info.file_size > max(1024 * 1024, info.compress_size * 1000)
                    ):
                        raise MediaError("Office 文件压缩比例异常")
                    lowered = name.lower()
                    if lowered in lowered_names:
                        raise MediaError("Office 文件包含重复压缩条目")
                    lowered_names.add(lowered)
                    if (
                        "vbaproject" in lowered
                        or "/embeddings/" in "/" + lowered
                        or "/externallinks/" in "/" + lowered
                        or lowered.endswith("connections.xml")
                    ):
                        raise MediaError("Office 文件包含宏、嵌入对象或外部连接")
                    if not info.is_dir() and (
                        lowered.endswith((".xml", ".rels", ".txt", ".csv"))
                        or "sharedstrings" in lowered
                    ):
                        try:
                            content = archive.read(info)
                        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                            raise MediaError("Office 文件条目无法安全读取") from exc
                        if TELEGRAM_TOKEN_SCAN_RE.search(
                            content.decode("utf-8", errors="ignore")
                        ):
                            raise MediaError(
                                "文件中检测到 Telegram 凭据形状，已拒绝发布"
                            )
                        xml_scan = content.replace(b"\x00", b"")
                        if lowered.endswith(".rels") and _OOXML_EXTERNAL_REL_RE.search(
                            xml_scan
                        ):
                            raise MediaError("Office 文件包含外部关系或远程模板")
                        if normalized == ".xlsx" and _OOXML_FORMULA_RE.search(xml_scan):
                            raise MediaError("Excel 文件包含公式；仅允许静态分析结果")
                    names.add(name)
                if not _ZIP_DOCUMENT_REQUIREMENTS[normalized].issubset(names):
                    raise MediaError("Office 文件缺少必要结构")
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise MediaError("Office 文件结构不正确") from exc
        return mime
    if b"\x00" in data:
        raise MediaError("文本文件包含二进制空字节")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MediaError("文本文件必须使用 UTF-8 编码") from exc
    if TELEGRAM_TOKEN_SCAN_RE.search(text):
        raise MediaError("文件中检测到 Telegram 凭据形状，已拒绝发布")
    if normalized == ".json":
        try:
            json.loads(
                text,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise MediaError("JSON 文件内容不合法") from exc
    if normalized in {".csv", ".tsv"}:
        try:
            rows = csv.reader(
                io.StringIO(text, newline=""),
                delimiter="," if normalized == ".csv" else "\t",
            )
            cells = 0
            for row in rows:
                cells += len(row)
                if cells > 2_000_000:
                    raise MediaError("表格单元格数量超过安全上限")
                for cell in row:
                    stripped = cell.lstrip(" \t\r\n")
                    if not stripped:
                        continue
                    if stripped[0] in {"=", "@"} or (
                        stripped[0] in {"+", "-"}
                        and _SAFE_SIGNED_NUMBER_RE.fullmatch(stripped) is None
                    ):
                        raise MediaError("CSV/TSV 包含可执行表格公式")
        except csv.Error as exc:
            raise MediaError("CSV/TSV 结构不合法") from exc
    return mime


def _clean_alt(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    return (text or fallback)[:300]


def _clean_name(value: Any, *, mime: str) -> str:
    name = PurePath(str(value or "image").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]+", "_", name).strip(" .")
    stem = Path(name or "image").stem[:120].strip(" .") or "image"
    return stem + _EXTENSIONS[mime]


def _clean_document_name(value: Any, *, suffix: str) -> str:
    name = PurePath(str(value or "document").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]+", "_", name).strip(" .")
    stem = Path(name or "document").stem[:120].strip(" .") or "document"
    return stem + suffix


class MediaStore:
    """Immutable media blobs and persistent, non-path-bearing metadata."""

    def __init__(self, console_store: Any):
        self.console_store = console_store
        self.root = Path(console_store.root) / "media"
        self.blobs_dir = self.root / "blobs"
        self.metadata_dir = self.root / "metadata"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

    def _metadata_path(self, media_id: str) -> Path:
        if not _ATTACHMENT_ID_RE.fullmatch(str(media_id or "")):
            raise MediaNotFound("文件不存在")
        return self.metadata_dir / f"{media_id}.json"

    def publish_workspace_image(
        self, workspace_id: str, relative_path: Any, *, session_id: str = "",
        alt: Any = "",
    ) -> Dict[str, Any]:
        """Validate/copy one image owned by an active workspace."""
        if not _WORKSPACE_ID_RE.fullmatch(str(workspace_id or "")):
            raise MediaError("研究空间上下文无效")
        # Lazy import avoids making the core workspace capability depend on the
        # media store during module import.  These helpers already enforce
        # relative-only paths, no secrets, no reparse points, and active owner.
        from guanlan_v2.console import workspace_tools as wt

        self.console_store.workspace_store.require(workspace_id, active_only=True)
        root = wt._root_for(self.console_store, workspace_id)
        source, _relative = wt._resolve(root, relative_path, must_exist=True)
        if not source.is_file():
            raise MediaError("发布路径必须指向图片文件")
        data = _read_regular_file(source, max_bytes=MAX_MEDIA_BYTES)
        mime, width, height = inspect_image(data)
        if source.suffix.lower() not in _SOURCE_EXTENSIONS[mime]:
            raise MediaError("图片扩展名与真实格式不一致")

        media_id = "mi_" + uuid.uuid4().hex
        name = _clean_name(source.name, mime=mime)
        safe_alt = _clean_alt(alt, fallback=Path(name).stem)
        digest = hashlib.sha256(data).hexdigest()
        blob_name = media_id + _EXTENSIONS[mime]
        blob_path = self.blobs_dir / blob_name
        metadata = {
            "schema_version": 1,
            "id": media_id,
            "workspace_id": workspace_id,
            "session_id": str(session_id or "")[:80],
            "name": name,
            "mime": mime,
            "width": width,
            "height": height,
            "size": len(data),
            "sha256": digest,
            "alt": safe_alt,
            "created": _now(),
            "blob_name": blob_name,
        }
        meta_path = self._metadata_path(media_id)
        with _WRITE_LOCK:
            blob_tmp = blob_path.with_suffix(blob_path.suffix + ".tmp")
            meta_tmp = meta_path.with_suffix(".json.tmp")
            try:
                blob_tmp.write_bytes(data)
                blob_tmp.replace(blob_path)
                meta_tmp.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=1, allow_nan=False),
                    encoding="utf-8",
                )
                meta_tmp.replace(meta_path)
            except Exception:
                # Targets are explicit children of the private media store.
                for target in (blob_tmp, meta_tmp, blob_path):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        return self.public_metadata(metadata)

    def publish_workspace_document(
        self, workspace_id: str, relative_path: Any, *, session_id: str = "",
        title: Any = "",
    ) -> Dict[str, Any]:
        """Validate/copy one allowlisted document owned by an active workspace."""
        if not _WORKSPACE_ID_RE.fullmatch(str(workspace_id or "")):
            raise MediaError("研究空间上下文无效")
        from guanlan_v2.console import workspace_tools as wt

        self.console_store.workspace_store.require(workspace_id, active_only=True)
        root = wt._root_for(self.console_store, workspace_id)
        source, _relative = wt._resolve(root, relative_path, must_exist=True)
        if not source.is_file():
            raise MediaError("发布路径必须指向文件")
        suffix = source.suffix.lower()
        data = _read_regular_file(
            source, max_bytes=MAX_DOCUMENT_BYTES, label="文件",
        )
        mime = inspect_document(data, suffix)
        document_id = "do_" + uuid.uuid4().hex
        name = _clean_document_name(source.name, suffix=suffix)
        safe_title = _clean_alt(title, fallback=Path(name).stem)
        digest = hashlib.sha256(data).hexdigest()
        blob_name = document_id + suffix
        blob_path = self.blobs_dir / blob_name
        metadata = {
            "schema_version": 2,
            "kind": "document",
            "id": document_id,
            "workspace_id": workspace_id,
            "session_id": str(session_id or "")[:80],
            "name": name,
            "mime": mime,
            "size": len(data),
            "sha256": digest,
            "title": safe_title,
            "created": _now(),
            "blob_name": blob_name,
        }
        meta_path = self._metadata_path(document_id)
        with _WRITE_LOCK:
            blob_tmp = blob_path.with_suffix(blob_path.suffix + ".tmp")
            meta_tmp = meta_path.with_suffix(".json.tmp")
            try:
                blob_tmp.write_bytes(data)
                blob_tmp.replace(blob_path)
                meta_tmp.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=1, allow_nan=False),
                    encoding="utf-8",
                )
                meta_tmp.replace(meta_path)
            except Exception:
                for target in (blob_tmp, meta_tmp, blob_path):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        return self.public_document_metadata(metadata)

    @staticmethod
    def public_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Strict DTO: no source path, blob path, hash, or session internals."""
        return {
            key: metadata[key]
            for key in (
                "id", "workspace_id", "name", "mime", "width", "height",
                "size", "alt", "created",
            )
        }

    @staticmethod
    def public_document_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: metadata[key]
            for key in (
                "id", "workspace_id", "name", "mime", "size", "title", "created",
            )
        }

    def load(self, media_id: str) -> Tuple[Dict[str, Any], bytes]:
        """Read and revalidate an immutable blob; archived owners remain readable."""
        meta_path = self._metadata_path(media_id)
        if not meta_path.is_file() or _is_reparse(meta_path):
            raise MediaNotFound("图片不存在")
        try:
            raw = meta_path.read_bytes()
            if len(raw) > 16 * 1024:
                raise ValueError("metadata too large")
            metadata = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise MediaNotFound("图片不存在") from exc
        if not isinstance(metadata, dict) or metadata.get("schema_version") not in {1, 2}:
            raise MediaNotFound("文件不存在")
        if metadata.get("id") != media_id:
            raise MediaNotFound("文件不存在")
        workspace_id = str(metadata.get("workspace_id") or "")
        if not _WORKSPACE_ID_RE.fullmatch(workspace_id):
            raise MediaNotFound("文件不存在")
        # Ownership metadata must still point to a real workspace, but archived
        # workspaces are intentionally valid for historic conversation cards.
        if self.console_store.workspace_store.get(workspace_id, active_only=False) is None:
            raise MediaNotFound("文件不存在")
        mime = str(metadata.get("mime") or "")
        if metadata.get("schema_version") == 1:
            suffix = _EXTENSIONS.get(mime)
            kind = "image"
        else:
            suffix = Path(str(metadata.get("name") or "")).suffix.lower()
            kind = str(metadata.get("kind") or "")
            if kind != "document" or _DOCUMENT_TYPES.get(suffix) != mime:
                raise MediaNotFound("文件不存在")
        if not suffix or metadata.get("blob_name") != media_id + suffix:
            raise MediaNotFound("文件不存在")
        blob_path = self.blobs_dir / str(metadata["blob_name"])
        try:
            resolved = blob_path.resolve(strict=True)
            blobs_root = self.blobs_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MediaNotFound("文件不存在") from exc
        if not _inside(blobs_root, resolved) or _is_reparse(blob_path):
            raise MediaNotFound("文件不存在")
        try:
            if kind == "image":
                data = _read_regular_file(blob_path, max_bytes=MAX_MEDIA_BYTES)
                actual_mime, width, height = inspect_image(data)
            else:
                data = _read_regular_file(
                    blob_path, max_bytes=MAX_DOCUMENT_BYTES, label="文件",
                )
                actual_mime = inspect_document(data, suffix)
                width = height = None
        except MediaError as exc:
            raise MediaNotFound("文件不存在") from exc
        dimensions_invalid = kind == "image" and (
            width != metadata.get("width") or height != metadata.get("height")
        )
        if (actual_mime != mime or dimensions_invalid
                or len(data) != metadata.get("size")
                or hashlib.sha256(data).hexdigest() != metadata.get("sha256")):
            raise MediaNotFound("文件不存在")
        return metadata, data

    def _load_owned(
        self, attachment_id: str, session_id: str,
    ) -> Tuple[Dict[str, Any], bytes]:
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise MediaNotFound("文件不存在")
        metadata, data = self.load(attachment_id)
        if metadata.get("session_id") != session_id:
            raise MediaNotFound("文件不存在")
        meta = self.console_store.get_meta(session_id)
        if not isinstance(meta, dict):
            raise MediaNotFound("文件不存在")
        return metadata, data

    def load_for_session(
        self, media_id: str, session_id: str,
    ) -> Tuple[Dict[str, Any], bytes]:
        """Load a fully revalidated image owned by exactly one Console session.

        This is the capability boundary used by outbound channels.  It first
        performs every immutable-blob, format, size and workspace check in
        :meth:`load`, then compares the durable owner to the trusted session
        supplied by the caller.  A missing owner (legacy media), a deleted
        session, or a cross-session id is intentionally indistinguishable from
        an unknown media id.

        Archived sessions remain readable here so an image that was explicitly
        queued while the session was active can still finish delivery.  The
        caller must enforce the writable-session gate before *new* publication.
        """
        metadata, data = self._load_owned(media_id, session_id)
        if metadata.get("schema_version") != 1:
            raise MediaNotFound("图片不存在")
        return metadata, data

    def load_document_for_session(
        self, document_id: str, session_id: str,
    ) -> Tuple[Dict[str, Any], bytes]:
        metadata, data = self._load_owned(document_id, session_id)
        if metadata.get("schema_version") != 2 or metadata.get("kind") != "document":
            raise MediaNotFound("文件不存在")
        return metadata, data

    def load_attachment_for_session(
        self, attachment_id: str, session_id: str,
    ) -> Tuple[Dict[str, Any], bytes]:
        return self._load_owned(attachment_id, session_id)


def artifact_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Stable ``media_image`` payload consumed by Console/SSE/UI adapters."""
    return {
        "media_id": metadata["id"],
        "name": metadata["name"],
        "mime": metadata["mime"],
        "width": metadata["width"],
        "height": metadata["height"],
        "alt": metadata["alt"],
        "workspace_id": metadata["workspace_id"],
    }


def document_artifact_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Stable ``file_document`` payload consumed by Console/SSE/UI adapters."""
    return {
        "document_id": metadata["id"],
        "name": metadata["name"],
        "mime": metadata["mime"],
        "size": metadata["size"],
        "title": metadata["title"],
        "workspace_id": metadata["workspace_id"],
    }
