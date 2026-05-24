"""로깅 셋업.

기존에는 `app/api/__init__.py` import 사이드이펙트로 핸들러가 붙었다.
이제 lifespan startup에서 명시적으로 1회만 호출한다.
"""
from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """`ediya` 네임스페이스 로거에 stream handler를 (재)부착하고 반환."""
    log = logging.getLogger("ediya")
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        log.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(name)s - %(levelname)s - %(message)s")
    )
    log.addHandler(handler)
    return log
