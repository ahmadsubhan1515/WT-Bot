import os

import uvicorn

from app.web.app import create_app

app = create_app()

if __name__ == "__main__":
    from app.config import get_settings

    s = get_settings()
    port = int(os.environ.get("PORT", str(s.port)))
    uvicorn.run(app, host=s.host, port=port)
