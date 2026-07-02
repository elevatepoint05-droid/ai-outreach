"""
config.py
=========
Satu-satunya tempat baca .env dan set default value.
Semua agent import konstanta dari sini — tidak perlu os.getenv() nyebar di
mana-mana dan tidak ada magic string duplikat.

Cara pakai:
    from agents.config import GROQ_API_KEY, GROQ_MAX_CALLS, KOTA_TARGET
    # atau standalone:
    from config import GROQ_API_KEY, GROQ_MAX_CALLS

Untuk mengubah config tanpa edit kode: isi .env (lihat .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Groq API ──────────────────────────────────────────────────────────────────
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = "llama-3.1-8b-instant"
GROQ_MAX_CALLS = int(os.getenv("GROQ_MAX_CALLS_PER_RUN", "40"))

# ── Portfolio ─────────────────────────────────────────────────────────────────
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "").strip()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# ── Target outreach ───────────────────────────────────────────────────────────
# KOTA_TARGET di .env diisi koma-separated: "Berau,Bulungan,Malinau"
KOTA_TARGET = [
    k.strip()
    for k in os.getenv("KOTA_TARGET", "Berau").split(",")
    if k.strip()
]
DEFAULT_KATEGORI = os.getenv("DEFAULT_KATEGORI", "klinik swasta").strip()

# ── Behavior ──────────────────────────────────────────────────────────────────
# Hari sebelum lead "sent" dianggap perlu di-follow-up
HARI_BATAS_FOLLOWUP = int(os.getenv("HARI_BATAS_FOLLOWUP", "3"))

# Threshold kemiripan nama untuk fuzzy dedup (0.0–1.0)
FUZZY_THRESHOLD = float(os.getenv("FUZZY_THRESHOLD", "0.80"))

# Mode draft default (True = semua pesan masuk draft dulu)
DRAFT_MODE_DEFAULT = os.getenv("DRAFT_MODE_DEFAULT", "false").lower() == "true"

# ── Self-critique pesan (#18) ─────────────────────────────────────────────────
# Aktifkan dengan SELF_CRITIQUE_ENABLED=true di .env
# Menambah 1 Groq API call per pesan — matikan kalau kuota mepet
SELF_CRITIQUE_ENABLED  = os.getenv("SELF_CRITIQUE_ENABLED", "false").lower() == "true"
SELF_CRITIQUE_MIN_SKOR = int(os.getenv("SELF_CRITIQUE_MIN_SKOR", "7"))   # 1-10
SELF_CRITIQUE_MAX_RETRY = int(os.getenv("SELF_CRITIQUE_MAX_RETRY", "2"))  # maks regenerasi

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Server ────────────────────────────────────────────────────────────────────
PORT_DASHBOARD = int(os.getenv("PORT_DASHBOARD", "8000"))

# ── Orchestrator (decision loop otonom) ────────────────────────────────────────
ORCHESTRATOR_ENABLED       = os.getenv("ORCHESTRATOR_ENABLED", "false").lower() == "true"
ORCHESTRATOR_CEK_INTERVAL  = int(os.getenv("ORCHESTRATOR_CEK_INTERVAL", "1800"))
ORCHESTRATOR_JAM_BUILD     = os.getenv("ORCHESTRATOR_JAM_BUILD", "06:00").strip()
