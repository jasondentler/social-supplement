"""Read-only Threads reply image retrieval, independent of the MCP transport.

Only the caller's *own* root Threads posts are expected to be queried. We do not
make any social-media writes, and never log credentials or media URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import re
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


GRAPH_ORIGIN = "https://graph.threads.net"
MAX_PAGES = 100
PAGE_SIZE = 100
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 2_400_000
MAX_IMAGE_COUNT = 3
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
FIELDS = "id,username,timestamp,text,permalink,media_type,media_url,replied_to,children{id,media_type,media_url}"


class SupplementError(Exception):
    """An expected, user-actionable failure (no secrets in the message)."""


@dataclass(frozen=True)
class ImageItem:
    index: int
    content: bytes
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class ImageLink:
    """Original Threads CDN URL for a single still-image position."""

    index: int
    url: str


@dataclass(frozen=True)
class ImageResult:
    thread_id: str
    reply_id: str
    username: str
    timestamp: str
    media_type: str
    total_images: int
    reply_url: str | None
    image_links: tuple[ImageLink, ...]
    image_items: tuple[ImageItem, ...]
    failures: tuple[str, ...] = ()


def normalize_timestamp(value: str) -> datetime:
    """Return a UTC time.

    Unzoned timestamps from supported comment listings may represent UTC wall
    clock values. Unzoned strings are interpreted as UTC, NOT local time.
    """
    if not value or not isinstance(value, str):
        raise SupplementError("A comment timestamp is required")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in (
            "%b %d, %Y, %I:%M %p",  # Common inbox display
            "%b %d, %Y, %I:%M:%S %p",
            "%B %d, %Y, %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise SupplementError("Unrecognized timestamp; supply a known UTC time or ISO 8601")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_seconds(value: str) -> bool:
    raw = value.strip()
    return bool(re.search(r"T\d\d:\d\d:\d\d", raw) or re.search(r"\d\d:\d\d:\d\d", raw))


def _is_same_comment(
    comment: dict, username: str, timestamp: datetime, text: str | None, precision_seconds: bool
) -> bool:
    if str(comment.get("username", "")).lstrip("@").casefold() != username.lstrip("@").casefold():
        return False
    try:
        remote = normalize_timestamp(str(comment["timestamp"]))
    except (KeyError, SupplementError):
        return False
    if precision_seconds:
        if remote != timestamp:
            return False
    elif remote.replace(second=0, microsecond=0) != timestamp.replace(second=0, microsecond=0):
        return False
    if text is not None:
        expected = text.strip()
        actual = (comment.get("text") or "").strip()
        if expected in ("(no text)", ""):
            expected = ""
        if expected != actual:
            return False
    return True


def _validate_media_url(url: str) -> None:
    try:
        p = urlsplit(url)
        hostname = (p.hostname or "").lower()
        valid = (
            p.scheme == "https"
            and p.username is None
            and p.password is None
            and (p.port is None or p.port == 443)
            and any(hostname == d or hostname.endswith("." + d) for d in ("cdninstagram.com", "fbcdn.net"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise SupplementError("Threads returned an unsupported image host; download refused")


def _validated_reply_url(value: object) -> str | None:
    """Keep only HTTPS comment permalinks on official Threads domains."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        ):
            return value
    except ValueError:
        pass
    return None


