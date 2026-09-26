"""Turns a file's bytes into a Bedrock Converse content block the model can read.

Shared by Slack attachments (attachments.py) and links (web_fetch.py):

  * PNG, JPEG, GIF, WebP                 -> `image` block, downscaled first if large
  * PDF, DOCX, DOC, XLSX, XLS, CSV, HTML, TXT, MD -> `document` block (Bedrock parses it)
  * JSON, YAML, logs, source code, ...   -> `document` block with format "txt"
  * anything else                        -> Unsupported, with a reason to tell the user

One ContentBudget lives for one invocation and enforces Converse's per-request limits,
since every file opened in a turn is resent on each model call of that turn.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Bedrock Converse limits (Anthropic models): 3.75 MB per image, 4.5 MB per document,
# 20 images and 5 documents per request.
MAX_IMAGE_BYTES = 3_750_000
MAX_DOCUMENT_BYTES = 4_500_000
MAX_IMAGES = 20
MAX_DOCUMENTS = 5

# Phone photos are often bigger than MAX_IMAGE_BYTES, so images are downloaded up to
# this size and then scaled down. Claude reads images at up to 1568 px on the long
# edge and scales anything larger down itself, so sending more only costs time.
MAX_IMAGE_DOWNLOAD_BYTES = 20_000_000
MAX_IMAGE_EDGE = 1568
# Refuse to decode anything bigger (a "decompression bomb" of a few KB can claim more).
Image.MAX_IMAGE_PIXELS = 50_000_000

IMAGE = "image"
DOCUMENT = "document"

_IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
_DOCUMENT_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
}
_DOCUMENT_MIMETYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "text/csv": "csv",
    "text/html": "html",
    "text/markdown": "md",
    "text/plain": "txt",
}
# Text in all but name: read as a plain-text document once it proves to be UTF-8.
_TEXT_MIMETYPES = {"application/json", "application/xml", "application/x-yaml", "application/yaml", "application/sql"}
_TEXT_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf", ".env", ".log", ".sql", ".tf", ".hcl",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cpp",
    ".swift", ".sh", ".bash", ".zsh", ".ps1", ".css", ".scss", ".graphql", ".proto", ".diff", ".patch",
}  # fmt: skip
_UNSUPPORTED_KINDS = {"audio": "audio files", "video": "video files"}

# Converse document names: letters, digits, single spaces, hyphens, parentheses and
# square brackets only. The name is also something the model reads, so keep it short.
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9\-()\[\] ]+")
_MAX_NAME_CHARS = 100


class Unsupported(Exception):
    """A file the model can't be given. str() is a short reason fit to show the user."""


@dataclass(frozen=True)
class Kind:
    block: str
    """IMAGE or DOCUMENT."""
    format: str
    """The Converse format, e.g. "png" or "pdf"."""
    max_download_bytes: int


def classify(name: str, mimetype: str) -> Kind:
    """What block a file becomes, from its name and MIME type, before downloading it. Raises Unsupported."""
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    extension = PurePosixPath(name or "").suffix.lower()
    if mimetype in _IMAGE_FORMATS:
        return Kind(IMAGE, _IMAGE_FORMATS[mimetype], MAX_IMAGE_DOWNLOAD_BYTES)
    document_format = _DOCUMENT_FORMATS.get(extension) or _DOCUMENT_MIMETYPES.get(mimetype)
    if document_format:
        return Kind(DOCUMENT, document_format, MAX_DOCUMENT_BYTES)
    if mimetype.startswith("text/") or mimetype in _TEXT_MIMETYPES or extension in _TEXT_EXTENSIONS:
        return Kind(DOCUMENT, "txt", MAX_DOCUMENT_BYTES)
    kind = _UNSUPPORTED_KINDS.get(mimetype.split("/")[0])
    if kind:
        raise Unsupported(f"I can't open {kind} yet")
    if mimetype.startswith("image/"):
        raise Unsupported("I can only read PNG, JPEG, GIF and WebP images")
    raise Unsupported(f"I can't open {extension or 'this kind of'} files")


def check_size(kind: Kind, size: int) -> None:
    """Raises Unsupported if a file of `size` bytes is over the download limit for its kind."""
    if size > kind.max_download_bytes:
        raise Unsupported(f"it's larger than the {kind.max_download_bytes // 1_000_000} MB I can read")


@dataclass
class ContentBudget:
    """Counts the images and documents opened in one invocation against Converse's limits."""

    images: int = 0
    documents: int = 0
    _names: set[str] = field(default_factory=set)

    def check(self, kind: Kind) -> None:
        """Raises Unsupported if one more file of this kind would exceed the request limits."""
        if kind.block == IMAGE and self.images >= MAX_IMAGES:
            raise Unsupported(f"I can read at most {MAX_IMAGES} images at a time")
        if kind.block == DOCUMENT and self.documents >= MAX_DOCUMENTS:
            raise Unsupported(f"I can read at most {MAX_DOCUMENTS} documents at a time")

    def block(self, kind: Kind, name: str, data: bytes) -> dict:
        """The content block for a downloaded file. Raises Unsupported."""
        self.check(kind)
        check_size(kind, len(data))
        if kind.block == IMAGE:
            image_format, data = _prepare_image(data, kind.format)
            self.images += 1
            return {"image": {"format": image_format, "source": {"bytes": data}}}
        if kind.format == "txt":
            _require_text(data)
        self.documents += 1
        return {"document": {"format": kind.format, "name": self._unique_name(name), "source": {"bytes": data}}}

    def _unique_name(self, name: str) -> str:
        """Converse rejects two documents with the same name in one request."""
        base = " ".join(_NAME_UNSAFE.sub(" ", name).split())[:_MAX_NAME_CHARS] or "file"
        candidate, number = base, 2
        while candidate in self._names:
            candidate, number = f"{base} ({number})", number + 1
        self._names.add(candidate)
        return candidate


def _prepare_image(data: bytes, image_format: str) -> tuple[str, bytes]:
    """Checks the bytes really are an image, and scales it down if it's large."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            too_big = max(image.size) > MAX_IMAGE_EDGE or len(data) > MAX_IMAGE_BYTES
            if not too_big:
                return image_format, data
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            return _encode(image, image_format)
    except Unsupported:
        raise
    except Exception as err:  # PIL raises many types for corrupt or hostile input
        logger.info("Could not read an image: %s", err)
        raise Unsupported("it doesn't look like a valid image") from err


def _encode(image: Image.Image, image_format: str) -> tuple[str, bytes]:
    """JPEG stays JPEG; everything else becomes PNG, or JPEG if the PNG is still too big."""
    candidates = ["jpeg"] if image_format == "jpeg" else ["png", "jpeg"]
    for candidate in candidates:
        out = io.BytesIO()
        if candidate == "jpeg":
            image.convert("RGB").save(out, format="JPEG", quality=85)
        else:
            image.save(out, format="PNG", optimize=True)
        if out.tell() <= MAX_IMAGE_BYTES:
            return candidate, out.getvalue()
    raise Unsupported("the image is too detailed to send even after scaling it down")


def _require_text(data: bytes) -> None:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise Unsupported("it isn't a text file I can read") from err
