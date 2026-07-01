"""
merge_leads.py
==============
Gabungkan data/priority_with_phones.json ke dalam data/leads.json.

Upgrade v2:
- Fuzzy dedup (#6): selain cek nomor WA, sekarang juga cek kemiripan nama
  bisnis dengan difflib — tangkap kasus kayak "Hotel Berlian" vs "Hotel Berlian
  Berau" yang nomor WA-nya beda tapi bisnisnya sama.
- Logging (#12): output ke console + data/outreach.log

Cara pakai:
    python agents/merge_leads.py
"""

import json
from pathlib import Path

try:
    from .utils import klasifikasi_kategori, adalah_duplikat_fuzzy, validasi_nomor_wa
    from .log_setup import buat_logger
    from .config import FUZZY_THRESHOLD as _THRESHOLD
    from . import db
except ImportError:
    from utils import klasifikasi_kategori, adalah_duplikat_fuzzy, validasi_nomor_wa
    from log_setup import buat_logger
    from config import FUZZY_THRESHOLD as _THRESHOLD
    import db

log = buat_logger("merge_leads")

BASE_DIR      = Path(__file__).resolve().parent.parent
LEADS_PATH    = BASE_DIR / "data" / "leads.json"
PRIORITY_PATH = BASE_DIR / "data" / "priority_with_phones.json"

# Threshold kemiripan nama untuk dianggap duplikat (0.0–1.0)
FUZZY_THRESHOLD = _THRESHOLD


def muat_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def simpan_json(path: Path, data: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    leads_lama         = db.muat_leads()
    kandidat_prioritas = muat_json(PRIORITY_PATH)

    if not kandidat_prioritas:
        log.warning(f"[merge_leads] {PRIORITY_PATH.name} kosong/tidak ada. Jalankan get_phones.py dulu.")
        return

    nomor_terpakai = {l["nomor_wa"] for l in leads_lama if l.get("nomor_wa")}
    nama_terpakai  = [l.get("nama", "") for l in leads_lama if l.get("nama")]

    ditambah            = 0
    dilewati_nomor      = 0
    dilewati_fuzzy      = 0
    dilewati_no_phone   = 0
    dilewati_invalid    = 0

    for p in kandidat_prioritas:
        nomor_wa_raw = p.get("nomor_wa")
        nama         = p.get("nama", "")

        if not nomor_wa_raw:
            dilewati_no_phone += 1
            continue

        # Validasi nomor WA (#7)
        ok, nomor_wa, alasan = validasi_nomor_wa(nomor_wa_raw)
        if not ok:
            log.info(f"[merge_leads] Skip nomor invalid '{nomor_wa_raw}' ({nama}): {alasan}")
            dilewati_invalid += 1
            continue

        # Dedup 1: nomor WA sama persis
        if nomor_wa in nomor_terpakai:
            dilewati_nomor += 1
            continue

        # Dedup 2: nama bisnis sangat mirip (fuzzy) — (#6)
        duplikat, nama_mirip = adalah_duplikat_fuzzy(nama, nama_terpakai, FUZZY_THRESHOLD)
        if duplikat:
            log.info(f"[merge_leads] Skip fuzzy duplikat: '{nama}' ≈ '{nama_mirip}'")
            dilewati_fuzzy += 1
            continue

        kategori = p.get("kategori", "")
        leads_lama.append({
            "nama":           nama,
            "nomor_wa":       nomor_wa,
            "alamat":         p.get("alamat", ""),
            "kota":           p.get("kota", "Berau"),
            "kategori":       kategori,
            "kategori_group": klasifikasi_kategori(kategori),
            "rating":         p.get("rating", ""),
            "ada_website":    False,
            "status":         "baru",
        })
        nomor_terpakai.add(nomor_wa)
        nama_terpakai.append(nama)
        ditambah += 1

    db.simpan_leads(leads_lama)
    log.info(f"[merge_leads] {ditambah} lead baru ditambahkan ke database (tabel leads).")
    if dilewati_nomor:
        log.info(f"[merge_leads] {dilewati_nomor} dilewati (nomor WA duplikat).")
    if dilewati_fuzzy:
        log.info(f"[merge_leads] {dilewati_fuzzy} dilewati (nama bisnis mirip / fuzzy dedup).")
    if dilewati_invalid:
        log.info(f"[merge_leads] {dilewati_invalid} dilewati (nomor WA tidak valid / telepon rumah).")
    if dilewati_no_phone:
        log.info(f"[merge_leads] {dilewati_no_phone} dilewati (tidak ada nomor WA).")
    log.info(f"[merge_leads] Total lead di database sekarang: {len(leads_lama)}.")


if __name__ == "__main__":
    main()
