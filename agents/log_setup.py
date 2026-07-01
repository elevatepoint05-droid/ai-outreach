"""
log_setup.py
============
Setup logging terpusat untuk semua agent.

- Console: tampilkan pesan apa adanya (format bersih, sama kayak print sebelumnya)
- File:    simpan ke data/outreach.log dengan timestamp + nama modul
           (berguna kalau main.py jalan via Task Scheduler tanpa terminal terbuka)

Cara pakai di tiap agent:
    from agents.log_setup import buat_logger
    log = buat_logger(__name__)
    log.info("Pesan info")
    log.warning("Pesan warning")
    log.error("Pesan error")

Level log bisa diatur lewat .env: LOG_LEVEL=DEBUG/INFO/WARNING (default INFO)
"""

import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "data" / "outreach.log"
try:
    from .config import LOG_LEVEL as _LOG_LEVEL_STR
except ImportError:
    from config import LOG_LEVEL as _LOG_LEVEL_STR
LOG_LEVEL = getattr(logging, _LOG_LEVEL_STR, logging.INFO)


def buat_logger(nama: str) -> logging.Logger:
    """
    Buat atau ambil logger bernama `nama`.
    Logger yang sama tidak diinisialisasi dua kali (idempotent).
    """
    logger = logging.getLogger(nama)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console handler — format bersih, tidak ada timestamp (sama kayak print lama)
    ch = logging.StreamHandler()
    ch.setLevel(LOG_LEVEL)
    ch.setFormatter(logging.Formatter("%(message)s"))

    # File handler — format lengkap dengan timestamp + level
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(fh)
    except OSError as e:
        # Kalau log file tidak bisa dibuat, lanjut tanpa file logging
        print(f"[log_setup] Peringatan: tidak bisa buat log file — {e}")

    logger.addHandler(ch)
    return logger
