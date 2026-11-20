"""Application entrypoint: assemble routers and serve the H5 single-page app."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core import APP_NAME, APP_VERSION, WEB_DIR
from .routers import admin, auth, contracts, system, workbench

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# `/api/contracts/archived` is declared before `/api/contracts/{contract_no}`
# inside the contracts router; keep this include order stable.
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(workbench.router)
app.include_router(contracts.router)

# Static H5 last so /api routes win.
if (WEB_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    # Android WebView caches the shell; always serve the latest file from disk.
    return FileResponse(WEB_DIR / "index.html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                 "Pragma": "no-cache", "Expires": "0"})
