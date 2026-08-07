"""Launch Scrapling's MCP server with the Chrome-fingerprint bug worked around.

Why this file exists
--------------------
Scrapling 0.4.12 hardcodes the Chromium/Chrome version it asks browserforge to
build a fingerprint for::

    # scrapling/engines/toolbelt/fingerprints.py
    chromium_version = 149
    chrome_version   = 149

The header dataset that ships with `apify-fingerprint-datapoints` (0.14.0 is the
newest published release) only carries data up to **Chrome 143**. So the moment
anything imports a browser fetcher, `_config_tools.py` calls
`generate_headers(browser_mode=True)` — chrome-only, pinned at 149 — and
browserforge raises::

    ValueError: No headers based on this input can be generated.

That kills `scrapling mcp` at import time, before the server ever binds. It is
upstream issue #396 and there is no released fix and no newer dataset to install.

Rather than editing the installed package (which any `pip install -U scrapling`
would silently revert), we correct the two module constants here, before the
browser modules are first imported, and then hand off to Scrapling's own entry
point. Everything downstream reads these module globals at call time, so the
patch is picked up by every fetcher.

Remove this shim once upstream #396 is fixed and the pinned version is one the
installed dataset actually has.

Run directly:  python -m tools.scrapling_mcp
Registered in .mcp.json as the "scrapling" server.
"""

import sys

# The newest Chrome the installed browserforge dataset can generate headers for.
# Verified empirically against apify-fingerprint-datapoints 0.14.0: versions
# 120-143 generate, 144+ raise. Keep this <= the dataset's ceiling.
_MAX_SUPPORTED_CHROME = 143


def _patch_fingerprint_version():
    """Clamp Scrapling's hardcoded Chrome version to one the dataset supports."""
    import scrapling.engines.toolbelt.fingerprints as fp

    if fp.chromium_version > _MAX_SUPPORTED_CHROME:
        fp.chromium_version = _MAX_SUPPORTED_CHROME
    if fp.chrome_version > _MAX_SUPPORTED_CHROME:
        fp.chrome_version = _MAX_SUPPORTED_CHROME


def main(argv=None):
    _patch_fingerprint_version()

    # Imported only after the patch — importing this pulls in the browser
    # fetchers, which is exactly what fails on an unpatched install.
    from scrapling.core.ai import ScraplingMCPServer

    argv = list(sys.argv[1:] if argv is None else argv)
    http = "--http" in argv
    host = _opt(argv, "--host", "0.0.0.0")
    port = int(_opt(argv, "--port", "8000"))
    executable_path = _opt(argv, "--executable-path", None)
    auth_token = _opt(argv, "--auth-token", None)

    server = ScraplingMCPServer(executable_path=executable_path, auth_token=auth_token)
    server.serve(http, host, port)


def _opt(argv, flag, default):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


if __name__ == "__main__":
    main()
