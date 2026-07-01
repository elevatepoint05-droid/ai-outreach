"""
csv_import.py
=============
Auto-import CSV dari Instant Data Scraper (Google Maps) ke tabel `leads`
(data/outreach.db, lewat agents/db.py — dulunya leads.json, lihat #13).

Gak perlu mapping kolom manual — script ini auto-deteksi kolom dari header
yang dihasilkan Instant Data Scraper (format class-name Google Maps).

Cara pakai:
    python agents/csv_import.py                      -> proses semua CSV di data/raw_scrape/
    python agents/csv_import.py path/ke/file.csv     -> proses satu file spesifik
    python agents/csv_import.py --dry-run            -> preview tanpa tulis ke database

Output: lead baru langsung masuk data/outreach.db (tabel leads), siap diproses python main.py build.

Format CSV yang didukung:
    Kolom-kolom Instant Data Scraper dari Google Maps (nama kolom = CSS class name).
    Script ini auto-handle variasi format (jumlah kolom W4Efsd berbeda antar export).
"""

import csv
import json
import sys
from pathlib import Path

try:
    from .utils import klasifikasi_kategori, validasi_nomor_wa, adalah_duplikat_fuzzy
    from .log_setup import buat_logger
    from .config import FUZZY_THRESHOLD, KOTA_TARGET
    from . import backup, db
except ImportError:
    from utils import klasifikasi_kategori, validasi_nomor_wa, adalah_duplikat_fuzzy
    from log_setup import buat_logger
    from config import FUZZY_THRESHOLD, KOTA_TARGET
    import backup, db

log = buat_logger("csv_import")

BASE_DIR   = Path(__file__).resolve().parent.parent
RAW_DIR    = BASE_DIR / "data" / "raw_scrape"
LEADS_PATH = BASE_DIR / "data" / "leads.json"

# ── Mapping kolom Instant Data Scraper → field kita ─────────────────────────
# Kolom-kolom ini adalah CSS class name yang dipakai Google Maps,
# di-capture langsung oleh Instant Data Scraper sebagai header CSV.
KOLOM_MAPS_URL  = "hfpxzc href"   # link Google Maps
KOLOM_NAMA      = "qBF1Pd"        # nama bisnis
KOLOM_RATING    = "MW4etd"        # rating (format: "4,5" dengan koma)
KOLOM_TELEPON   = "UsdlK"         # nomor telepon (kalau ada di halaman)
KOLOM_WEBSITE   = "A1zNzb href"   # URL website (kalau bisnis punya)

# Kolom "W4Efsd" muncul berulang (W4Efsd, W4Efsd 2, W4Efsd 3, dst).
# Yang pertama (W4Efsd) = kategori. Yang lain bisa berisi alamat, status buka, dll.
KOLOM_KATEGORI  = "W4Efsd"
# Kolom alamat: cari di W4Efsd 2, 3, 4 — ambil yang terlihat kayak alamat
KOLOM_ALAMAT_CANDIDATES = ["W4Efsd 2", "W4Efsd 3", "W4Efsd 4", "W4Efsd 5"]

# Kalimat yang biasanya muncul di kolom W4Efsd tapi BUKAN alamat
BUKAN_ALAMAT = {"·", "tutup", "buka", "sementara tutup", ""}


def _ambil_nilai(row: dict, kolom: str) -> str:
    """Ambil nilai kolom, strip whitespace."""
    return (row.get(kolom) or "").strip()


def _tebak_alamat(row: dict) -> str:
    """
    Coba tebak kolom mana yang berisi alamat dari candidates W4Efsd 2-5.
    Alamat biasanya punya karakter seperti "Jl.", nomor, titik koma,
    atau panjang > 5 karakter dan bukan kata kunci status toko.
    """
    for kolom in KOLOM_ALAMAT_CANDIDATES:
        val = _ambil_nilai(row, kolom)
        val_lower = val.lower()
        # skip kalau kosong, hanya "·", atau kata status toko
        if not val or val_lower in BUKAN_ALAMAT:
            continue
        if val == "·" or val.startswith("·"):
            continue
        # skip kalau isinya jam buka ("Buka pukul ...", "Tutup")
        if any(kw in val_lower for kw in ["pukul", "tutup", "buka", "sementara"]):
            continue
        # kalau cukup panjang, kemungkinan besar alamat
        if len(val) >= 5:
            return val
    return ""


def _tebak_kota(maps_url: str, kota_default: str) -> str:
    """
    Coba tebak kota dari URL Google Maps (nama place).
    Fallback ke kota_default kalau tidak bisa ditebak.
    """
    nama_kota_dikenal = ["Berau", "Bulungan", "Malinau", "Tarakan",
                         "Samarinda", "Balikpapan", "Bontang", "Kutai"]
    url_lower = maps_url.lower()
    for kota in nama_kota_dikenal:
        if kota.lower() in url_lower:
            return kota
    return kota_default


def _ada_website(row: dict) -> bool:
    """Cek apakah bisnis punya website (bukan link Google)."""
    website = _ambil_nilai(row, KOLOM_WEBSITE)
    if not website:
        return False
    google_domains = ("google.com", "goo.gl", "maps.app.goo.gl")
    return not any(d in website for d in google_domains)


def _normalisasi_rating(raw: str) -> str:
    """Konversi rating '4,5' (koma) → '4.5' (titik), kembalikan string."""
    return raw.replace(",", ".").strip()


