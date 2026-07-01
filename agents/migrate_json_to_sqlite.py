"""
migrate_json_to_sqlite.py
==========================
Migrasi satu kali: pindahkan isi data/leads.json dan data/sent.json
ke data/outreach.db (SQLite) lewat agents/db.py (#13).

leads.json dan sent.json TIDAK dihapus setelah migrasi — tetap disimpan
sebagai arsip/backward-compat, tapi setelah ini semua kode baca/tulis lewat
db.py, jadi perubahan berikutnya tidak lagi mengubah file JSON tersebut.

Aman dijalankan berkali-kali: script ini replace-all isi tabel dengan isi
JSON terbaru (bukan append), jadi tidak akan menghasilkan duplikat.

Cara pakai:
    python agents/migrate_json_to_sqlite.py
"""

import json
from pathlib import Path

try:
    from . import db
    from .log_setup import buat_logger
except ImportError:
    import db
    from log_setup import buat_logger

log = buat_logger("migrate_json_to_sqlite")

BASE_DIR = Path(__file__).resolve().parent.parent
LEADS_PATH = BASE_DIR / "data" / "leads.json"
SENT_PATH = BASE_DIR / "data" / "sent.json"


def _muat_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    leads = _muat_json(LEADS_PATH)
    sent = _muat_json(SENT_PATH)

    if not leads and not sent:
        log.warning(
            f"[migrate] {LEADS_PATH.name} dan {SENT_PATH.name} kosong/tidak ada — tidak ada yang dimigrasi."
        )
        return

    db.simpan_leads(leads)
    db.simpan_sent(sent)

    # Verifikasi: baca balik dari DB, pastikan jumlah baris cocok
    cek_leads = db.muat_leads()
    cek_sent = db.muat_sent()

    log.info(f"[migrate] leads.json  → tabel leads : {len(leads)} → {len(cek_leads)} baris")
    log.info(f"[migrate] sent.json   → tabel sent  : {len(sent)} → {len(cek_sent)} baris")

    if len(cek_leads) != len(leads) or len(cek_sent) != len(sent):
        log.warning("[migrate] Jumlah baris tidak cocok! Cek data sebelum lanjut pakai DB.")
    else:
        log.info(f"[migrate] Migrasi selesai. Database: {db.DB_PATH}")
        log.info(
            "[migrate] leads.json & sent.json tetap disimpan sebagai arsip, "
            "tapi mulai sekarang sistem baca/tulis lewat data/outreach.db."
        )


if __name__ == "__main__":
    main()
