# gaoisalign
A script to align Irish and English parallel texts

## Changes (2025 update)

### Fixes
- SOAP paging: each page is now requested (was page 1 repeatedly).
- `celex_list.txt` cache is now loaded when present.
- `clean_for_xml`: `&` escaped first.

### EU pipeline
- CELEX IDs can be passed as arguments: `python gaoisalign.py 32025R0327 32025R1534`. No arguments runs the cached list or SOAP query.
- Documents fetched from the Cellar API (`publications.europa.eu/resource/celex/{id}`), with retry on 202 and URL-quoted IDs.
- Existing `.txt`/`.html` files are reused.
- Acts missing GA or EN text are skipped.
- Empty `<p>` elements and empty SaT segments are dropped.
- Orphan list markers (`(a)`, `1.`, `—`, `•`) are joined to the following segment.

### Alignment
- hunalign runs per file pair; failures are logged and skipped.
- Files over 8,000 lines are chunked with `scripts/partialAlign.py`.
- `batch_{jurisdiction}.txt` replaced by a temporary batch file per pair.

### TMX
- Written only for files aligned in the current run; bad `.tsv` files are skipped.
- `regex` dependency replaced with `unicodedata`.

### Notes
- `scripts/partialAlign.py` is a Python 3 port of hunalign's `partialAlign.py` (Daniel Varga, LGPL-3.0).