def proses_csv(
    path: Path,
    kota_default: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """
    Proses satu file CSV dari Instant Data Scraper.
    Return: list lead yang valid dari file ini.
    """
    if not path.exists():
        log.warning(f"[csv_import] File tidak ditemukan: {path}")
        return []

    if not kota_default:
        kota_default = KOTA_TARGET[0] if KOTA_TARGET else "Berau"

    leads = []
    total_baris = 0
    skip_no_nama = 0
    skip_ada_website = 0
    skip_no_phone = 0

    with open(path, encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total_baris += 1

            # Wajib: nama bisnis
            nama = _ambil_nilai(row, KOLOM_NAMA)
            if not nama:
                skip_no_nama += 1
                continue

            # Skip kalau sudah punya website sendiri
            if _ada_website(row):
                skip_ada_website += 1
                continue

            # Nomor WA (opsional — lead tanpa nomor tetap masuk tapi flagged)
            telepon_raw = _ambil_nilai(row, KOLOM_TELEPON)
            nomor_wa    = ""
            if telepon_raw:
                ok, nomor_norm, _ = validasi_nomor_wa(telepon_raw)
                if ok:
                    nomor_wa = nomor_norm

            if not nomor_wa:
                skip_no_phone += 1
                # Tetap masuk pipeline — bisa di-enrich manual atau lewat get_phones.py

            maps_url  = _ambil_nilai(row, KOLOM_MAPS_URL)
            kategori  = _ambil_nilai(row, KOLOM_KATEGORI)
            alamat    = _tebak_alamat(row)
            kota      = _tebak_kota(maps_url, kota_default)
            rating    = _normalisasi_rating(_ambil_nilai(row, KOLOM_RATING))

            leads.append({
                "nama":           nama,
                "nomor_wa":       nomor_wa,
                "alamat":         alamat,
                "kota":           kota,
                "kategori":       kategori,
                "kategori_group": klasifikasi_kategori(kategori),
                "rating":         rating,
                "maps_url":       maps_url,
                "ada_website":    False,
                "status":         "baru",
            })

    label = "[DRY RUN] " if dry_run else ""
    log.info(f"{label}[csv_import] {path.name}: {total_baris} baris → "
             f"{len(leads)} kandidat "
             f"(skip: {skip_ada_website} punya website, "
             f"{skip_no_phone} no-phone, {skip_no_nama} no-nama)")
    return leads


def gabung_ke_leads(
    leads_baru: list[dict],
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """
    Gabungkan leads_baru ke leads.json dengan dedup (nomor WA + fuzzy nama).
    Return: (ditambah, skip_nomor, skip_fuzzy)
    """
    if not leads_baru:
        return 0, 0, 0

    # Baca leads yang sudah ada
    leads_lama: list[dict] = db.muat_leads()

    nomor_terpakai = {l["nomor_wa"] for l in leads_lama if l.get("nomor_wa")}
    nama_terpakai  = [l.get("nama", "") for l in leads_lama if l.get("nama")]

    ditambah   = 0
    skip_nomor = 0
    skip_fuzzy = 0

    for lead in leads_baru:
        nomor_wa = lead.get("nomor_wa")

        # Dedup nomor WA
        if nomor_wa and nomor_wa in nomor_terpakai:
            skip_nomor += 1
            continue

        # Dedup fuzzy nama
        duplikat, nama_mirip = adalah_duplikat_fuzzy(
            lead.get("nama", ""), nama_terpakai, FUZZY_THRESHOLD
        )
        if duplikat:
            log.info(f"[csv_import] Skip fuzzy: '{lead['nama']}' ≈ '{nama_mirip}'")
            skip_fuzzy += 1
            continue

        leads_lama.append(lead)
        if nomor_wa:
            nomor_terpakai.add(nomor_wa)
        nama_terpakai.append(lead.get("nama", ""))
        ditambah += 1

    if not dry_run and ditambah > 0:
        backup.simpan()
        db.simpan_leads(leads_lama)

    return ditambah, skip_nomor, skip_fuzzy


def main(paths: list[Path] = None, dry_run: bool = False) -> None:
    """
    Proses satu atau beberapa CSV dan gabungkan ke leads.json.
    paths=None → proses semua CSV di data/raw_scrape/
    """
    if paths is None:
        paths = sorted(RAW_DIR.glob("*.csv"))

    if not paths:
        log.warning("[csv_import] Tidak ada file CSV ditemukan.")
        log.warning(f"[csv_import] Taruh CSV dari Instant Data Scraper di: {RAW_DIR}")
        return

    semua_leads: list[dict] = []
    for path in paths:
        leads = proses_csv(path, dry_run=dry_run)
        semua_leads.extend(leads)

    log.info(f"[csv_import] Total kandidat dari {len(paths)} file: {len(semua_leads)}")

    ditambah, skip_nomor, skip_fuzzy = gabung_ke_leads(semua_leads, dry_run=dry_run)

    label = "[DRY RUN] " if dry_run else ""
    log.info(f"{label}[csv_import] ✓ {ditambah} lead baru masuk database (tabel leads)")
    if skip_nomor:
        log.info(f"{label}[csv_import] {skip_nomor} skip (nomor WA duplikat)")
    if skip_fuzzy:
        log.info(f"{label}[csv_import] {skip_fuzzy} skip (nama bisnis mirip)")

    if dry_run:
        log.info("[csv_import] DRY RUN selesai — database tidak diubah.")
    else:
        log.info(f"[csv_import] Selesai. Jalankan: python main.py build")


if __name__ == "__main__":
    args   = sys.argv[1:]
    dry    = "--dry-run" in args
    paths  = [Path(a) for a in args if not a.startswith("--")]
    main(paths if paths else None, dry_run=dry)
