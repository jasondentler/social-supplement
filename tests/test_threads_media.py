import io
import unittest
from datetime import timezone

import httpx
from PIL import Image

from threads_media import (
    SupplementError,
    ThreadsMediaClient,
    normalize_timestamp,
)

ROOT = "12345678901234567"
USER = "fixture_user"
# These addresses exist only inside MockTransport; no live social content is used.
URL = "https://scontent-fixture.cdninstagram.com/fixture-image-one.jpg"
URL_2 = "https://scontent-fixture.cdninstagram.com/fixture-image-two.webp"
REPLY_LINK = "https://www.threads.com/@fixture_user/post/fixture-post"


def sample_jpeg(color="red") -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (1800, 1200), color).save(b, format="JPEG", quality=90)
    return b.getvalue()


def reply(
    *,
    id="23456789012345678",
    username=USER,
    time="2026-11-16T23:05:02+0000",
    text=None,
    media_type="IMAGE",
    url=URL,
    permalink=REPLY_LINK,
    children=None,
):
    obj = {
        "id": id,
        "username": username,
        "timestamp": time,
        "text": text,
        "media_type": media_type,
        "replied_to": {"id": ROOT},
        "permalink": permalink,
    }
    if url is not None:
        obj["media_url"] = url
    if children is not None:
        obj["children"] = {"data": children}
    return obj


class MockAPI:
    def __init__(self, pages, files=None, redirect=None):
        self.pages = pages
        self.files = files or {URL: sample_jpeg(), URL_2: sample_jpeg("blue")}
        self.redirect = redirect
        self.calls = []

    def response(self, request):
        self.calls.append(request)
        if request.url.host == "graph.threads.net":
            if request.url.path != f"/v1.0/{ROOT}/conversation":
                return httpx.Response(404, json={"error": {"code": 100}})
            if request.headers.get("Authorization") != "Bearer test-token":
                return httpx.Response(401, json={"error": {"code": 190}})
            if request.url.params.get("fields") is None:
                return httpx.Response(400, json={"error": {"code": 100}})
            if request.url.params.get("after") is None:
                return httpx.Response(200, json=self.pages[0])
            return httpx.Response(200, json=self.pages[1])
        uri = str(request.url)
        if self.redirect and uri == self.redirect[0]:
            return httpx.Response(302, headers={"Location": self.redirect[1]})
        if uri in self.files:
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=self.files[uri])
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.response), follow_redirects=False)


