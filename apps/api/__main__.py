import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "apps.api.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_config=None,  # our JSON logging (packages/core/logging.py) owns the root logger
        access_log=False,  # RequestContextMiddleware writes structured access logs
        proxy_headers=True,
        forwarded_allow_ips="*",
        reload=os.environ.get("RELOAD") == "1",
    )
