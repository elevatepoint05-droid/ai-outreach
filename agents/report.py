"""
report.py
=========
Generate laporan PDF ringkasan outreach — buat tracking progress mingguan/bulanan.

Isi laporan:
- Ringkasan status (draft, pending, sent, replied, bounced)
- Reply rate & conversion metrics
- Breakdown per kategori (klinik/hotel/lainnya)
- A/B testing performance (template A vs B)
- Rata-rata waktu respons
- Daftar lead yang reply (dengan nomor + kategori)
- Daftar top-scoring pesan (self-critique)

Cara pakai:
    python main.py report                    -> laporan minggu ini, save ke data/reports/
    python main.py report --hari 30          -> laporan 30 hari terakhir
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)

try:
    from . import db
    from .log_setup import buat_logger
except ImportError:
    import db
    from log_setup import buat_logger

log = buat_logger("report")

BASE_DIR    = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "data" / "reports"

# Warna brand sederhana (biru-hijau, konsisten sama dashboard)
WARNA_AKSEN   = colors.HexColor("#2563eb")
WARNA_HIJAU   = colors.HexColor("#16a34a")
WARNA_ABU     = colors.HexColor("#6b7280")
WARNA_BG_ALT  = colors.HexColor("#f3f4f6")


def _parse_iso(s: str | None) -> datetime | None:
    """Parse ISO timestamp string, toleran terhadap format yang beda-beda."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None


def _filter_periode(sent: list[dict], hari: int) -> list[dict]:
    """Filter sent entries yang di-update dalam N hari terakhir."""
    batas = datetime.now() - timedelta(days=hari)
    hasil = []
    for item in sent:
        waktu = _parse_iso(item.get("updated_at")) or _parse_iso(item.get("jam_kirim"))
        if waktu and waktu >= batas:
            hasil.append(item)
        elif not waktu:
            # Kalau gak ada timestamp sama sekali, tetap masukkan (safety)
            hasil.append(item)
    return hasil


def _hitung_avg_response(sent: list[dict]) -> str:
    """Hitung rata-rata waktu respons (jam_kirim -> jam_reply) dalam jam."""
    durasi = []
    for item in sent:
        if item.get("jam_kirim") and item.get("jam_reply"):
            kirim = _parse_iso(item["jam_kirim"])
            reply = _parse_iso(item["jam_reply"])
            if kirim and reply and reply > kirim:
                durasi.append((reply - kirim).total_seconds() / 3600)
    if not durasi:
        return "Belum ada data"
    rata = sum(durasi) / len(durasi)
    if rata < 1:
        return f"{rata*60:.0f} menit"
    elif rata < 24:
        return f"{rata:.1f} jam"
    else:
        return f"{rata/24:.1f} hari"


def _buat_style() -> dict:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="JudulUtama", fontSize=20, textColor=WARNA_AKSEN,
        fontName="Helvetica-Bold", spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SubJudul", fontSize=11, textColor=WARNA_ABU,
        fontName="Helvetica", spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="SeksiJudul", fontSize=14, textColor=colors.black,
        fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="TeksBiasa", fontSize=10, textColor=colors.black,
        fontName="Helvetica", spaceAfter=4,
    ))
    return styles


