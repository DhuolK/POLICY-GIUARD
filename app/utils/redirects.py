"""Same-origin redirect helper.

``request.referrer`` is fully attacker-controlled: any third-party page can
POST to us and set the Referer header, so ``redirect(request.referrer or ...)``
turns every one of those handlers into an open redirect (CWE-601) usable for
phishing from a domain the user trusts.

``safe_redirect`` only honours a Referer that points back at our own host
(``request.host`` is the proxy-corrected value once ProxyFix is installed) and
otherwise falls back to the caller-supplied in-app URL.
"""

from urllib.parse import urlparse


def same_origin_referrer():
    """Return the Referer when it is same-origin, else None."""
    from flask import request

    target = (request.referrer or '').strip()
    if not target:
        return None

    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None  # Relative, javascript:, data: — not trustworthy targets.
    if parsed.netloc.lower() != (request.host or '').lower():
        return None

    return target


def safe_redirect(fallback_url, **kwargs):
    """Redirect to the same-origin Referer if present, else to `fallback_url`."""
    from flask import redirect

    return redirect(same_origin_referrer() or fallback_url, **kwargs)
