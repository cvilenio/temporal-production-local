from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..scenarios import SCENARIOS
from ..settings import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Nav rail needs these on every page (base.html). The Temporal icon links to the
# local Web UI iframe in OSS mode, or out to the hosted Cloud console in Cloud
# mode (which can't be framed). Exposed as Jinja globals so each route doesn't
# have to thread them through its context.
templates.env.globals["temporal_embed_url"] = settings.temporal_ui_embed_url
templates.env.globals["temporal_cloud_url"] = settings.temporal_cloud_url


@router.get("/", response_class=RedirectResponse)
async def index():
    return RedirectResponse(url="/orders", status_code=302)


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={"scenarios": SCENARIOS, "active": "orders"},
    )


@router.get("/domain-trigger", response_class=HTMLResponse)
async def domain_trigger_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="domain_trigger.html",
        context={"active": "domain-trigger"},
    )


@router.get("/tracking", response_class=HTMLResponse)
async def tracking_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="tracking.html", context={"active": "tracking"}
    )


@router.get("/architecture", response_class=HTMLResponse)
async def architecture_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="architecture.html",
        context={"active": "architecture", "backend": settings.console_backend},
    )


# Embedded tool UIs — each rendered as a full-bleed iframe inside the console.
def _embed_page(request: Request, *, active: str, title: str, embed_url: str):
    return templates.TemplateResponse(
        request=request,
        name="embed.html",
        context={"active": active, "title": title, "embed_url": embed_url},
    )


@router.get("/temporal-ui", response_class=HTMLResponse)
async def temporal_ui_page(request: Request):
    return _embed_page(
        request,
        active="temporal-ui",
        title="Temporal Web UI",
        embed_url=settings.temporal_ui_embed_url,
    )


@router.get("/grafana", response_class=HTMLResponse)
async def grafana_page(request: Request):
    return _embed_page(
        request,
        active="grafana",
        title="Grafana",
        embed_url=settings.grafana_embed_url,
    )


@router.get("/pgweb", response_class=HTMLResponse)
async def pgweb_page(request: Request):
    return _embed_page(
        request,
        active="pgweb",
        title="pgweb — Orders DB",
        embed_url=settings.pgweb_embed_url,
    )


@router.get("/headlamp", response_class=HTMLResponse)
async def headlamp_page(request: Request):
    return _embed_page(
        request,
        active="headlamp",
        title="Headlamp — Cluster Explorer",
        embed_url=settings.headlamp_embed_url,
    )


@router.get("/argocd", response_class=HTMLResponse)
async def argocd_page(request: Request):
    return _embed_page(
        request,
        active="argocd",
        title="ArgoCD",
        embed_url=settings.argocd_embed_url,
    )
