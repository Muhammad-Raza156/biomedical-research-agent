"""FastAPI entrypoint (placeholder — replaced in plan 10)."""

from fastapi import FastAPI

app = FastAPI(title="Biomedical Research Agent API")


@app.get("/ping")
async def ping() -> dict[str, str]:
    """Liveness probe used while the real API is not built yet."""
    return {"status": "ok"}
