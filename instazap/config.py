import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data: Path = Path(os.getenv('INSTAZAP_DATA', './data'))
    max_bytes: int = 200_000_000
    ttl: int = 86400
    max_jobs: int = 8
    disk_budget: int = 3_000_000_000
    access_secret: str = os.getenv('INSTAZAP_ACCESS_SECRET', '')
    secure_cookie: bool = os.getenv('INSTAZAP_SECURE_COOKIE', '0') == '1'
    public_origin: str = os.getenv('INSTAZAP_ORIGIN', '')
    worker: bool = True