class MatchingTests(unittest.TestCase):
    def test_display_timestamp_is_utc_wall_clock(self):
        actual = normalize_timestamp("Nov 16, 2026, 11:05 PM")
        self.assertEqual(actual.hour, 23)
        self.assertEqual(actual.tzinfo, timezone.utc)

    def test_timezone_aware_iso(self):
        actual = normalize_timestamp("2026-11-16T17:05:02-06:00")
        self.assertEqual(actual.hour, 23)

    def test_selects_image_without_downloading_others(self):
        data = [reply(), reply(id="34567890123456789", username="other_fixture_user", url=URL_2)]
        api = MockAPI([{"data": data}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            result = c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertEqual(result.reply_id, "23456789012345678")
        self.assertEqual(result.total_images, 1)
        self.assertEqual(result.reply_url, REPLY_LINK)
        self.assertEqual([(x.index, x.url) for x in result.image_links], [(1, URL)])
        self.assertEqual(len(result.image_items), 1)
        self.assertTrue(result.image_items[0].content.startswith(b"\xff\xd8"))
        img = Image.open(io.BytesIO(result.image_items[0].content))
        self.assertEqual(img.size, (1440, 960))
        self.assertEqual(sum(1 for r in api.calls if r.url.host != "graph.threads.net"), 1)
        self.assertEqual(sum(1 for r in api.calls if r.url.path.endswith("/conversation")), 1)

    def test_paginates_and_uses_second_page(self):
        pages = [
            {
                "data": [reply(username="different")],
                "paging": {"next": "https://graph.threads.net/?after=abc", "cursors": {"after": "abc"}},
            },
            {"data": [reply()]},
        ]
        api = MockAPI(pages)
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            found = c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertEqual(found["username"], USER)
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[1].url.params["after"], "abc")

    def test_same_user_minute_rejected(self):
        a = reply()
        b = reply(id="23456789012345679", time="2026-11-16T23:05:57+0000")
        api = MockAPI([{"data": [a, b]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            with self.assertRaisesRegex(SupplementError, "Ambiguous"):
                c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM")

    def test_same_user_minute_with_exact_text(self):
        a = reply(text="One")
        b = reply(id="23456789012345679", time="2026-11-16T23:05:57+0000", text="Two")
        api = MockAPI([{"data": [a, b]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            found = c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM", text="Two")
        self.assertEqual(found["id"], b["id"])

    def test_empty_text_matches_null(self):
        api = MockAPI([{"data": [reply()]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            found = c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM", text="(no text)")
        self.assertEqual(found["id"], "23456789012345678")

    def test_carousel_children_downloading_only_requested_image(self):
        children = [
            {"id": "1", "media_type": "IMAGE", "media_url": URL},
            {"id": "2", "media_type": "IMAGE", "media_url": URL_2},
        ]
        sample = reply(media_type="CAROUSEL_ALBUM", url=URL, children=children)
        api = MockAPI([{"data": [sample]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            result = c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM", image_indices=[2])
        self.assertEqual([i.index for i in result.image_items], [2])
        self.assertEqual(result.total_images, 2)
        self.assertEqual([(x.index, x.url) for x in result.image_links], [(1, URL), (2, URL_2)])
        cdn_requests = [str(r.url) for r in api.calls if r.url.host != "graph.threads.net"]
        self.assertEqual(cdn_requests, [URL_2])

    def test_10_image_carousel_defaults_to_three(self):
        children = [{"id": str(i), "media_type": "IMAGE", "media_url": URL} for i in range(1, 11)]
        api = MockAPI([{"data": [reply(media_type="CAROUSEL_ALBUM", children=children)]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            result = c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertEqual(result.total_images, 10)
        self.assertEqual([x.index for x in result.image_links], list(range(1, 11)))
        self.assertEqual([x.index for x in result.image_items], [1, 2, 3])
        self.assertEqual(sum(1 for r in api.calls if r.url.host != "graph.threads.net"), 3)

    def test_missing_reply_permalink_is_not_fabricated(self):
        api = MockAPI([{"data": [reply(permalink=None)]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            result = c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertIsNone(result.reply_url)
        self.assertEqual(result.image_links[0].url, URL)

    def test_untrusted_reply_permalink_is_not_returned(self):
        api = MockAPI([{"data": [reply(permalink="https://fake-threads.com/phish")]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            result = c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertIsNone(result.reply_url)

    def test_unselected_carousel_url_is_still_validated(self):
        children = [
            {"id": "1", "media_type": "IMAGE", "media_url": URL},
            {"id": "2", "media_type": "IMAGE", "media_url": "http://127.0.0.1/private"},
        ]
        api = MockAPI([{"data": [reply(media_type="CAROUSEL_ALBUM", children=children)]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            with self.assertRaisesRegex(SupplementError, "unsupported image host"):
                c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM", image_indices=[1])
        self.assertEqual(len([r for r in api.calls if r.url.host != "graph.threads.net"]), 0)

    def test_api_requests_native_permalink_field(self):
        api = MockAPI([{"data": [reply()]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertIn("permalink", api.calls[0].url.params["fields"].split(","))

    def test_index_limit(self):
        api = MockAPI([{"data": [reply()]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            with self.assertRaisesRegex(SupplementError, "image_indices"):
                c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM", image_indices=[1, 2, 3, 4])
        self.assertEqual(len(api.calls), 0)

    def test_ssrf_blocks_non_meta_host(self):
        api = MockAPI([{"data": [reply(url="https://127.0.0.1/private")]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            with self.assertRaisesRegex(SupplementError, "unsupported image host"):
                c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertEqual(len([r for r in api.calls if r.url.host != "graph.threads.net"]), 0)

    def test_redirect_to_non_meta_host_rejected(self):
        api = MockAPI([{"data": [reply()]}], redirect=(URL, "https://internal.local/admin"))
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            with self.assertRaisesRegex(SupplementError, "unsupported image host"):
                c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertEqual(len([r for r in api.calls if r.url.host != "graph.threads.net"]), 1)

    def test_direct_media_object_is_never_requested(self):
        children = [{"id": "45678901234567890", "media_type": "IMAGE", "media_url": URL}]
        api = MockAPI([{"data": [reply(media_type="CAROUSEL_ALBUM", children=children)]}])
        with ThreadsMediaClient("test-token", client=api.client()) as c:
            c.get_images(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertFalse(any(r.url.path.endswith("45678901234567890") for r in api.calls))

    def test_graph_api_token_not_exposed_in_error(self):
        def error(_):
            return httpx.Response(403, json={"error": {"code": 100, "message": "bad token test-token"}})

        with ThreadsMediaClient("test-token", client=httpx.Client(transport=httpx.MockTransport(error))) as c:
            with self.assertRaises(SupplementError) as x:
                c.find_comment(ROOT, USER, "Nov 16, 2026, 11:05 PM")
        self.assertNotIn("test-token", str(x.exception))


if __name__ == "__main__":
    unittest.main()
