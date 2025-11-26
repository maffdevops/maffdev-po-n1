from fastapi import FastAPI

app = FastAPI(title="Pocket SaaS Postbacks (skeleton)")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
