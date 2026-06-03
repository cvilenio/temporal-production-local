from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..scenarios import SCENARIOS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/tracking", response_class=HTMLResponse)
async def tracking_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="tracking.html", context={"active": "tracking"}
    )


@router.get("/architecture", response_class=HTMLResponse)
async def architecture_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="architecture.html", context={"active": "architecture"}
    )
