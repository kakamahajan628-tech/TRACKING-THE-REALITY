import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dotenv import load_dotenv
from .config import Settings
from .scanner import Scanner
from .telegram import Telegram
from .control import Controller
from .reports import html_report, json_text, csv_report, report_bundle, markets_csv, technical_csv

load_dotenv()
settings=Settings.env()
if os.getenv('RENDER')=='true' and not settings.dashboard_token:
    raise RuntimeError('Set DASHBOARD_TOKEN before deploying the private dashboard on Render')
scanner=Scanner(settings)
controller=Controller(settings,scanner)
telegram=Telegram(settings.telegram_token,settings.telegram_chat,scanner,controller)
logger=logging.getLogger('atlas')
# httpx INFO logs contain Telegram request URLs. Keep all network library logs quiet.
logging.getLogger('httpx').setLevel(logging.CRITICAL)
logging.getLogger('httpcore').setLevel(logging.CRITICAL)


async def scheduled_scans():
    await controller.run(telegram.publish)


@asynccontextmanager
async def lifespan(app):
    await controller.initialize()
    tasks=[asyncio.create_task(scheduled_scans())]
    if settings.telegram_token and not settings.demo:tasks.append(asyncio.create_task(telegram.poll()))
    yield
    for task in tasks:task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    await telegram.close();await scanner.close();await controller.close()


app=FastAPI(title='Atlas 136',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
security=HTTPBasic(auto_error=False)


def authorize(credentials:HTTPBasicCredentials|None=Depends(security)):
    if not settings.dashboard_token:return
    if credentials and hmac.compare_digest(credentials.username,'atlas') and hmac.compare_digest(credentials.password,settings.dashboard_token):return
    raise HTTPException(status_code=401,detail='Private research dashboard',headers={'WWW-Authenticate':'Basic realm="Atlas 136"'})


@app.middleware('http')
async def headers(request:Request,call_next):
    response=await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store',
                             'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
    return response


@app.get('/healthz')
async def health():
    # Liveness is public; it does not leak the watchlist, tokens or report contents.
    return {'status':'running','service':'atlas-136'}


@app.get('/readyz',dependencies=[Depends(authorize)])
async def ready():
    report=scanner.latest
    recent=report and time.time()-datetime.fromisoformat(report['generated_at']).timestamp()<settings.interval*2
    fresh=report and any(c['coverage'].get('ok',0)>0 for c in report['coins'])
    matching=report and report.get('control_revision')==controller.state.revision
    payload={'ready':bool(recent and fresh and controller.ready and matching),'paused':controller.state.paused,'last_report':report['generated_at'] if report else None,'scan_in_progress':scanner.lock.locked(),
             'last_error':scanner.last_error,'telegram':telegram.status,'demo':settings.demo}
    return JSONResponse(payload,status_code=200 if payload['ready'] else 503)


@app.get('/',response_class=HTMLResponse,dependencies=[Depends(authorize)])
async def dashboard():return HTMLResponse(html_report(scanner.latest,live=True))


def latest():
    if not scanner.latest:raise HTTPException(503,'First scan is still collecting')
    return scanner.latest


@app.get('/api/report',dependencies=[Depends(authorize)])
async def report_json(request:Request):
    report=latest();etag='"'+report['generated_at']+'"'
    if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers={'ETag':etag})
    return Response(json_text(report),media_type='application/json',headers={'ETag':etag})


@app.get('/api/control',dependencies=[Depends(authorize)])
async def control_state():return controller.snapshot()


@app.get('/reports/latest.zip',dependencies=[Depends(authorize)])
async def download():
    report=latest()
    data=await asyncio.to_thread(report_bundle,report)
    return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="atlas-136-{report["scan_id"]}.zip"'})


@app.get('/reports/latest.html',response_class=HTMLResponse,dependencies=[Depends(authorize)])
async def html():return HTMLResponse(html_report(latest()))


@app.get('/reports/latest.csv',dependencies=[Depends(authorize)])
async def csv():return Response(csv_report(latest()),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="metrics.csv"'})


@app.get('/reports/markets.csv',dependencies=[Depends(authorize)])
async def market_csv():return Response(markets_csv(latest()),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="markets.csv"'})


@app.get('/reports/technical.csv',dependencies=[Depends(authorize)])
async def technical_download():return Response(technical_csv(latest()),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="technical.csv"'})
