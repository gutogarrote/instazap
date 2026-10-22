import os
import re
from dataclasses import dataclass, field
from http.cookies import CookieError, Morsel
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
    cookie_name: str = field(default_factory=lambda: os.getenv('INSTAZAP_COOKIE_NAME', 'instazap_session'))

    def __post_init__(self):
        # RFC cookie-name token; SimpleCookie also reserves attribute names.
        if not isinstance(self.cookie_name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self.cookie_name):
            raise ValueError('INSTAZAP_COOKIE_NAME deve ser um nome de cookie válido (token ASCII sem separadores).')
        try:
            Morsel().set(self.cookie_name, '', '')
        except CookieError as exc:
            raise ValueError('INSTAZAP_COOKIE_NAME não pode ser um atributo reservado de cookie.') from exc
