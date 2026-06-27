"""Loopback hostnames must be rewritten to the current page host so
clicking external endpoints works when Korina is browsed over Tailscale."""
from pathlib import Path
EP = Path("/home/roggoz/Korina-Agent/Korina/js/endpoints.js")
src = EP.read_text()


def test_has_externalize_helper():
    assert 'function _externalize' in src, "missing _externalize() helper"


def test_externalize_rewrites_loopback():
    i = src.find('function _externalize')
    assert i != -1
    j = src.find('\n}', i)
    body = src[i:j+2]
    # Match either literal "127.0.0.1" or regex-escaped form "127\\.0\\.0\\.1"
    assert ('127' in body) and ('localhost' in body) and ('location.hostname' in body), \
        f"_externalize must reference 127.0.0.1, localhost, and location.hostname (body={body!r})"
    # And actually perform a regex replace, not just a comment.
    assert '.replace(' in body, "_externalize must use String.replace"


def test_row_emits_externalized_urls():
    assert '_externalize(origin + ep.path)' in src, \
        "page-server row must wrap URL with _externalize()"
    assert '_rowHtml(item.key, _externalize(url)' in src, \
        "external-service row must wrap URL with _externalize()"