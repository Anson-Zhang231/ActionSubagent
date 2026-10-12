from __future__ import annotations

from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def open_no_redirect(request: Request, *, timeout: float) -> Any:
    """Keep application credentials and request bodies on the configured origin."""
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)
