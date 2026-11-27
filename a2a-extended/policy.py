"""Configuration and credential-bearing origin policy."""

from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from io import BufferedReader, RawIOBase
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request


class Refused(ValueError):
    pass


def resolve(peer):
    from hermes_cli.config import load_config_readonly

    peers = (load_config_readonly() or {}).get("a2a_agents") or {}
    entry = peers.get(peer)
    if not isinstance(entry, dict):
        raise Refused("Peer is not configured.")
    auth = entry.get("auth") or {}
    token = auth.get("token", "")
    if not isinstance(token, str) or any(not 33 <= ord(c) <= 126 for c in token):
        raise Refused("Invalid bearer credential configuration.")
    url = entry.get("url")
    origin(url)
    if urlsplit(url).query or urlsplit(url).fragment:
        raise Refused("Configured peer URL must not contain a query or fragment.")
    return url, token, entry.get("tenant")


def origin(url):
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
            raise ValueError
        parts = urlsplit(url)
        defaults = {"http": 80, "https": 443}
        if parts.scheme not in defaults or not parts.hostname or parts.username is not None:
            raise ValueError
        return parts.scheme, parts.hostname.lower(), parts.port if parts.port is not None else defaults[parts.scheme]
    except ValueError:
        raise Refused("Invalid peer or interface URL.") from None


def check(url, trusted):
    # Operator-approved aliases could be added in a future version.
    if origin(url) != trusted:
        raise Refused("Cross-origin peer URL or redirect refused.")


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, trusted):
        self.trusted = trusted

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check(newurl, self.trusted)
        except Refused:
            fp.close()
            raise
        # Never repeat a dispatched task or follow a redirect that changes POST to GET.
        # 307/308 explicitly indicate that the task endpoint has moved unchanged.
        if req.get_method() == "POST":
            if code in (307, 308):
                return Request(newurl, data=req.data, headers=dict(req.header_items()), method="POST")
            fp.close()
            raise Refused("Task redirect would change the request method.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    http_error_308 = HTTPRedirectHandler.http_error_302


def _activity_handlers(activity):
    """Observe actual HTTP bytes, including partial headers and chunk framing."""
    class Reader(RawIOBase):
        def __init__(self, source):
            self.source = source

        def readable(self):
            return True

        def readinto(self, buffer):
            count = self.source.readinto(buffer)
            if count:
                activity()
            return count

        def close(self):
            if not self.closed:
                try:
                    self.source.close()
                finally:
                    super().close()

    class ResponseSocket:
        def __init__(self, sock):
            self.sock = sock

        def makefile(self, mode):
            return BufferedReader(Reader(self.sock.makefile(mode, buffering=0)))

    class Response(HTTPResponse):
        def __init__(self, sock, *args, **kwargs):
            super().__init__(ResponseSocket(sock), *args, **kwargs)

    class HTTP(HTTPConnection):
        response_class = Response

    class HTTPS(HTTPSConnection):
        response_class = Response

    class HTTPBytes(HTTPHandler):
        def http_open(self, request):
            return self.do_open(HTTP, request)

    class HTTPSBytes(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(HTTPS, request)

    return HTTPBytes(), HTTPSBytes()


def open_peer(url, trusted, token, *, activity=None, data=None, timeout=17):
    handlers = _activity_handlers(activity) if activity is not None else ()
    from hermes_cli.urllib_security import open_credentialed_url

    check(url, trusted)
    headers = {"A2A-Version": "1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers.update({"Content-Type": "application/json", "Accept": "text/event-stream"})
    request = Request(url, data=data, headers=headers)
    return open_credentialed_url(
        request, timeout=timeout,
        opener_factory=lambda _: build_opener(ProxyHandler({}), SameOriginRedirect(trusted), *handlers),
    )


def redact(text, token):
    return text.replace(token, "[REDACTED]") if token else text
