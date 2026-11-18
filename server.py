"""Social Supplement MCP — extensible tools for social-media workflows."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, Icon, ImageContent, TextContent

from threads_media import SupplementError, ThreadsMediaClient


def _icon() -> list[Icon]:
    """Advertise real PNG bitmap logos for both client color schemes."""
    icons = []
    for theme in ("light", "dark"):
        path = Path(__file__).parent / "assets" / f"social-supplement-{theme}.png"
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        icons.append(
            Icon(
                src=f"data:image/png;base64,{encoded}",
                mime_type="image/png",
                sizes=["512x512"],
                theme=theme,
            )
        )
    return icons


mcp = MCPServer(
    "Social Supplement MCP",
    version="0.3.0",
    description="An extensible collection of supplemental social-media MCP tools for any compatible client",
    instructions=(
        "Social Supplement MCP provides additional read-only social-media capabilities. "
        "Use get_threads_comment_images for a specific known comment on one of your Threads posts. "
        "The AI should inspect native image blocks; no vision analysis occurs in this MCP. "
        "Unzoned comment timestamps are interpreted as UTC by the Threads adapter. "
        "Never infer an image for an ambiguous comment match."
    ),
    icons=_icon(),
)


@mcp.tool(icons=_icon())
def get_threads_comment_images(
    thread_id: str,
    username: str,
    timestamp: str,
    text: str | None = None,
    image_indices: list[int] | None = None,
    max_dimension: int = 1440,
) -> CallToolResult:
    """Return native image blocks for ONE known Threads comment.

    Args:
      thread_id: Numeric ID of the root Threads post.
      username: Exact comment author username.
      timestamp: Comment timestamp, e.g. 'Nov 16, 2026, 11:05 PM',
        or an ISO 8601 timestamp with timezone. Naive timestamps are treated as UTC.
      text: Optional exact comment text, useful when one user replied twice in a minute.
      image_indices: Optional 1-based photo positions; maximum 3. By default, the
        first 3 images are returned, even for carousels with more than 3.
      max_dimension: Maximum image dimension in pixels (320–2048, default 1440).

    Returns the exact Threads comment permalink and direct CDN URL of each still
    image (including carousel slides not selected for byte download). These URLs
    may expire. Does not modify or publish social media content.
    """
    try:
        with ThreadsMediaClient(os.environ.get("THREADS_TOKEN", "")) as client:
            result = client.get_images(
                thread_id=thread_id,
                username=username,
                timestamp=timestamp,
                text=text,
                image_indices=image_indices,
                max_dimension=max_dimension,
            )
    except SupplementError as exc:
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=str(exc))],
        )
    blocks: list[TextContent | ImageContent] = [
        TextContent(
            type="text",
            text=(
                f"Threads reply {result.reply_id} by @{result.username}, "
                f"{result.timestamp}; {result.total_images} still image(s). "
                f"This response includes {len(result.image_items)} image(s). "
                f"Direct reply link: {result.reply_url or 'unavailable from Threads API'}. "
                "Direct image links are original, expiring Threads CDN URLs. "
                "For remaining carousel slides, call again with image_indices. "
                "This tool cannot publish replies."
            ),
        )
    ]
    for link in result.image_links:
        blocks.append(
            TextContent(type="text", text=f"Direct image {link.index} URL: {link.url}")
        )
    for image in result.image_items:
        blocks.append(
            TextContent(
                type="text",
                text=f"Carousel/image position {image.index} ({result.total_images} still image(s) available)",
            )
        )
        blocks.append(
            ImageContent(
                type="image",
                mime_type=image.mime_type,
                data=base64.b64encode(image.content).decode("ascii"),
            )
        )
    for failure in result.failures:
        blocks.append(TextContent(type="text", text=f"Warning: {failure}"))
    return CallToolResult(content=blocks)


if __name__ == "__main__":
    mcp.run(transport="stdio")
