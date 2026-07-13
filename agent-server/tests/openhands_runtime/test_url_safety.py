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


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
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
        )
        with pytest.raises(UrlPolicyError):
            fetcher.fetch("https://example.com/start")
    finally:
        client.close()
    assert requested == ["https://93.184.216.34/start"]


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
        ).fetch("https://example.com/guide")
    finally:
        client.close()
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"


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
            ).fetch("https://example.com/large")
    finally:
        client.close()
    assert captured.value.code == "response_too_large"


class ChunkStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"a" * 700_000
        yield b"b" * 400_000


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
        ).fetch("https://example.com/guide#fragment")
    finally:
        client.close()
    assert result.final_url == "https://example.com/guide"
    assert result.status_code == 200
    assert result.title == "Replay Guide"
    assert result.text_excerpt == "Replay Guide Append then replay."
    assert result.content_length == len(html)
    assert result.redirect_chain == ()
