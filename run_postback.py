import logging

from fastapi import FastAPI
import uvicorn

from app.postback.api import router as postback_router

logger = logging.getLogger("pocket_saas.postback_main")

app = FastAPI()
app.include_router(postback_router)


if __name__ == "__main__":
    # Запускаем uvicorn на 0.0.0.0:8000 без reload (под systemd он не нужен)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )