import os

import uvicorn

from services.mcp_server.server import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8765")),
        log_level="warning",
        proxy_headers=True,
    )
