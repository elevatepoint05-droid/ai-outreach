"""
rate_guard.py
=============
Deteksi pola pengiriman WA yang beresiko bikin nomor lo kena restriksi/ban.
"""

from datetime import datetime, timedelta
from pathlib import Path

try:
    from . import db
    from .log_setup import buat_logger
except ImportError:
    import db
    from log_setup import buat_logger

log = buat_logger("rate_guard")

BASE_DIR   = Path(__file__).resolve().parent.parent
LOG_PATH   = BASE_DIR / "data" / "kirim_log.txt"

BATAS_PER_JAM  = 5
BATAS_PER_HARI = 20
BATAS_BAHAYA_JAM  = 10
BATAS_BAHAYA_HARI = 40


def catat_kirim(nomor_wa: str) -> None:
    """Catat timestamp pengiriman ke file log sederhana (append-only)."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    waktu = datetime.now().isoformat(timespec="seconds")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{waktu}|{nomor_wa}\n")


def _baca_log_kirim() -> list[datetime]:
    """Baca semua timestamp pengiriman dari log file."""
    if not LOG_PATH.exists():
        return []
    waktu_list = []
    for baris in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if "|" not in baris:
            continue
        waktu_str = baris.split("|")[0]
        try:
            waktu_list.append(datetime.fromisoformat(waktu_str))
        except Exception:
            continue
    return waktu_list


def cek_kecepatan_kirim() -> dict:
    """Cek kecepatan pengiriman WA saat ini, return level resiko + detail."""
    semua_waktu = _baca_log_kirim()
    sekarang = datetime.now()

    batas_jam  = sekarang - timedelta(hours=1)
    batas_hari = sekarang - timedelta(hours=24)

    per_jam  = sum(1 for w in semua_waktu if w >= batas_jam)
    per_hari = sum(1 for w in semua_waktu if w >= batas_hari)

    if per_jam >= BATAS_BAHAYA_JAM or per_hari >= BATAS_BAHAYA_HARI:
        level = "bahaya"
        pesan = (
            f"⚠️ BAHAYA: {per_jam} pesan/jam, {per_hari} pesan/24 jam. "
            f"Ini pola yang beresiko TINGGI kena restriksi WhatsApp. "
            f"Sangat disarankan STOP kirim dulu beberapa jam."
        )
    elif per_jam >= BATAS_PER_JAM or per_hari >= BATAS_PER_HARI:
        level = "waspada"
        pesan = (
            f"⚡ WASPADA: {per_jam} pesan/jam, {per_hari} pesan/24 jam. "
            f"Mendekati batas aman. Pertimbangkan kasih jeda sebelum lanjut kirim."
        )
    else:
        level = "aman"
        pesan = f"✅ Aman: {per_jam} pesan/jam, {per_hari} pesan/24 jam."

    return {
        "level": level,
        "jumlah_per_jam": per_jam,
        "jumlah_per_hari": per_hari,
        "pesan": pesan,
    }
