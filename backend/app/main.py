"""Saarthi 2.0 - FastAPI application.

Serves the API and the dashboard from one origin, so the front end never needs a
hardcoded backend URL (a mistake that broke the earlier project in every browser
but the developer's).

Run it:
    python -m uvicorn app.main:app --reload --port 8010      (from backend/)
    run.bat                                                   (from the project root)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .agents import registry
from .core import accounts, commands, storage, voiceprint
from .core.config import (AGENCY_DIR, CONSOLE_DIR, FRONTEND_DIR, LANDING_DIR,
                          ensure_dirs, get_settings)
from .core.schemas import FarmContext
from .services import soilgrids as soil_service
from .services import weather as weather_service
from .rag import documents as kb_documents
from .rag import pipeline
from .routers import (appearance, assistant, auth, calls, chat, enrol, geo, insights,
                      kb, knowledge, profile, sensors, speech, system, vision, voice,
                      voiceid)
from .routers import pipeline as pipeline_router   # not rag.pipeline above
from .routers import support as support_router
from .routers import ledger as ledger_router
from .routers import i18n as i18n_router
from .routers import tasks as tasks_router
from .telephony import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
# httpx logs the full request URL at INFO, and our Agmarknet calls carry the
# data.gov.in key in the query string - so every market lookup was writing a live
# credential into the container log, where it persists in `docker logs`. Its request
# lines add nothing we do not already log ourselves, with the source and the outcome
# and without the key.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger("saarthi")


async def _warm_caches() -> None:
    """Pre-fetch the slow services, and open the model connection, in the background.

    The first request of a process pays for a TLS handshake to api.mistral.ai -
    a measured 265 ms on top of an answer that is already the slowest thing on the
    page. Doing it at boot means the first farmer does not.
    """
    from .llm import mistral_client
    from .llm import pool as llm_pool
    if get_settings().has("MISTRAL_API_KEY"):
        opened = await llm_pool.warm(mistral_client.CHAT_URL)
        log.info("Model connection: %s", "open" if opened else "will open on first use")

    # The same, for speech. A spoken turn makes two Azure requests - identify
    # what was said, then say the answer - and both were opening their own
    # connection to eastus. They share the model pool now, so the handshake is
    # paid once here instead of twice per turn.
    from .speech import azure_speech
    if azure_speech.configured():
        spoke = await llm_pool.warm(azure_speech._endpoint("tts"))
        log.info("Speech connection: %s", "open" if spoke else "will open on first use")

    farm = FarmContext()
    try:
        weather, soil = await asyncio.gather(
            weather_service.get_summary(farm.latitude, farm.longitude),
            soil_service.get_summary(farm.latitude, farm.longitude),
            return_exceptions=True,
        )
        log.info("Cache warm-up: weather %s, SoilGrids %s",
                 "ok" if isinstance(weather, dict) and weather.get("available") else "unavailable",
                 "ok" if isinstance(soil, dict) and soil.get("available") else "unavailable")
    except asyncio.CancelledError:                                 # shutdown during warm-up
        raise
    except Exception as exc:                                       # noqa: BLE001
        log.info("Cache warm-up skipped: %s", exc)
    await _warm_greetings()


async def _warm_greetings() -> None:
    """Synthesise the fixed opening lines now, so no caller waits for them.

    The greeting goes out before the farmer has said a word, so its synthesis time is
    silence they just have to sit through - about a second even for a short line. The
    text never varies for a new caller, so doing it once at boot takes it off the
    critical path of every first call.
    """
    from .routers.voice import voice_agent_greeting
    from .speech import voice_io
    if voice_io.provider_name() == "none":
        return
    # Every language, not just two. A farmer who books a call in Odia waits for
    # the greeting to be synthesised on the call itself otherwise - and that
    # wait is silence on an open line. This runs in the background at boot.
    from .core import languages as language_registry
    for language in [l.code for l in language_registry.LANGUAGES]:
        try:
            spoken = await voice_io.synthesize(
                await voice_agent_greeting(None, language), language)
            log.info("Greeting audio (%s): %s", language,
                     "ready" if spoken.ok else "unavailable - " + spoken.reason[:80])
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            log.info("Greeting warm-up skipped (%s): %s", language, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    if not storage.is_open():          # tests open their own temp database
        storage.init()
    registry.assert_fifteen()
    settings = get_settings()

    # Accounts, and the first administrator if one was configured and none exists.
    accounts.ensure_schema()
    admin = accounts.ensure_admin(settings.get("SAARTHI_ADMIN_EMAIL", ""),
                                  settings.get("SAARTHI_ADMIN_PHONE", ""),
                                  settings.get("SAARTHI_ADMIN_PASSWORD", ""))
    if admin:
        log.info("Administrator account ready: %s", admin.get("email"))
    elif not accounts.count_admins():
        log.warning("No administrator account exists. Set SAARTHI_ADMIN_EMAIL, "
                    "SAARTHI_ADMIN_PHONE and SAARTHI_ADMIN_PASSWORD in "
                    "config/api_keys.env, then restart.")
    # The knowledge console's own tables, and a tidy-up: a job that was mid-flight
    # when the process died would otherwise sit on "embedding" for ever, which looks
    # exactly like a system still working rather than one that needs a retry.
    kb_documents.ensure_schema()
    # The queue a spoken instruction waits in until a screen picks it up.
    commands.ensure_schema()
    # Voiceprints, and the consent that has to exist before one does.
    voiceprint.ensure_schema()
    abandoned = kb_documents.abandon_running_jobs()
    if abandoned:
        log.info("Marked %d interrupted ingestion job(s) as failed.", abandoned)

    stats = await pipeline.ensure_index()
    log.info("Knowledge base: %s chunks from %s documents (embeddings: %s)",
             stats.get("chunks"), stats.get("documents"), stats.get("provider"))
    for item in settings.capability_report():
        log.info("%-22s %s", item["name"], "configured" if item["configured"]
                 else "NOT configured - " + item["without_it"])
    log.info("Dashboard: http://%s:%s/", settings.get("SAARTHI_HOST", "127.0.0.1"),
             settings.get_int("SAARTHI_PORT", 8010))

    # Warm the slow external caches in the background. SoilGrids takes ~20 s cold,
    # which otherwise lands on whoever presses Run pipeline first.
    warm = asyncio.create_task(_warm_caches())
    # Scheduled call-backs are dialled by a sweeper rather than a timer, so a
    # restart during the wait does not silently drop someone's call.
    scheduler.start_sweeper()
    yield
    scheduler.stop_sweeper()
    warm.cancel()
    from .llm import pool as llm_pool
    await llm_pool.close()


app = FastAPI(
    title="Saarthi 2.0",
    version="2.0.0",
    description=("Regional-language AI farmer advisory: conversational AI, RAG, 15 "
                 "agricultural agents, seven-parameter soil sensor and feature-phone "
                 "access. InduX 5.0, Problem Statement 16, Team VisionCoders."),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # the Android companion app posts from the field
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (system, chat, speech, calls, sensors, insights, knowledge,
               vision, voice, profile, auth, enrol, pipeline_router,
               support_router, ledger_router, i18n_router, tasks_router, kb,
               voiceid, assistant, appearance, geo):
    app.include_router(module.router)
app.include_router(voice.farmer_router)
app.include_router(auth.admin_router)


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:      # pragma: no cover
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": "Internal error", "path": str(request.url.path)})


class HostPrefix:
    """Serve a second site at the root of its own hostname.

    Starlette matches mounts on path, not host, so a site can only live under a
    path prefix. For requests arriving on a configured hostname this quietly
    prefixes that path, which puts the site at the host root with its stylesheets
    and images still resolving - rather than redirecting a visitor to a URL with
    "/vc" or "/kb" in it.

    The API and the docs are left alone so they answer on every name; prefixing
    them would take /api/health to /kb/api/health and 404 it. That is also why
    this is done here rather than with a Traefik addPrefix, which cannot tell the
    two apart.
    """

    PASS_THROUGH = ("/api", "/docs", "/redoc", "/openapi.json")

    def __init__(self, app, host: str = "", prefix: str = "") -> None:
        self.app = app
        self.host = (host or "").strip().lower()
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.host:
            await self.app(scope, receive, send)
            return

        requested = ""
        for key, value in scope.get("headers") or []:
            if key == b"host":
                requested = value.decode("latin-1").split(":")[0].strip().lower()
                break

        path = scope.get("path", "/")
        if requested == self.host and not path.startswith(self.PASS_THROUGH):
            scope = dict(scope)
            scope["path"] = self.prefix + "/" if path == "/" else self.prefix + path
        await self.app(scope, receive, send)


class AgencyHost:
    """Serve the VisionCoders agency site at the root of its own hostname.

    One container, two public faces:

        team.visioncoders.in        the agency site        (this middleware)
        saarthi2.0.visioncoders.in  Saarthi - its public page at "/", its sign-in,
                                    and the dashboard at "/app"

    Starlette mounts match on path, not host, so the agency site can only live under
    "/vc". For requests arriving on the agency hostname this quietly prefixes "/vc" to
    the path, which puts that site at the host root with its stylesheets and images
    still resolving - rather than redirecting a visitor to a URL with "/vc" in it.

    The API and the docs are left alone so they answer on every name; prefixing them
    would take /api/health to /vc/api/health and 404 it. That is also why this is done
    here rather than with a Traefik addPrefix, which cannot tell the two apart.
    """

    PASS_THROUGH = ("/api", "/docs", "/redoc", "/openapi.json")

    def __init__(self, app, host: str = "") -> None:
        self.app = app
        self.host = (host or "").strip().lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.host:
            await self.app(scope, receive, send)
            return

        requested = ""
        for key, value in scope.get("headers") or []:
            if key == b"host":
                requested = value.decode("latin-1").split(":")[0].strip().lower()
                break

        path = scope.get("path", "/")
        if requested == self.host and not path.startswith(self.PASS_THROUGH):
            scope = dict(scope)
            scope["path"] = "/vc/" if path == "/" else "/vc" + path
        await self.app(scope, receive, send)


# Unset by default, which leaves every hostname serving Saarthi. Setting
# SAARTHI_AGENCY_HOST gives the VisionCoders site a hostname of its own, and
# SAARTHI_CONSOLE_HOST does the same for the knowledge console.
app.add_middleware(AgencyHost, host=get_settings().get("SAARTHI_AGENCY_HOST", ""))
app.add_middleware(HostPrefix, host=get_settings().get("SAARTHI_CONSOLE_HOST", ""),
                   prefix="/kb")


class RevalidatingStatics(StaticFiles):
    """Serve the dashboard with `no-cache` on html/css/js.

    Browsers otherwise keep a deployed-over stylesheet and script, which looks
    exactly like a broken release: after one deploy the cached CSS still lacked
    the rule that hides the "Running..." panel, so it stayed on screen forever.
    `no-cache` still allows a 304, so this costs a revalidation, not a download.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Match on the served content type, not the path: "/" resolves to index.html
        # internally and would otherwise keep being cached.
        content_type = response.headers.get("content-type", "")
        if content_type.startswith(("text/html", "text/css", "application/javascript",
                                    "text/javascript", "application/json")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Two static sites, mounted after every route so /api keeps precedence:
#
#   /app   the dashboard - the working tool
#   /      the public site - what a visitor sees first
#
# Order matters. A mount at "/" matches everything beneath it, so the dashboard has to
# be mounted FIRST or the landing site would swallow /app and serve its own 404.
if FRONTEND_DIR.exists():
    # A mount only answers paths BELOW it, so bare "/app" would fall through to the
    # landing mount and be served as its 404 - and "/app" is exactly what a person
    # types and what the site's own button links to.
    @app.get("/app", include_in_schema=False)
    async def dashboard_entry() -> RedirectResponse:
        return RedirectResponse("/app/", status_code=308)

    app.mount("/app", RevalidatingStatics(directory=str(FRONTEND_DIR), html=True),
              name="dashboard")

# The knowledge console: /kb on any hostname, and the root of its own when
# SAARTHI_CONSOLE_HOST is set. Its API is admin-only; this is only the page.
if CONSOLE_DIR.exists():
    @app.get("/kb", include_in_schema=False)
    async def console_entry() -> RedirectResponse:
        return RedirectResponse("/kb/", status_code=308)

    app.mount("/kb", RevalidatingStatics(directory=str(CONSOLE_DIR), html=True),
              name="console")

# The agency site, reachable at /vc on any hostname and at the root of its own.
if AGENCY_DIR.exists():
    app.mount("/vc", RevalidatingStatics(directory=str(AGENCY_DIR), html=True),
              name="agency")

if LANDING_DIR.exists():
    app.mount("/", RevalidatingStatics(directory=str(LANDING_DIR), html=True),
              name="landing")
elif FRONTEND_DIR.exists():       # no public site built: the dashboard is the whole app
    app.mount("/", RevalidatingStatics(directory=str(FRONTEND_DIR), html=True),
              name="dashboard-root")
else:                                                               # pragma: no cover
    @app.get("/")
    async def missing_frontend() -> Dict[str, Any]:
        return {"detail": "frontend/ not found", "api_docs": "/docs"}
