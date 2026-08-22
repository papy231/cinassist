"""CinAssist — Audio/Video-Synchronisation bei der Ingestion (Take-Modell).

Deterministische Kette, kein LLM:
  bwf_ixml.py   — BWF-`bext`- + `iXML`-Chunk-Parser (Timecode, Szene/Take, Spurnamen)
  ltc.py        — LTC-Decoder (SMPTE 12M Biphase-Mark) + Kanal-Erkennung
  waveform.py   — FFT-Kreuzkorrelation (Stufe 2)
  matcher.py    — Kaskade Timecode → Wellenform → Klappe → Dateiname (Stufe 1–4)
  probe.py      — ffprobe-Wrapper + Ordner-Scan (Filter `._*` etc.)
"""
