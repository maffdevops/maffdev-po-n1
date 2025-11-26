import logging

import uvicorn
from fastapi import FastAPI

from app.postback.api import router as postback_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Pocket SaaS Postback")
app.include_router(postback_router)


if __name__ == "__main__":
    uvicorn.run(
        "run_postback:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )