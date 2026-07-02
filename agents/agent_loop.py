"""
agent_loop.py
=============
Loop Engineering beneran — Think → Act → Observe → Done?, AI yang mikir
dan milih tool sendiri, BUKAN if/else hardcoded.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

try:
    from . import db, tool_registry
    from .log_setup import buat_logger
    from . import config as cfg
except ImportError:
    import db, tool_registry
    from log_setup import buat_logger
    import config as cfg

log = buat_logger("agent_loop")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MODEL = cfg.GROQ_MODEL if hasattr(cfg, "GROQ_MODEL") else "llama-3.1-8b-instant"

PROMPT_SISTEM = """Kamu adalah AI agent yang mengelola sistem outreach WhatsApp
untuk freelancer web developer di Indonesia. Tugasmu: putuskan SATU aksi paling
tepat untuk diambil sekarang, berdasarkan kondisi sistem yang diberikan.

Kamu HANYA boleh memilih dari daftar tools yang tersedia — jangan mengarang
tool yang tidak ada di daftar.

Prinsip pengambilan keputusan:
- Kalau ada lead pending tapi belum ada pesan dibuatkan -> build_pesan
- Kalau ada lead sent yang lama tidak respons -> cek_followup
- Kalau leads.json ada yang belum pernah dicek websitenya -> scan_website
  (tapi jangan pilih ini kalau baru saja dilakukan di riwayat)
- Kalau semua sudah ditangani, sistem sudah rapi -> tidak_ada_aksi
- Jangan pilih tool yang sama berkali-kali berturut-turut tanpa alasan baru

Jawab HANYA dalam format JSON, tidak ada teks lain:
{"tool": "<nama_tool>", "alasan": "<1 kalimat alasan singkat>", "selesai": <true/false>}

