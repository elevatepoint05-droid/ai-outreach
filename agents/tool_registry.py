"""
tool_registry.py
=================
Daftar "tools" yang bisa dipilih oleh agent loop.
"""

from typing import Any, Callable

try:
    from . import db
    from .log_setup import buat_logger
except ImportError:
    import db
    from log_setup import buat_logger

log = buat_logger("tool_registry")


def _hitung_pending_dan_draft() -> int:
    """Helper: hitung lead yang statusnya pending/draft (belum sukses dikirim)."""
    sent = db.get_sent()
    return sum(1 for s in sent if s.get("status") in ("pending", "draft"))


def _tool_cek_status() -> dict:
    """Baca ringkasan status sistem sekarang — aman, tidak ubah apapun."""
    sent = db.get_sent()
    leads = db.get_leads()
    from collections import Counter
    breakdown = Counter(s.get("status", "pending") for s in sent)
    return {
        "total_leads": len(leads),
        "breakdown_status": dict(breakdown),
    }


def _tool_build_pesan(mode_draft: bool = False) -> dict:
    """Generate pesan WA baru untuk lead pending, pakai Groq."""
    try:
        from . import builder
    except ImportError:
        import builder
    sebelum = _hitung_pending_dan_draft()
    builder.main(mode_draft=mode_draft)
    sesudah = _hitung_pending_dan_draft()
    return {"pesan_baru_dibuat": True, "pending_sebelum": sebelum, "pending_sesudah": sesudah}


def _tool_cek_followup() -> dict:
    """Tandai lead yang sudah lewat batas hari belum respons jadi followup_due."""
    try:
        from . import tracker
    except ImportError:
        import tracker
    jumlah = tracker.cek_followup()
    return {"jumlah_ditandai_followup": jumlah}


def _tool_scan_website(force: bool = False) -> dict:
    """Cek lead mana yang sebenarnya sudah punya website."""
    try:
        from . import cek_website
    except ImportError:
        import cek_website
    cek_website.main(dry_run=False, force=force)
    return {"scan_selesai": True}


def _tool_generate_report(hari: int = 7) -> dict:
    """Bikin laporan PDF ringkasan performa outreach N hari terakhir."""
    try:
        from . import report
    except ImportError:
        import report
    path = report.generate(hari=hari)
    return {"laporan_dibuat": str(path)}


def _tool_tidak_ada_aksi() -> dict:
    """Tidak melakukan apa-apa — dipilih kalau kondisi sistem sudah baik."""
    return {"info": "Tidak ada aksi yang diperlukan saat ini."}


TOOLS: dict[str, dict[str, Any]] = {
    "cek_status": {
        "deskripsi": "Baca ringkasan kondisi sistem sekarang (jumlah lead per status). "
                     "Pakai ini kalau butuh info sebelum mutusin aksi lain.",
        "fungsi": _tool_cek_status,
        "butuh_args": [],
        "kategori": "aman",
    },
    "build_pesan": {
        "deskripsi": "Generate pesan WhatsApp baru untuk lead yang masih pending "
                     "(belum pernah dikirimi pesan). Pakai Groq API, ada biaya kuota.",
        "fungsi": _tool_build_pesan,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "cek_followup": {
        "deskripsi": "Cek lead yang sudah dikirimi pesan tapi lebih dari beberapa hari "
                     "belum ada respons, tandai perlu di-follow-up.",
        "fungsi": _tool_cek_followup,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "scan_website": {
        "deskripsi": "Cek lead mana yang ternyata sudah punya website sendiri, biar "
                     "tidak dikirimi pesan penawaran yang tidak relevan.",
        "fungsi": _tool_scan_website,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "generate_report": {
        "deskripsi": "Bikin laporan PDF ringkasan performa outreach untuk periode tertentu.",
        "fungsi": _tool_generate_report,
        "butuh_args": [],
        "kategori": "aman",
    },
    "tidak_ada_aksi": {
        "deskripsi": "Pilih ini kalau setelah dicek, tidak ada yang perlu dilakukan sekarang.",
        "fungsi": _tool_tidak_ada_aksi,
        "butuh_args": [],
        "kategori": "aman",
    },
}


def daftar_tools_untuk_prompt() -> str:
    """Format daftar tools jadi teks yang bisa dimasukkan ke prompt AI."""
    baris = []
    for nama, info in TOOLS.items():
        baris.append(f"- {nama}: {info['deskripsi']}")
    return "\n".join(baris)


def eksekusi_tool(nama_tool: str, **kwargs) -> dict:
    """Eksekusi tool berdasarkan nama."""
    if nama_tool not in TOOLS:
        return {"error": f"Tool '{nama_tool}' tidak ada di registry. Tools tersedia: {list(TOOLS.keys())}"}

    tool = TOOLS[nama_tool]
    try:
        hasil = tool["fungsi"](**kwargs)
        return {"sukses": True, "hasil": hasil}
    except Exception as e:
        log.warning(f"[tool_registry] Tool '{nama_tool}' gagal dieksekusi: {e}")
        return {"sukses": False, "error": str(e)}
