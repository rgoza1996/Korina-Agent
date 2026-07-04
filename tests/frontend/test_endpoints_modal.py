"""Contracts for the Endpoints modal.

The modal (#endpointsModal) is opened from a new "Endpoints" button
next to the existing "Settings" button. It shows every URL Korina has
wired, each as a clickable <a target="_blank"> link, so the user can
visit endpoints in a new tab without navigating away from the running
session.

Enforced contracts:
1. #endpointsBtn sits next to #settingsBtn in the header row.
2. #endpointsModal markup is present (backdrop, title, close button,
   list container, refresh button).
3. endpoints.js exports openEndpoints, closeEndpoints, populateEndpoints.
4. populateEndpoints fetches /api/health (not /api/capabilities).
5. populateEndpoints renders every link with target="_blank".
6. app.js binds the Endpoints button + close + refresh.
7. ESC and backdrop-click close the Endpoints modal without affecting
   the Settings modal.
8. Every advertised route matches an actual @router.* route.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "Korina" / "index.html"
APP_JS = REPO / "Korina" / "js" / "app.js"
ENDPOINTS_JS = REPO / "Korina" / "js" / "endpoints.js"
STYLES_CSS = REPO / "Korina" / "styles.css"


def test_index_has_endpoints_button_next_to_settings():
    src = INDEX.read_text()
    assert 'id="endpointsBtn"' in src, "missing #endpointsBtn"
    cs = src.find('id="clearSessionBtn"')
    ep = src.find('id="endpointsBtn"')
    st = src.find('id="settingsBtn"')
    assert cs < ep < st, \
        f"#endpointsBtn must sit between #clearSessionBtn and #settingsBtn (got cs={cs}, ep={ep}, st={st})"


def test_index_has_endpoints_modal_markup():
    src = INDEX.read_text()
    for needle in (
        'id="endpointsModal"',
        'id="endpointsList"',
        'id="closeEndpointsBtn"',
        'id="refreshEndpointsBtn"',
        'aria-labelledby="endpointsTitle"',
    ):
        assert needle in src, f"missing {needle!r} in index.html"


def test_endpoints_js_exports():
    src = ENDPOINTS_JS.read_text()
    assert 'export function openEndpoints' in src, "missing export openEndpoints"
    assert 'export function closeEndpoints' in src, "missing export closeEndpoints"
    assert 'export async function populateEndpoints' in src or \
           'export function populateEndpoints' in src, \
        "missing export populateEndpoints"


def test_populate_endpoints_reads_health_not_capabilities():
    src = ENDPOINTS_JS.read_text()
    assert "fetch('/api/health')" in src or 'fetch("/api/health")' in src, \
        "populateEndpoints must fetch /api/health"
    fetch_calls = src.count('fetch(')
    assert fetch_calls == 1, f"populateEndpoints should make exactly one fetch (got {fetch_calls})"


def test_all_rendered_links_use_target_blank():
    src = ENDPOINTS_JS.read_text()
    assert 'target="_blank"' in src, \
        "_rowHtml must render links with target=\"_blank\""


def test_app_js_imports_endpoints():
    src = APP_JS.read_text()
    assert 'import { openEndpoints, closeEndpoints, populateEndpoints }' in src, \
        "app.js must import endpoints module symbols"
    assert 'from "./endpoints.js"' in src, "import path must be ./endpoints.js"


def test_app_js_wires_button_click():
    src = APP_JS.read_text()
    assert "bindClick('endpointsBtn', openEndpoints)" in src, \
        "wireUiHandlers must bind endpointsBtn -> openEndpoints"
    assert "bindClick('closeEndpointsBtn', closeEndpoints)" in src, \
        "wireUiHandlers must bind closeEndpointsBtn -> closeEndpoints"
    assert "bindClick('refreshEndpointsBtn'" in src, \
        "wireUiHandlers must bind refreshEndpointsBtn -> populateEndpoints"


def test_app_js_handles_backdrop_click_and_escape():
    src = APP_JS.read_text()
    assert "if (e.target === epModal) closeEndpoints()" in src, \
        "backdrop click must close endpointsModal"
    assert "endpointsModal')?.classList.contains('open')" in src, \
        "ESC handler must check endpointsModal open state"


def test_styles_include_endpoints_classes():
    src = STYLES_CSS.read_text()
    for cls in ('.endpointsBtn', '.endpointsModal', '.endpointsList',
                '.endpointsGroup', '.endpointsGroupHead', '.endpointsGroupBody',
                '.endpointsRow', '.endpointsRow .link'):
        assert cls in src, f"missing CSS class {cls!r} in styles.css"


def test_endpoints_page_server_endpoints_match_real_routes():
    """Every path in PAGE_SERVER_ENDPOINTS must correspond to an actual
    @router.* route in korina/routes/*.py (single or double quoted)."""
    src = ENDPOINTS_JS.read_text()
    routes_dir = REPO / "korina" / "routes"
    declared = set()
    route_re = re.compile(r'@router\.(get|post|delete|put|patch)\(\s*["\']([^"\']+)["\']')
    for f in routes_dir.glob("*.py"):
        for line in f.read_text().splitlines():
            for m in route_re.finditer(line):
                declared.add(m.group(2))
    # FastAPI built-in routes
    declared |= {"/openapi.json", "/docs", "/"}
    # endpoints.js path constants
    paths = set(re.findall(r"path:\s*['\"]([^'\"]+)['\"]", src))
    missing = paths - declared
    assert not missing, \
        f"endpoints.js advertises routes that are not declared in korina/routes/: {sorted(missing)}\n" \
        f"(advertised: {sorted(paths)})"