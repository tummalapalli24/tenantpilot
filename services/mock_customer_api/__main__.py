import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "services.mock_customer_api.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8081")),
        log_level="warning",
    )
