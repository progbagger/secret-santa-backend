import sys
from datetime import datetime
from pathlib import Path

import uvicorn

from secret_santa_backend.token_issuer import create_app

if __name__ == "__main__":
    port = int(sys.argv[1])
    clock_file = Path(sys.argv[2])
    uvicorn.run(create_app(now=lambda: datetime.fromisoformat(clock_file.read_text())), host="127.0.0.1", port=port)
