<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/social-supplement-dark.png">
  <img src="assets/social-supplement-light.png" width="192" alt="Orange vitamin tablet with an engraved social-media glyph">
</picture>

# Social Supplement MCP

An extensible collection of **supplemental social-media tools** using the open [Model Context Protocol](https://modelcontextprotocol.io/). It is **independent of ChatGPT, individual social platforms, and social-media schedulers**.

The server exposes **MCP over stdio** for any compatible MCP client. The OpenAI Secure MCP Tunnel is an **optional remote transport**, not a requirement. The first tool retrieves images posted in replies to your own Threads posts; additional adapters may be added later. Image interpretation and posting are left to the caller.

## Setup

Requirements: Python 3.11+, Make, and an OS keyring. macOS Keychain is supported.

```sh
make setup        # create Python .venv and install development dependencies
make config       # securely store Threads token in the OS keyring
make check        # Ruff lint, formatting check, and unit tests
make serve        # start the OpenAI tunnel and its supervised stdio MCP
```

To use the server **without** the tunnel, configure any stdio MCP client to execute:

```json
{
  "command": "make",
  "args": ["--no-print-directory", "-s", "-C", "/Users/jasondentler/projects/social-supplement", "stdio"]
}
```

Replace the example directory when installing elsewhere. A client starts and controls the direct stdio process using `make stdio`. The Makefile sends quality-check output to stderr, keeping stdout reserved for the MCP JSON-RPC protocol.

Alternatively, run `.venv/bin/python -m scripts.serve` after `make check` for direct stdio. The Python process loads the provider token from the OS keyring without printing it.

### Tunnel-based startup (`make serve`)

To use the OpenAI Secure MCP Tunnel, run:

```sh
make config-tunnel   # store tunnel key and tunnel ID in the OS keyring
make serve           # run quality checks, then start tunnel-client and the MCP
```

`make serve` launches `tunnel-client`, which supervises the local MCP child process. The `tunnel-client` binary must be installed separately. This is the default development workflow, but **the MCP itself does not require the tunnel**: use `make stdio` with any compatible client.

## Tools

### `get_threads_comment_images`

Fetch image attachments for **one known comment** on a Threads post you own.

```python
get_threads_comment_images(
    thread_id: str,
    username: str,
    timestamp: str,
    text: str | None = None,
    image_indices: list[int] | None = None,
    max_dimension: int = 1440,
)
```

The caller supplies the post ID, commenter username, and timestamp from any existing inbox or comment-listing tool. `text` optionally disambiguates multiple comments by the same user. Unzoned timestamps are interpreted as UTC; use ISO 8601 with an explicit timezone offset when available.

The tool fetches paginated reply **metadata** until a single match is established. It returns the **native comment permalink** from Threads plus a **direct CDN link for every still image** in that reply (including all carousel slides). It also downloads selected images and returns them as native MCP `ImageContent` alongside identifying text. The default request includes image bytes for up to three images; additional slides can be requested using one-based `image_indices` without losing links to the others. **Direct image URLs are signed and may expire; request them again to refresh.** If Threads omits a comment permalink, the tool reports it as unavailable instead of guessing. The MCP does not interpret, publish, edit, or delete social media content.

Image requests enforce an 8 MB source cap per image, a 40-megapixel decode cap, a 2.4 MB total response-image cap, HTTPS Meta CDN host validation, and bounded pagination. Images are downscaled, converted to JPEG, and stripped of source metadata. If comment matching is ambiguous, the tool refuses to select an image.

## Branding

The two **actual transparent PNG** files are `assets/social-supplement-light.png` and `assets/social-supplement-dark.png`. Both use the orange vitamin tablet with an invented engraved social-media glyph. The README and MCP icon metadata select the variant based on the client's color scheme. No fake SVG wrappers or `.b64` image files are required.

## Development

```sh
make lint        # Ruff static checks and formatting validation
make format      # apply Ruff formatter
make test        # run offline unit tests
make check       # gate for both lint and tests
make hooks       # enable pre-commit checks and commit-message lint
```

Commits require explicit user approval. The Git hook enforces a **single-line Conventional Commit** message; hooks do not initiate commits.
