"""
get_phones.py
=============
Ambil nomor telepon untuk setiap lead di priority_leads.txt,
lalu simpan ke data/priority_with_phones.json.

Strategi (berurutan, berhenti di strategi pertama yang berhasil):
  1. CSV asli (kolom UsdlK + scan semua kolom untuk pola nomor HP) — paling andal
  2. Google Maps HTML scraping — best-effort, Google render nomor via JS
     sehingga hanya sesekali berhasil lewat pola regex di blob awal

Cara pakai:
    python agents/get_phones.py
    python agents/get_phones.py --dry-run   # tanpa tulis file output

Catatan:
    Bisnis yang tidak ditemukan nomornya akan tetap masuk JSON
    dengan field nomor_wa kosong dan phone_source "not_found".
"""

import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
BASE_DIR       = Path(__file__).resolve().parent.parent
RAW_DIR        = BASE_DIR / "data" / "raw_scrape"
PRIORITY_FILE  = BASE_DIR / "data" / "priority_leads.txt"
OUTPUT_FILE    = BASE_DIR / "data" / "priority_with_phones.json"

# ---------------------------------------------------------------------------
# Kategori yang di-skip
# ---------------------------------------------------------------------------
SKIP_CATEGORIES = {
    "puskesmas", "rumah sakit", "rsud",
    "bidan", "pusat kesehatan masyarakat",
    "dokter kandungan", "pusat perawatan ibu hamil",
}

# ---------------------------------------------------------------------------
# HTTP headers — pakai user-agent mobile karena kadang lebih ringkas
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.82 Mobile Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Normalisasi nomor ke format 62xxxxxxxxxx
# ---------------------------------------------------------------------------
def normalisasi(nomor: str) -> str | None:
    d = re.sub(r"\D", "", nomor)
    if d.startswith("0"):
        d = "62" + d[1:]
    elif d.startswith("8"):
        d = "62" + d
    if not d.startswith("62"):
        return None
    if not (11 <= len(d) <= 15):
        return None
    return d


def is_valid_phone(val: str) -> bool:
    d = re.sub(r"\D", "", val or "")
    return (
        9 <= len(d) <= 13
        and (d.startswith("0") or d.startswith("62"))
        and not d.startswith("021")   # singkirkan telepon rumah Jakarta
    )


