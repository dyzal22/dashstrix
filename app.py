import os
import uvicorn
from strix.interface.web import app

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0"
    uvicorn.run(app, host=host, port=port)