def generate(hari: int = 7, output_path: Path | None = None) -> Path:
    """
    Generate laporan PDF untuk N hari terakhir.
    Return: path file PDF yang dibuat.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    semua_sent  = db.get_sent()
    semua_leads = db.get_leads()
    periode     = _filter_periode(semua_sent, hari)

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = REPORTS_DIR / f"laporan_{hari}hari_{ts}.pdf"

    styles = _buat_style()
    elements = []

    # ── HEADER ─────────────────────────────────────────────────────────────
    elements.append(Paragraph("Laporan Outreach — AI Automation", styles["JudulUtama"]))
    periode_label = f"{hari} hari terakhir" if hari != 999 else "Semua waktu"
    elements.append(Paragraph(
        f"Periode: {periode_label} · Dibuat: {datetime.now().strftime('%d %B %Y, %H:%M')}",
        styles["SubJudul"]
    ))

    # ── RINGKASAN STATUS ───────────────────────────────────────────────────
    elements.append(Paragraph("Ringkasan Status", styles["SeksiJudul"]))
    breakdown = {}
    for s in semua_sent:
        st = s.get("status", "pending")
        breakdown[st] = breakdown.get(st, 0) + 1

    total_sent    = breakdown.get("sent", 0) + breakdown.get("replied", 0)
    total_replied = breakdown.get("replied", 0)
    reply_rate    = f"{(total_replied/total_sent*100):.1f}%" if total_sent else "0%"

    data_ringkasan = [
        ["Metrik", "Jumlah"],
        ["Total Leads", str(len(semua_leads))],
        ["Draft (belum direview)", str(breakdown.get("draft", 0))],
        ["Pending (siap kirim)", str(breakdown.get("pending", 0))],
        ["Sudah Dikirim", str(breakdown.get("sent", 0))],
        ["Ada Balasan", str(breakdown.get("replied", 0))],
        ["Perlu Follow Up", str(breakdown.get("followup_due", 0))],
        ["Bounced", str(breakdown.get("bounced", 0))],
        ["Reply Rate", reply_rate],
        ["Rata-rata Waktu Respons", _hitung_avg_response(semua_sent)],
    ]
    tabel_ringkasan = Table(data_ringkasan, colWidths=[9*cm, 6*cm])
    tabel_ringkasan.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), WARNA_AKSEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARNA_BG_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(tabel_ringkasan)

    # ── BREAKDOWN KATEGORI ────────────────────────────────────────────────
    elements.append(Paragraph("Breakdown per Kategori", styles["SeksiJudul"]))
    kategori_stat = {}
    for s in semua_sent:
        # Reply rate cuma masuk akal untuk pesan yang beneran udah dikirim
        if s.get("status") not in {"sent", "replied", "followup_due"}:
            continue
        grup = s.get("kategori_group", "lainnya") or "lainnya"
        if grup not in kategori_stat:
            kategori_stat[grup] = {"total": 0, "replied": 0}
        kategori_stat[grup]["total"] += 1
        if s.get("status") == "replied":
            kategori_stat[grup]["replied"] += 1

    label_kategori = {"klinik": "Klinik", "hotel": "Hotel", "lainnya": "Lainnya"}
    data_kategori = [["Kategori", "Total Terkirim", "Replied", "Reply Rate"]]
    for grup, stat in sorted(kategori_stat.items()):
        rate = f"{(stat['replied']/stat['total']*100):.0f}%" if stat["total"] else "0%"
        data_kategori.append([
            label_kategori.get(grup, grup), str(stat["total"]), str(stat["replied"]), rate
        ])
    tabel_kategori = Table(data_kategori, colWidths=[6*cm, 4*cm, 3*cm, 3*cm])
    tabel_kategori.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), WARNA_HIJAU),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARNA_BG_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(tabel_kategori)

    # ── A/B TESTING ────────────────────────────────────────────────────────
    ab_stat = {"A": {"total": 0, "replied": 0}, "B": {"total": 0, "replied": 0}}
    for s in semua_sent:
        tmpl = s.get("template_id")
        if tmpl in ab_stat:
            ab_stat[tmpl]["total"] += 1
            if s.get("status") == "replied":
                ab_stat[tmpl]["replied"] += 1

    if ab_stat["A"]["total"] or ab_stat["B"]["total"]:
        elements.append(Paragraph("A/B Testing — Performa Template", styles["SeksiJudul"]))
        data_ab = [["Template", "Total Kirim", "Replied", "Reply Rate"]]
        for tmpl, stat in ab_stat.items():
            rate = f"{(stat['replied']/stat['total']*100):.1f}%" if stat["total"] else "0%"
            label = "Template A (fokus masalah)" if tmpl == "A" else "Template B (fokus peluang)"
            data_ab.append([label, str(stat["total"]), str(stat["replied"]), rate])
        tabel_ab = Table(data_ab, colWidths=[7*cm, 3.5*cm, 2.5*cm, 3*cm])
        tabel_ab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), WARNA_ABU),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARNA_BG_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]))
        elements.append(tabel_ab)

    # ── LEAD YANG REPLY (dalam periode) ───────────────────────────────────
    replied_periode = [s for s in periode if s.get("status") == "replied"]
    if replied_periode:
        elements.append(PageBreak())
        elements.append(Paragraph(f"Lead yang Membalas ({periode_label})", styles["SeksiJudul"]))
        data_replied = [["Nama Bisnis", "Nomor WA", "Kategori"]]
        for r in replied_periode[:30]:  # cap 30 biar gak kepanjangan
            grup_label = label_kategori.get(r.get("kategori_group", ""), r.get("kategori", "-"))
            data_replied.append([
                (r.get("nama") or "-")[:40], r.get("nomor_wa", "-"), grup_label
            ])
        tabel_replied = Table(data_replied, colWidths=[8*cm, 4*cm, 3.5*cm])
        tabel_replied.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), WARNA_HIJAU),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARNA_BG_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ]))
        elements.append(tabel_replied)

    # ── TOP-SCORING PESAN (kalau self-critique aktif) ─────────────────────
    dengan_skor = [s for s in semua_sent if (s.get("skor_pesan") or 0) > 0]
    if dengan_skor:
        dengan_skor.sort(key=lambda s: s.get("skor_pesan") or 0, reverse=True)
        elements.append(Paragraph("Top 5 Pesan dengan Skor Tertinggi", styles["SeksiJudul"]))
        data_skor = [["Nama Bisnis", "Skor", "Cuplikan Pesan"]]
        for s in dengan_skor[:5]:
            cuplikan = (s.get("pesan") or "")[:60] + "..."
            data_skor.append([
                (s.get("nama") or "-")[:30], f"{s.get('skor_pesan')}/10", cuplikan
            ])
        tabel_skor = Table(data_skor, colWidths=[4.5*cm, 2*cm, 9*cm])
        tabel_skor.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), WARNA_AKSEN),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARNA_BG_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ]))
        elements.append(tabel_skor)

    # ── FOOTER ─────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        "Generated otomatis oleh AI Outreach Automation System.",
        styles["TeksBiasa"]
    ))

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
    )
    doc.build(elements)

    log.info(f"[report] Laporan dibuat: {output_path}")
    return output_path


def main() -> None:
    args = sys.argv[1:]
    hari = 7
    if "--hari" in args:
        try:
            idx = args.index("--hari")
            hari = int(args[idx + 1])
        except (IndexError, ValueError):
            print("[report] --hari butuh angka, contoh: --hari 30")
            return

    path = generate(hari=hari)
    print(f"[report] ✓ Laporan berhasil dibuat: {path}")


if __name__ == "__main__":
    main()
