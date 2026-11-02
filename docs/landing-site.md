# The public site, and how it shares a container with the dashboard

Added 15 Oct 2026. Saarthi 2.0 now opens on a landing page instead of dropping a
visitor straight into the working dashboard.

```
/        the public site      landing/     (the Studiova-based Saarthi site)
/app     the dashboard        frontend/    (the 15-agent tool)
/api     unchanged, and outranks both
```

## Where it came from

Cloned from the Saarthi site already running on the VPS at `/opt/stacks/saarthi/site`
(served on `rtvi5789t5u8efk.microeagle.online`). **That original is untouched and still
running** - this is a copy, not a move.

The source keeps its pages in `site/html/` and its assets in `site/assets/`, so every
page refers to `../assets/...` and the site root is a meta-refresh stub that bounces to
`html/index.html`. Served that way, `/` would be a redirect hop. In `landing/` the pages
sit at the root beside `assets/`, with the references rewritten to match, so `/` *is*
the page.

Rebuild it with `scratchpad/build_landing.py` if the upstream site ever changes.

### What is not copied

| | why |
|---|---|
| `*.map` (57 files, 3 MB) | source maps for a vendored CSS bundle nobody debugs |
| `home-banner-video.mp4` (52 MB) | see below |
| `auth-bg.mp4` (32 MB) | see below |

The result is **18 MB in the repository** instead of 187 MB.

The two videos live in the build context on the VPS only. They were copied there once
from the old saarthi stack, and they survive every redeploy because `tar` only
overwrites the paths it actually contains - and the deploy tarball does not contain
them. They are baked into the image at build time like any other asset.

Without them the pages fall back to `auth-bg-poster.jpg`, which is what happens in local
development. That is a `poster` attribute added during the build, not something the
upstream template had.

## Links into the dashboard

The hero's arrow was a dead `javascript:void(0)`. It now opens the dashboard, as do the
header's Sign In and Sign Up buttons - there is no account system yet, so pointing them
at a form that posts nowhere would be worse than pointing them at the product.

**Bare `/app` needs its own route.** A Starlette mount only answers paths *below* it, so
`/app` with no trailing slash would fall through to the landing mount and be served as
*its* 404 - and `/app` is exactly what a person types and what the site's own button
links to. `main.py` registers a 308 to `/app/` before the mounts.

**Mount order is load-bearing.** A mount at `/` matches everything beneath it, so the
dashboard is mounted first. Reverse them and the landing site swallows `/app`.

## A hostname of its own

`SAARTHI_DASHBOARD_HOST` (in `config/api_keys.env`, unset by default) gives the
dashboard its own public name:

```
unset            one hostname: site at /, dashboard at /app
app.example.in   that hostname serves the DASHBOARD at its root;
                 every other hostname still gets the public site
```

The `DashboardHost` middleware prefixes `/app` to the path for requests arriving on that
name. Prefixing rather than redirecting is deliberate: the dashboard ends up at the
host's root with its stylesheet and script still resolving, instead of bouncing the
visitor to a URL with `/app` in it.

`/api`, `/docs`, `/redoc` and `/openapi.json` are passed through untouched, so they
answer on **both** names. Prefixing them would send `/api/health` to `/app/api/health`
and 404 it - which is also why this is done in the application and not with a Traefik
`addPrefix` middleware, which cannot tell the two apart.

The host header's port is stripped before matching, so `app.example.in:8010` in
development matches the same setting.

### Adding the names

`deploy/traefik-indux-saarthi-hosts.yml.template` is the Traefik route, with two
placeholders. In order:

1. create both A records -> `187.127.191.13`, TTL 300, and let them resolve
2. set `SAARTHI_DASHBOARD_HOST` in `config/api_keys.env` **in place** (single-FILE bind
   mount - see DEPLOY.md), then `docker compose restart`
3. copy the template to `/opt/stacks/traefik/dynamic/indux-saarthi.yml` without the
   `.template` suffix; Traefik reloads on its own

Keep the `indux5.0.visioncoders.in` router until nothing points at it: the n8n
conversation workflow calls back to `SAARTHI_PUBLIC_URL`, and Twilio fetches spoken
audio from whatever host that names.

## The copy (aligned 15 Oct 2026)

The page arrived describing the EARLIER agritech project: a 27-class scanner, a WhatsApp
bot, a Jetpack Compose Android app, a five-agent pipeline and Bhunaksha land records.
Saarthi 2.0 has none of those - it has fifteen agents, a 32-class model, a conversational
phone line and a RAG knowledge base. The page was overselling and underselling it at once.

`deploy/landing_copy.py` holds the new wording as (old, new) replacements rather than a
frozen copy of the page, so the markup stays upstream's and only the words are ours.
**Every replacement is asserted**: one that stops matching fails the build instead of
quietly leaving the old claim on a public page. That assertion has already earned its
keep twice - once when a replacement's own output collided with a later one, and once
when a stat's label changed but the figure above it did not.

Every figure on the page was read from the running system before it was written:

| claim | source |
|---|---|
| 15 agents | `/api/health` → `agents` |
| 13 languages | `/api/languages` |
| 32 classes, 13 crops | `/api/vision/status` |
| 39,531 photographs | `/api/vision/status` → `training_data.images` |
| 86.4% accurate, 75.1% macro | `/api/vision/status` |
| 90% threshold, right 96.1% of the time | `/api/vision/status` → `threshold_rationale` |

Also corrected: the six platform cards and their badges, the four services, the three
"principles", all five FAQ entries, the cards' links (they pointed at the old
deployment), the placeholder contact details, and the "built on public data" logo band -
Bhunaksha, ASTER and Postgres were removed from it, because this project does not use
Bhunaksha or ASTER at all and stores its data in SQLite rather than Postgres.

**Left alone:** the pricing tiers, including the Premium tier's reference to the Ankur
platform. That is a commercial decision rather than a factual claim about this codebase.

### The build is atomic

`build_landing.py` assembles into `landing.building/` and swaps it in only once every
step has succeeded. It used to delete the live directory first and validate afterwards,
so the first failing copy assertion left no site at all.
