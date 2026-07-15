from collections.abc import Iterator
from ipaddress import ip_address

import httpx
import pytest

from focusproof.openhands_runtime.tools.url_safety import (
    Address,
    UrlPolicyError,
    UrlSafetyPolicy,
)


def client_for(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(
        transport=handler,
        follow_redirects=False,
        timeout=httpx.Timeout(1.0),
    )


def public_resolver(hostname: str) -> tuple[Address, ...]:
    del hostname
    return (ip_address("93.184.216.34"),)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "https://localhost./admin",
        "https://sub.localhost./admin",
        "http://127.0.0.1/",
        "https://127.1/",
        "https://2130706433/",
        "https://0x7f000001/",
        "https://0177.0.0.1/",
        "http://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.170.2/v2/credentials",
        "https://[fd00:ec2::254]/latest/meta-data/",
        "http://[fe80::1]/",
        "https://user:secret@example.com/",
    ],
)
def test_policy_blocks_unsafe_targets(value: str) -> None:
    with pytest.raises(UrlPolicyError):
        UrlSafetyPolicy(allow_http=False, resolver=public_resolver).validate(value)


def test_policy_normalizes_https_and_strips_fragment() -> None:
    safe = UrlSafetyPolicy(
        allow_http=False,
        resolver=public_resolver,
    ).validate("HTTPS://Example.COM:443/path?q=1#private-fragment")
    assert safe.normalized == "https://example.com/path?q=1"
    assert safe.hostname == "example.com"
    assert safe.addresses == ("93.184.216.34",)


@pytest.mark.parametrize("blocked", ["10.0.0.8", "fd00::1", "2001:db8::1"])
def test_policy_blocks_hostname_when_any_resolved_address_is_unsafe(
    blocked: str,
) -> None:
    def resolver(hostname: str) -> tuple[Address, ...]:
        del hostname
        return (ip_address("93.184.216.34"), ip_address(blocked))

    with pytest.raises(UrlPolicyError, match="blocked"):
        UrlSafetyPolicy(allow_http=False, resolver=resolver).validate(
            "https://example.com/"
        )


def test_policy_revalidates_redirect_target_with_same_rules() -> None:
    policy = UrlSafetyPolicy(allow_http=False, resolver=public_resolver)
    assert policy.validate("https://example.com/start").hostname == "example.com"
    with pytest.raises(UrlPolicyError):
        policy.validate("http://127.0.0.1/private")


def test_fetcher_revalidates_redirect_target_before_request() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    client = client_for(httpx.MockTransport(handler))
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=15.0,
        )
        with pytest.raises(UrlPolicyError):
            fetcher.fetch("https://example.com/start")
    finally:
        client.close()
    assert requested == ["https://93.184.216.34/start"]


def test_fetcher_blocks_redirect_hostname_that_resolves_private_before_request() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    requested: list[str] = []

    def resolver(hostname: str) -> tuple[Address, ...]:
        if hostname == "example.com":
            return (ip_address("93.184.216.34"),)
        assert hostname == "private.example"
        return (ip_address("10.0.0.8"),)

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://private.example/internal"},
        )

    client = client_for(httpx.MockTransport(handler))
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=resolver),
            client=client,
            total_timeout_seconds=15.0,
        )
        with pytest.raises(UrlPolicyError, match="blocked"):
            fetcher.fetch("https://example.com/start")
    finally:
        client.close()

    assert requested == ["https://93.184.216.34/start"]


def test_fetcher_deadline_includes_initial_policy_and_dns_validation() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    clock = FakeClock()

    def slow_resolver(hostname: str) -> tuple[Address, ...]:
        del hostname
        clock.advance(1.1)
        return (ip_address("93.184.216.34"),)

    requests: list[httpx.Request] = []

    def record_unexpected_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = client_for(
        httpx.MockTransport(record_unexpected_request)
    )
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=slow_resolver),
            client=client,
            total_timeout_seconds=1.0,
            clock=clock,
        )
        with pytest.raises(UrlFetchError) as captured:
            fetcher.fetch("https://example.com/guide")
    finally:
        client.close()

    assert captured.value.code == "network_timeout"
    assert requests == []


def test_fetcher_deadline_covers_redirect_validation() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    clock = FakeClock()

    def slow_resolver(hostname: str) -> tuple[Address, ...]:
        del hostname
        clock.advance(0.3)
        return (ip_address("93.184.216.34"),)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        clock.advance(0.5)
        return httpx.Response(
            302,
            headers={"location": "https://redirect.example/next"},
        )

    client = client_for(httpx.MockTransport(handler))
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=slow_resolver),
            client=client,
            total_timeout_seconds=1.0,
            clock=clock,
        )
        with pytest.raises(UrlFetchError) as captured:
            fetcher.fetch("https://example.com/start")
    finally:
        client.close()

    assert captured.value.code == "network_timeout"
    assert len(requests) == 1


def test_fetcher_pins_connection_to_policy_validated_address() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"safe",
        )

    client = client_for(httpx.MockTransport(handler))
    try:
        BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=15.0,
        ).fetch("https://example.com/guide")
    finally:
        client.close()
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"


