"""
cek_website.py
==============
Scan leads.json dan update field `ada_website` secara akurat.

Untuk leads yang masuk lewat csv_import.py, website sudah dicek otomatis
dari kolom A1zNzb href di CSV. Script ini menangani leads LAMA (dari
get_phones.py / manual) yang `ada_website`-nya belum dicek beneran.

Cara kerja:
1. Baca leads.json, filter yang ada `maps_url` dan belum dicek.
2. Fetch halaman Google Maps bisnis tersebut.
3. Cari pola website URL (non-Google) di HTML awal.
4. Update `ada_website: true` + simpan URL websitenya kalau ketemu.
5. Builder.py otomatis skip lead yang `ada_website: true`.

Cara pakai:
    python agents/cek_website.py              -> scan semua lead yang belum dicek
    python agents/cek_website.py --dry-run    -> preview tanpa tulis ke leads.json
    python agents/cek_website.py --force      -> scan ulang semua (termasuk yang sudah dicek)

Catatan:
- Google Maps modern di-render via JS, jadi tidak semua website terdeteksi.
- Script ini best-effort: yang terdeteksi = pasti punya website.
  Yang tidak terdeteksi belum tentu tidak punya website.
- Jeda 1 detik antar request supaya tidak diblokir Google.
"""

import json
import re
import sys
import time
from pathlib import Path

try:
    import requests as _req
    _REQUESTS_ADA = True
except ImportError:
    _REQUESTS_ADA = False

try:
    from .log_setup import buat_logger
    from .config import KOTA_TARGET
    from . import backup, db
except ImportError:
    from log_setup import buat_logger
    from config import KOTA_TARGET
    import backup, db

log = buat_logger("cek_website")

BASE_DIR   = Path(__file__).resolve().parent.parent
LEADS_PATH = BASE_DIR / "data" / "leads.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9",
}

# Pola URL website non-Google di blob HTML Maps
_RE_WEBSITE = re.compile(
    r'"(https?://(?!(?:www\.)?google(?:usercontent)?\.com|'
    r'gstatic\.com|googleapis\.com|goo\.gl|play\.google)[^"]{4,100})"'
)

# Domain yang dianggap bukan website bisnis mandiri (social media, marketplace)
_BUKAN_WEBSITE_MANDIRI = {
    "facebook.com", "instagram.com", "tiktok.com",
    "tokopedia.com", "shopee.co.id", "bukalapak.com",
    "gofood.co.id", "grabfood.com", "twitter.com", "youtube.com",
    "whatsapp.com", "line.me", "linktr.ee",
}


def _punya_website_sendiri(url: str) -> bool:
    """True kalau URL adalah website bisnis mandiri (bukan social media/marketplace)."""
    domain = url.split("/")[2].lower().replace("www.", "")
    return not any(blocked in domain for blocked in _BUKAN_WEBSITE_MANDIRI)


def cek_website_dari_maps(maps_url: str) -> tuple[bool, str]:
    """
    Fetch HTML awal Google Maps dan cari website URL di blob JS.
    Return: (ada_website, url_website_atau_kosong)
    """
    if not _REQUESTS_ADA or not maps_url:
        return False, ""

    try:
        r = _req.get(maps_url, headers=HEADERS, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return False, ""
        html = r.text
    except Exception as e:
        log.warning(f"[cek_website] Request gagal: {e}")
        return False, ""

    matches = _RE_WEBSITE.findall(html)
    for url in matches:
        if _punya_website_sendiri(url):
            return True, url

    return False, ""


def main(dry_run: bool = False, force: bool = False) -> None:
    if not _REQUESTS_ADA:
        log.warning("[cek_website] Library 'requests' tidak ditemukan. Jalankan: pip install requests")
        return

    leads = db.muat_leads()

    # Pilih yang perlu dicek
    kandidat = [
        (i, l) for i, l in enumerate(leads)
        if l.get("maps_url")
        and (force or not l.get("website_dicek"))
        and not l.get("ada_website")  # skip yang sudah jelas punya website
    ]

    log.info(f"[cek_website] {len(kandidat)} lead akan dicek (dari {len(leads)} total).")
    if not kandidat:
        log.info("[cek_website] Semua lead sudah dicek atau tidak punya maps_url.")
        return

    ditemukan  = 0
    tidak      = 0

    for urut, (idx, lead) in enumerate(kandidat, 1):
        nama = lead.get("nama", "?")
        log.info(f"[cek_website] [{urut}/{len(kandidat)}] Cek: {nama[:45]}")

        ada, url_website = cek_website_dari_maps(lead["maps_url"])

        lead["website_dicek"] = True  # tandai sudah dicek
        if ada:
            lead["ada_website"]   = True
            lead["website_url"]   = url_website
            ditemukan += 1
            log.info(f"[cek_website]   → Punya website: {url_website[:60]}")
        else:
            tidak += 1

        time.sleep(1.0)  # jeda sopan ke server Google

    if not dry_run:
        backup.simpan()
        db.simpan_leads(leads)
        log.info(f"[cek_website] Database (tabel leads) diperbarui.")

    label = "[DRY RUN] " if dry_run else ""
    log.info(
        f"{label}[cek_website] Selesai. "
        f"{ditemukan} punya website (akan diskip builder), "
        f"{tidak} tidak ditemukan."
    )


if __name__ == "__main__":
    args    = sys.argv[1:]
    dry     = "--dry-run" in args
    force   = "--force" in args
    main(dry_run=dry, force=force)
