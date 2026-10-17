import json
import logging

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Local Workflows substitute — demo only")
logger = logging.getLogger("uvicorn.error")


@app.post("/workflow")
async def receive(request: Request):
    payload = await request.json()
    logger.info("DEMO WORKFLOW RECEIVED %s", json.dumps(payload, ensure_ascii=False))
    return Response(status_code=202)