def test_fetcher_dns_rebinding_cannot_change_the_pinned_request_address() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    resolver_results = iter(
        [
            (ip_address("93.184.216.34"),),
            (ip_address("127.0.0.1"),),
        ]
    )
    resolver_calls: list[str] = []
    requests: list[httpx.Request] = []

    def rebinding_resolver(hostname: str) -> tuple[Address, ...]:
        resolver_calls.append(hostname)
        return next(resolver_results)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"safe",
        )

    client = client_for(httpx.MockTransport(handler))
    try:
        BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=rebinding_resolver),
            client=client,
            total_timeout_seconds=15.0,
        ).fetch("https://rebind.example/guide")
    finally:
        client.close()

    assert resolver_calls == ["rebind.example"]
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "rebind.example"


def test_fetcher_disables_connection_and_cookie_reuse_for_pinned_requests() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "set-cookie": "next=secret"},
            content=b"safe",
        )

    client = client_for(httpx.MockTransport(handler))
    client.cookies.set("prior", "secret", domain="93.184.216.34")
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=15.0,
        )
        fetcher.fetch("https://one.example/guide")
        fetcher.fetch("https://two.example/guide")
    finally:
        client.close()

    assert len(requests) == 2
    assert [request.headers["host"] for request in requests] == [
        "one.example",
        "two.example",
    ]
    assert all(request.headers["connection"] == "close" for request in requests)
    assert all("cookie" not in request.headers for request in requests)


def test_fetcher_rejects_more_than_three_redirects() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            302,
            headers={"location": f"https://example.com/{index + 1}"},
        )

    client = client_for(httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError) as captured:
            BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
                client=client,
                total_timeout_seconds=15.0,
            ).fetch("https://example.com/0")
    finally:
        client.close()
    assert captured.value.code == "too_many_redirects"


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_fetcher_maps_connection_and_read_timeouts(
    error_type: type[httpx.TimeoutException],
) -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("timed out", request=request)

    client = client_for(httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError) as captured:
            BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
                client=client,
                total_timeout_seconds=15.0,
            ).fetch("https://example.com/timeout")
    finally:
        client.close()
    assert captured.value.code == "network_timeout"


def test_fetcher_rejects_content_length_over_limit() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "1048577"},
            request=request,
        )
    )
    client = client_for(transport)
    try:
        with pytest.raises(UrlFetchError) as captured:
            BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
                client=client,
                total_timeout_seconds=15.0,
            ).fetch("https://example.com/large")
    finally:
        client.close()
    assert captured.value.code == "response_too_large"


class ChunkStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"a" * 700_000
        yield b"b" * 400_000


class SlowChunkStream(httpx.SyncByteStream):
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.closed = False
        self.yielded = 0

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(10):
            self.clock.advance(0.4)
            self.yielded += 1
            yield b"small"

    def close(self) -> None:
        self.closed = True


def test_fetcher_stops_and_closes_slow_stream_at_total_deadline() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    clock = FakeClock()
    stream = SlowChunkStream(clock)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
            request=request,
        )
    )
    client = client_for(transport)
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=1.0,
            clock=clock,
        )
        with pytest.raises(UrlFetchError) as captured:
            fetcher.fetch("https://example.com/slow")
    finally:
        client.close()

    assert captured.value.code == "network_timeout"
    assert stream.closed is True
    assert stream.yielded == 3


def test_fetcher_stops_when_streamed_body_crosses_limit() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=ChunkStream(),
            request=request,
        )
    )
    client = client_for(transport)
    try:
        with pytest.raises(UrlFetchError) as captured:
            BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
                client=client,
                total_timeout_seconds=15.0,
            ).fetch("https://example.com/stream")
    finally:
        client.close()
    assert captured.value.code == "response_too_large"


def test_fetcher_rejects_unsupported_binary_content() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"binary",
            request=request,
        )
    )
    client = client_for(transport)
    try:
        with pytest.raises(UrlFetchError) as captured:
            BoundedUrlFetcher(
                policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
                client=client,
                total_timeout_seconds=15.0,
            ).fetch("https://example.com/file")
    finally:
        client.close()
    assert captured.value.code == "content_type_unsupported"


def test_fetcher_extracts_bounded_html_title_and_text() -> None:
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    html = b"<html><head><title> Replay Guide </title></head><body>Append then replay.</body></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html,
            request=request,
        )
    )
    client = client_for(transport)
    try:
        result = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=15.0,
        ).fetch("https://example.com/guide#fragment")
    finally:
        client.close()
    assert result.final_url == "https://example.com/guide"
    assert result.status_code == 200
    assert result.title == "Replay Guide"
    assert result.text_excerpt == "Replay Guide Append then replay."
    assert result.content_length == len(html)
    assert result.redirect_chain == ()


def test_fetcher_deadline_covers_metadata_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import focusproof.openhands_runtime.tools.url_fetcher as fetcher_module
    from focusproof.openhands_runtime.tools.url_fetcher import (
        BoundedUrlFetcher,
        UrlFetchError,
    )

    clock = FakeClock()

    def slow_extract(text: str, *, is_html: bool) -> str:
        del text, is_html
        clock.advance(1.1)
        return "bounded excerpt"

    monkeypatch.setattr(fetcher_module, "_extract_text", slow_extract)
    client = client_for(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                content=b"safe",
                request=request,
            )
        )
    )
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=public_resolver),
            client=client,
            total_timeout_seconds=1.0,
            clock=clock,
        )
        with pytest.raises(UrlFetchError) as captured:
            fetcher.fetch("https://example.com/metadata")
    finally:
        client.close()

    assert captured.value.code == "network_timeout"