# ---------------------------------------------------------------------------
# Strategi 1 — scan semua CSV untuk nomor HP
# ---------------------------------------------------------------------------
def bangun_csv_phone_dict() -> dict[str, tuple[str, str]]:
    """
    Kembalikan dict: nama_bisnis_lowercase → (nomor_mentah, nama_kolom).
    Scan kolom UsdlK sebagai prioritas, lalu scan kolom lain sebagai fallback.
    """
    result: dict[str, tuple[str, str]] = {}
    PRIORITY_COL = "UsdlK"

    for f in sorted(RAW_DIR.glob("*.csv")):
        with open(f, encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            headers = next(reader, [])
            prio_idx = next(
                (i for i, h in enumerate(headers) if h.strip() == PRIORITY_COL),
                None,
            )

            for row in reader:
                if not row or not (row[0] or "").startswith("http"):
                    continue
                name = (row[1].strip() if len(row) > 1 else "").lower()
                if not name or name in result:
                    continue

                phone_found = None
                source_col  = ""

                # Cek kolom UsdlK dulu
                if prio_idx is not None and len(row) > prio_idx:
                    val = row[prio_idx].strip()
                    if is_valid_phone(val):
                        phone_found = val
                        source_col  = PRIORITY_COL

                # Fallback: scan semua kolom untuk pola nomor HP
                if not phone_found:
                    for i, val in enumerate(row):
                        val = val.strip()
                        # skip kolom URL, gambar, teks panjang
                        if val.startswith("http") or len(val) > 25:
                            continue
                        if is_valid_phone(val):
                            phone_found = val
                            source_col  = headers[i] if i < len(headers) else f"col{i}"
                            break

                if phone_found:
                    result[name] = (phone_found, source_col)

    return result


# ---------------------------------------------------------------------------
# Strategi 2 — Maps HTML scraping (best-effort)
# ---------------------------------------------------------------------------
PHONE_PATTERNS = [
    # nomor dalam tanda kutip di blob JS
    re.compile(r'"(0[2-9]\d{7,11})"'),
    re.compile(r'"(\+62\d{9,12})"'),
    # nomor dipisah karakter \n di dalam blob
    re.compile(r'\\n(0[2-9][\d\-]{7,12})\\n'),
    re.compile(r'\\n(\+62[\d\-]{9,13})\\n'),
    # nomor setelah karakter khusus di raw text
    re.compile(r'(?<!["\d])(0[89]\d{8,10})(?!["\d])'),
]


def scrape_maps_phone(url: str) -> str | None:
    """
    Coba ambil nomor dari halaman Google Maps via requests.
    Berhasil hanya kalau nomor kebetulan ada di blob awal (tidak selalu).
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return None
        html = r.text
    except requests.RequestException:
        return None

    seen: set[str] = set()
    for pat in PHONE_PATTERNS:
        for match in pat.findall(html):
            raw = match.strip()
            norm = normalisasi(raw)
            if norm and norm not in seen:
                seen.add(norm)
                return norm   # ambil yang pertama valid

    return None


# ---------------------------------------------------------------------------
# Parse priority_leads.txt
# ---------------------------------------------------------------------------
def parse_priority_leads() -> list[dict]:
    leads = []
    with open(PRIORITY_FILE, encoding="utf-8") as fh:
        for line in fh:
            if "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            no = parts[0].strip()
            try:
                int(no)
            except ValueError:
                continue

            name     = parts[1].strip()
            kategori = parts[2].strip()
            rating   = parts[3].strip()
            alamat   = parts[4].strip()
            url      = parts[5].strip() if len(parts) > 5 else ""

            if any(kw in kategori.lower() for kw in SKIP_CATEGORIES):
                continue

            leads.append({
                "nama": name,
                "kategori": kategori,
                "rating": rating,
                "alamat": alamat,
                "maps_url": url,
            })
    return leads


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(dry_run: bool = False) -> None:
    print("=" * 60)
    print("GET PHONES — Priority Leads")
    print("=" * 60)

    print("\n[1/3] Membangun lookup nomor dari CSV asli...")
    csv_phones = bangun_csv_phone_dict()
    print(f"      {len(csv_phones)} bisnis punya nomor di CSV")

    print("\n[2/3] Parsing priority_leads.txt...")
    leads = parse_priority_leads()
    print(f"      {len(leads)} lead setelah skip Puskesmas/RSUD/Bidan/Bidan")

    print("\n[3/3] Menggabungkan nomor + Maps scraping fallback...")
    results = []
    stats = {"csv": 0, "maps": 0, "not_found": 0}

    for i, lead in enumerate(leads, 1):
        name_key = lead["nama"].lower()
        nomor_wa = ""
        source   = "not_found"

        # Strategi 1: CSV
        if name_key in csv_phones:
            raw, col = csv_phones[name_key]
            nomor_wa = normalisasi(raw) or ""
            if nomor_wa:
                source = f"csv:{col}"
                stats["csv"] += 1

        # Strategi 2: Maps scraping (hanya kalau belum punya nomor)
        if not nomor_wa and lead["maps_url"]:
            print(f"  [{i}/{len(leads)}] Maps scraping: {lead['nama'][:45]}...", end="\r")
            scraped = scrape_maps_phone(lead["maps_url"])
            if scraped:
                nomor_wa = scraped
                source   = "maps_html"
                stats["maps"] += 1
            time.sleep(0.8)   # jeda sopan ke server Google

        if not nomor_wa:
            stats["not_found"] += 1

        results.append({
            "nama":         lead["nama"],
            "nomor_wa":     nomor_wa,
            "kategori":     lead["kategori"],
            "rating":       lead["rating"],
            "alamat":       lead["alamat"],
            "kota":         "Berau",
            "maps_url":     lead["maps_url"],
            "phone_source": source,
        })

    # Ringkasan
    print(" " * 70)   # hapus baris progress
    print(f"\n{'='*60}")
    print("HASIL")
    print(f"{'='*60}")
    print(f"Total leads diproses  : {len(results)}")
    print(f"Nomor dari CSV        : {stats['csv']}")
    print(f"Nomor dari Maps HTML  : {stats['maps']}")
    print(f"Tidak ditemukan       : {stats['not_found']}")
    print(f"Coverage              : {len(results)-stats['not_found']}/{len(results)} ({(len(results)-stats['not_found'])*100//len(results)}%)")

    # Tabel terminal
    print(f"\n{'No':<4} {'Nama':40} {'Kategori':22} {'Nomor WA':16} {'Sumber'}")
    print("-" * 100)
    for i, r in enumerate(results, 1):
        nomor_display = r["nomor_wa"] if r["nomor_wa"] else "-"
        sumber_short  = r["phone_source"].split(":")[0]
        print(
            f"{str(i):<4} {r['nama'][:40]:40} {r['kategori'][:22]:22} "
            f"{nomor_display[:16]:16} {sumber_short}"
        )

    # Simpan JSON
    if not dry_run:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nDisimpan ke: {OUTPUT_FILE}")
        print(f"Total entri JSON: {len(results)}")
    else:
        print("\n[DRY RUN] Tidak menulis file output.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)