class ThreadsMediaClient:
    def __init__(self, access_token: str, client: httpx.Client | None = None):
        if not access_token or not access_token.strip():
            raise SupplementError("THREADS_TOKEN is missing from the MCP server environment")
        self._token = access_token.strip()
        self.client = client or httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ThreadsMediaClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _api_get(self, endpoint: str, params: dict) -> dict:
        try:
            resp = self.client.get(
                GRAPH_ORIGIN + endpoint,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
                follow_redirects=False,
            )
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupplementError("Threads API request failed or returned invalid JSON") from exc
        if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = err.get("code", resp.status_code)
            msg = str(err.get("message", "API access denied"))
            # Explicitly avoid ever reflecting secrets or URLs in error messages.
            if "dev mode" in msg.lower():
                raise SupplementError("Threads API reports development-mode restriction for this request")
            raise SupplementError(f"Threads API error {code} (HTTP {resp.status_code})")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise SupplementError("Threads API returned an unexpected response structure")
        return payload

    def find_comment(self, thread_id: str, username: str, timestamp: str, text: str | None = None) -> dict:
        if not re.fullmatch(r"\d{8,32}", thread_id):
            raise SupplementError("thread_id must be a numeric Threads root post ID")
        if not username or not username.strip():
            raise SupplementError("An exact Threads username is required")
        expected = normalize_timestamp(timestamp)
        has_seconds = _has_seconds(timestamp)
        cursor: str | None = None
        seen_cursors: set[str] = set()
        matches: list[dict] = []
        for _ in range(MAX_PAGES):
            params: dict = {"fields": FIELDS, "reverse": "false", "limit": PAGE_SIZE}
            if cursor:
                params["after"] = cursor
            page = self._api_get(f"/v1.0/{thread_id}/conversation", params)
            for comment in page["data"]:
                if isinstance(comment, dict) and _is_same_comment(comment, username, expected, text, has_seconds):
                    matches.append(comment)
                    if len(matches) > 1:
                        raise SupplementError(
                            "Ambiguous comment match: multiple replies have the same username and timestamp; "
                            "include the exact comment text or a more precise timestamp"
                        )
            paging = page.get("paging", {})
            cursors = paging.get("cursors", {}) if isinstance(paging, dict) else {}
            next_cursor = cursors.get("after") if isinstance(cursors, dict) and paging.get("next") else None
            if not next_cursor:
                break
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise SupplementError("Invalid Threads pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise SupplementError(
                "Thread exceeds the configured pagination limit; no safe unique match can be guaranteed"
            )
        if not matches:
            raise SupplementError("No matching Threads reply found; check username, timestamp, and text")
        return matches[0]

    def _download(self, url: str) -> bytes:
        for _ in range(4):
            _validate_media_url(url)
            try:
                with self.client.stream("GET", url, follow_redirects=False) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise SupplementError("Image CDN redirect had no destination")
                        url = urljoin(url, loc)
                        continue
                    if resp.status_code != 200:
                        raise SupplementError(f"Image CDN returned HTTP {resp.status_code}; URL may have expired")
                    chunks = []
                    count = 0
                    for chunk in resp.iter_bytes():
                        count += len(chunk)
                        if count > MAX_SOURCE_BYTES:
                            raise SupplementError("Image source exceeds the 8 MB download limit")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPError as exc:
                raise SupplementError("Could not download image bytes from the Threads CDN") from exc
        raise SupplementError("Too many image CDN redirects")

    @staticmethod
    def _optimize(source: bytes, max_dimension: int) -> bytes:
        try:
            with Image.open(BytesIO(source)) as img:
                if img.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                    raise SupplementError("Unsupported image format")
                if img.width * img.height > MAX_PIXELS:
                    raise SupplementError("Image dimensions exceed the safety limit")
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "L"):
                    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
                        rgba = img.convert("RGBA")
                        bg = Image.new("RGBA", rgba.size, "white")
                        bg.alpha_composite(rgba)
                        img = bg.convert("RGB")
                    else:
                        img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                output = BytesIO()
                img.save(output, format="JPEG", quality=78, optimize=True)
                return output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise SupplementError("Image could not be decoded safely") from exc

    def get_images(
        self,
        thread_id: str,
        username: str,
        timestamp: str,
        text: str | None = None,
        image_indices: list[int] | None = None,
        max_dimension: int = 1440,
    ) -> ImageResult:
        if not (320 <= max_dimension <= 2048):
            raise SupplementError("max_dimension must be between 320 and 2048")
        if image_indices is not None:
            if (
                not image_indices
                or len(image_indices) > MAX_IMAGE_COUNT
                or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in image_indices)
                or len(set(image_indices)) != len(image_indices)
            ):
                raise SupplementError(
                    f"image_indices must contain 1–{MAX_IMAGE_COUNT} distinct positive 1-based indices"
                )
        comment = self.find_comment(thread_id, username, timestamp, text)
        media_type = str(comment.get("media_type") or "TEXT_POST")
        targets: list[tuple[int, str]] = []
        if media_type == "IMAGE" and comment.get("media_url"):
            targets = [(1, comment["media_url"])]
        elif media_type == "CAROUSEL_ALBUM":
            children = (comment.get("children") or {}).get("data", [])
            if not isinstance(children, list) or not children:
                raise SupplementError("Carousel child metadata was not supplied by Threads")
            for i, child in enumerate(children, 1):
                if not isinstance(child, dict):
                    continue
                # Reserve each position, even if it happens to be a video (not an image).
                if child.get("media_type") == "IMAGE" and child.get("media_url"):
                    targets.append((i, child["media_url"]))
        elif media_type == "VIDEO":
            raise SupplementError("This supplement handles still images, not videos")
        if not targets:
            raise SupplementError("This reply contains no retrievable still images")
        # Return links for *every* still image, even when only three image bytes
        # are requested. The URLs are signed and may expire.
        for _, url in targets:
            _validate_media_url(url)
        image_links = tuple(ImageLink(index=i, url=url) for i, url in targets)
        total_images = len(targets)
        by_index = dict(targets)
        requested = image_indices or sorted(by_index)[:MAX_IMAGE_COUNT]
        if any(i not in by_index for i in requested):
            raise SupplementError("One or more requested indices are not available still images")
        images: list[ImageItem] = []
        failures: list[str] = []
        total_bytes = 0
        for i in requested:
            try:
                processed = self._optimize(self._download(by_index[i]), max_dimension)
                if total_bytes + len(processed) > MAX_TOTAL_IMAGE_BYTES:
                    failures.append(f"Image {i}: response size cap reached; request fewer images")
                    break
                images.append(ImageItem(index=i, content=processed))
                total_bytes += len(processed)
            except SupplementError as exc:
                failures.append(f"Image {i}: {exc}")
        if not images:
            raise SupplementError("No images could be returned: " + "; ".join(failures))
        return ImageResult(
            thread_id=thread_id,
            reply_id=str(comment["id"]),
            username=str(comment["username"]),
            timestamp=str(comment["timestamp"]),
            media_type=media_type,
            total_images=total_images,
            reply_url=_validated_reply_url(comment.get("permalink")),
            image_links=image_links,
            image_items=tuple(images),
            failures=tuple(failures),
        )
