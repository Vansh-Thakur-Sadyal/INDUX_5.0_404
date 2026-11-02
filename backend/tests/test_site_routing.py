"""Two static sites on one app: the public page at /, the dashboard at /app.

The order these are mounted in is load-bearing. A mount at "/" answers everything
beneath it, so if the landing site were mounted first it would swallow /app and serve
its own 404 - which is what a visitor would see after pressing the button on the
landing page.
"""
from __future__ import annotations

from app.core.config import AGENCY_DIR, FRONTEND_DIR, LANDING_DIR


def test_the_public_site_is_what_a_visitor_lands_on(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    if LANDING_DIR.exists():
        assert "Saarthi" in response.text


def test_the_dashboard_lives_under_app(client):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "Saarthi 2.0" in response.text
    assert 'id="nav"' in response.text           # the dashboard shell, not the site


def test_bare_app_redirects_rather_than_404s(client):
    """The landing page's own button points at /app, with no trailing slash."""
    response = client.get("/app", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"].endswith("/app/")
    assert client.get("/app").status_code == 200


def test_the_dashboards_own_files_are_served_under_app(client):
    for path in ("/app/app.js", "/app/styles.css"):
        assert client.get(path).status_code == 200, path


def test_the_api_still_outranks_both_sites(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_the_sites_are_not_the_same_directory():
    assert FRONTEND_DIR != LANDING_DIR


# ------------------------------------------- a hostname of its own for the dashboard
def test_without_the_setting_one_host_serves_both(client):
    """The default: the site at /, the dashboard at /app, on a single name."""
    assert "Saarthi 2.0" not in client.get("/").text
    assert "Saarthi 2.0" in client.get("/app/").text


def _hosted_client(host: str):
    """A client for an app whose AGENCY hostname is configured."""
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    import importlib
    from app import main as main_module

    get_settings().values["SAARTHI_AGENCY_HOST"] = host
    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_the_agency_host_serves_the_agency_site_at_its_root(client):
    """team.visioncoders.in is VisionCoders; everything else is Saarthi."""
    with _hosted_client("team.test") as hosted:
        root = hosted.get("/", headers={"Host": "team.test"})
        assert root.status_code == 200
        assert "VisionCoders" in root.text
        assert "Saarthi 2.0 - AI Farming Companion" not in root.text

        # its own pages and assets resolve from that root
        for path in ("/team.html", "/contact.html"):
            assert hosted.get(path, headers={"Host": "team.test"}).status_code == 200, path


def test_saarthi_keeps_its_site_sign_in_and_dashboard(client):
    """The product hostname carries all three, which is the arrangement asked for."""
    with _hosted_client("team.test") as hosted:
        host = {"Host": "saarthi2.0.test"}
        landing = hosted.get("/", headers=host)
        assert landing.status_code == 200
        assert "Saarthi" in landing.text
        assert "VisionCoders — software that ships" not in landing.text

        assert 'id="authForm"' in hosted.get("/sign-in.html", headers=host).text
        assert 'id="nav"' in hosted.get("/app/", headers=host).text


def test_the_api_answers_on_the_agency_host_too(client):
    """Prefixing /api would take /api/health to /vc/api/health and 404 it."""
    with _hosted_client("team.test") as hosted:
        for host in ("team.test", "saarthi2.0.test"):
            body = hosted.get("/api/health", headers={"Host": host})
            assert body.status_code == 200, host
            assert body.json()["status"] == "ok"


def test_a_port_on_the_host_header_still_matches(client):
    """Browsers send "host:8010" in development; the port is not part of the name."""
    with _hosted_client("team.test") as hosted:
        assert "VisionCoders" in hosted.get("/", headers={"Host": "team.test:8010"}).text


def test_the_agency_site_is_also_reachable_by_path(client):
    """So it can be checked without faking a Host header."""
    if AGENCY_DIR.exists():
        assert "VisionCoders" in client.get("/vc/").text
