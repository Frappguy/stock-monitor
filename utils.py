"""Shared helpers used by webui.py and monitor.py."""

_HEADER_REPLACEMENTS = {
    "\u2014": "-",   # em dash
    "\u2013": "-",   # en dash
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote / apostrophe
    "\u201C": '"',   # left double quote
    "\u201D": '"',   # right double quote
    "\u2026": "...", # ellipsis
}


def ascii_header(s):
    """Make a string safe for use as an HTTP/1.1 header value.

    HTTP/1.1 limits header values to ISO-8859-1 (latin-1). Python's http.client
    will raise UnicodeEncodeError if a header value contains a character outside
    that range. ntfy reads request bodies as UTF-8, so this sanitizer is only
    needed for header-borne fields like Title and Click.
    """
    if not s:
        return ""
    for k, v in _HEADER_REPLACEMENTS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


def display_label(product, default="sold out"):
    """Return the badge text shown on the UI when a product is in the sold-out state.

    Behaviour:
      - If the product has no ``status_label`` configured, return ``default``.
      - If the configured label is at most 20 characters, return it verbatim
        (preserving the user's exact capitalization).
      - Otherwise return the literal string "Custom" so the badge stays compact.
    """
    raw = (product.get("status_label") or "").strip()
    if not raw:
        return default
    return raw if len(raw) <= 20 else "Custom"