"selesai": true kalau menurutmu tidak perlu putaran berikutnya setelah ini."""


def _snapshot_kondisi() -> dict:
    """Ambil snapshot kondisi sistem sekarang buat dikasih ke AI."""
    sent = db.get_sent()
    leads = db.get_leads()
    from collections import Counter
    breakdown = Counter(s.get("status", "pending") for s in sent)
    belum_dicek_website = sum(1 for l in leads if not l.get("website_dicek"))
    return {
        "total_leads": len(leads),
        "breakdown_status": dict(breakdown),
        "leads_belum_dicek_website": belum_dicek_website,
        "waktu_sekarang": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _think(histori_aksi: list[dict], api_key: str) -> dict:
    """Satu panggilan LLM: kasih kondisi sistem + histori, minta AI pilih tool berikutnya."""
    kondisi = _snapshot_kondisi()
    daftar_tools = tool_registry.daftar_tools_untuk_prompt()

    histori_teks = "Belum ada aksi diambil di putaran ini."
    if histori_aksi:
        baris = [
            f"{i+1}. Tool: {h['tool']} -> Hasil: {json.dumps(h['hasil'], ensure_ascii=False)[:150]}"
            for i, h in enumerate(histori_aksi)
        ]
        histori_teks = "\n".join(baris)

    konteks_user = (
        f"KONDISI SISTEM SEKARANG:\n{json.dumps(kondisi, ensure_ascii=False, indent=2)}\n\n"
        f"TOOLS TERSEDIA:\n{daftar_tools}\n\n"
        f"HISTORI AKSI DI PUTARAN INI:\n{histori_teks}\n\n"
        "Pilih SATU tool untuk dijalankan sekarang."
    )

    client = Groq(api_key=api_key)
    respon = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PROMPT_SISTEM},
            {"role": "user", "content": konteks_user},
        ],
        temperature=0.3,
        max_tokens=150,
    )
    teks = respon.choices[0].message.content.strip()
    teks = teks.replace("```json", "").replace("```", "").strip()

    try:
        keputusan = json.loads(teks)
        if "tool" not in keputusan:
            raise ValueError("Field 'tool' tidak ada di respons AI")
        return keputusan
    except Exception as e:
        log.warning(f"[agent_loop] Gagal parse keputusan AI: {e}. Raw: {teks[:200]}")
        return {"tool": "tidak_ada_aksi", "alasan": f"Parsing gagal, fallback aman: {e}", "selesai": True}


def jalankan_loop(max_iterasi: int = 5) -> list[dict]:
    """
    Jalankan Think -> Act -> Observe -> Done? sampai AI bilang selesai
    atau max_iterasi tercapai. Tiap putaran otomatis dicatat ke database
    (tabel agent_history) supaya bisa ditampilkan di dashboard.
    """
    import uuid

    api_key = cfg.GROQ_API_KEY
    if not api_key:
        log.warning("[agent_loop] GROQ_API_KEY belum diset, tidak bisa jalankan agent loop.")
        # Bentuk entri ini sengaja disamakan sama entri histori normal (ada key
        # "hasil" berisi dict) supaya format_ringkasan() tidak KeyError waktu
        # baca h["hasil"] — tanpa ini, error asli ketutup jadi "❌ Agent loop
        # error: 'hasil'" yang gak informatif buat user.
        return [{
            "putaran": 0,
            "tool": "error",
            "alasan": "GROQ_API_KEY belum diset di .env",
            "hasil": {"sukses": False, "error": "GROQ_API_KEY belum diset"},
        }]

    run_id  = uuid.uuid4().hex[:8]
    histori: list[dict] = []

    for putaran in range(1, max_iterasi + 1):
        log.info(f"[agent_loop] === Putaran {putaran}/{max_iterasi} (run={run_id}) ===")

        keputusan = _think(histori, api_key)
        nama_tool = keputusan.get("tool", "tidak_ada_aksi")
        alasan    = keputusan.get("alasan", "")
        selesai   = keputusan.get("selesai", False)

        log.info(f"[agent_loop] THINK -> pilih '{nama_tool}': {alasan}")

        hasil_eksekusi = tool_registry.eksekusi_tool(nama_tool)

        entri_histori = {
            "putaran": putaran,
            "tool": nama_tool,
            "alasan": alasan,
            "hasil": hasil_eksekusi,
        }
        histori.append(entri_histori)
        log.info(f"[agent_loop] OBSERVE -> {hasil_eksekusi}")

        try:
            db.log_agent_aksi(
                run_id=run_id, putaran=putaran, tool=nama_tool, alasan=alasan,
                hasil=hasil_eksekusi, sukses=hasil_eksekusi.get("sukses", False),
            )
        except Exception as e:
            log.warning(f"[agent_loop] Gagal catat ke database (tidak fatal): {e}")

        if nama_tool == "tidak_ada_aksi" or selesai:
            log.info(f"[agent_loop] Loop selesai di putaran {putaran} (AI memutuskan selesai).")
            break
    else:
        log.info(f"[agent_loop] Loop berhenti karena mencapai max_iterasi ({max_iterasi}).")

    return histori


def format_ringkasan(histori: list[dict]) -> str:
    """Format histori jadi teks ringkas buat dikirim ke Telegram."""
    if not histori:
        return "Tidak ada aksi yang diambil."

    aksi_nyata = [h for h in histori if h["tool"] != "tidak_ada_aksi"]
    if not aksi_nyata:
        return "🧠 Agent loop jalan, tapi tidak ada aksi yang diperlukan saat ini."

    baris = ["🧠 <b>Agent loop mengambil keputusan:</b>\n"]
    for h in aksi_nyata:
        status = "✅" if h["hasil"].get("sukses") else "⚠️"
        baris.append(f"{status} <b>{h['tool']}</b> — {h['alasan']}")
    return "\n".join(baris)


def main() -> None:
    args = sys.argv[1:]
    max_iter = 5
    if "--max-iterasi" in args:
        try:
            idx = args.index("--max-iterasi")
            max_iter = int(args[idx + 1])
        except (IndexError, ValueError):
            pass

    print("[agent_loop] Menjalankan Think-Act-Observe loop...")
    histori = jalankan_loop(max_iterasi=max_iter)

    print("\n" + "=" * 60)
    for h in histori:
        print(f"Putaran {h.get('putaran', '?')}: {h.get('tool')} — {h.get('alasan', '')}")
        print(f"  Hasil: {h.get('hasil')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
