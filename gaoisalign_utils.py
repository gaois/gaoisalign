"""Pure helper functions extracted from gaoisalign.py.

These have no module-level side effects, so they can be imported freely
from tests or other tools without triggering the end-to-end pipeline that
gaoisalign.py runs at import time.
"""

import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup
import requests

from scripts.partialAlign import partialAlignWithIO

HUNALIGN_PARTIAL_THRESHOLD = 8000
HUNALIGN_CHUNK_SIZE = 5000

_WS_RE = re.compile(r"\s+")

_LIST_MARKER_ONLY_RE = re.compile(
	r"""^\s*(?:
		\(\s*(?:[a-zA-Z]|\d{1,3}|[ivx]{1,5})\s*\) |
		\d{1,3}\s*[.)] |
		[—–-]+ |
		•+
	)\s*$""",
	re.IGNORECASE | re.VERBOSE,
)

_STRUCTURAL_HEADING_ONLY_RE = re.compile(
	r"""^\s*(?:
		(?:Article|Airteagal)\s+\d{1,3}[a-zA-Z]? |
		Definitions |
		Sainmhínithe
	)\s*$""",
	re.IGNORECASE | re.VERBOSE,
)


def normalize_space(s):
	return _WS_RE.sub(" ", s.strip())


def is_list_marker_only(s):
	if not s or not normalize_space(s):
		return False
	return _LIST_MARKER_ONLY_RE.match(normalize_space(s)) is not None


def is_structural_heading_only(s):
	if not s or not normalize_space(s):
		return False
	return _STRUCTURAL_HEADING_ONLY_RE.match(normalize_space(s)) is not None


def reattach_orphan_markers(sentences):
	cleaned = [s.strip() for s in sentences if s and s.strip()]
	reattached = []
	i = 0

	while i < len(cleaned):
		current = cleaned[i]
		if is_list_marker_only(current) and i + 1 < len(cleaned):
			next_sentence = cleaned[i + 1]
			if not is_list_marker_only(next_sentence) and not is_structural_heading_only(next_sentence):
				reattached.append(f'{current} {next_sentence}')
				i += 2
				continue
		reattached.append(current)
		i += 1

	return reattached


def contains_unicode_letter(s):
	# True if s has any character in Unicode general category "Letter" (any script).
	return any(unicodedata.category(ch).startswith("L") for ch in s)


# Funciton to decode accented character entities in source xml:
def decode_xml_fadas(text):
	text = re.sub(r'<Afada ?/>', 'Á', text)
	text = re.sub(r'<afada ?/>', 'á', text)
	text = re.sub(r'<Efada ?/>', 'É', text)
	text = re.sub(r'<efada ?/>', 'é', text)
	text = re.sub(r'<Ifada ?/>', 'Í', text)
	text = re.sub(r'<ifada ?/>', 'í', text)
	text = re.sub(r'<Ofada ?/>', 'Ó', text)
	text = re.sub(r'<ofada ?/>', 'ó', text)
	text = re.sub(r'<Ufada ?/>', 'Ú', text)
	text = re.sub(r'<ufada ?/>', 'ú', text)
	return text

# Function to replace xml special characters with xml entity references:
def clean_for_xml(segment):
	segment = segment.replace('<', '&lt;')
	segment = segment.replace('>', '&gt;')
	segment = segment.replace('&', '&amp;')
	segment = segment.replace('\'', '&apos;')
	segment = segment.replace('\"', '&quot;')
	return segment

# Function to get paragraphs of text from xml document (IE):
def xml_to_txt(data):
	data = decode_xml_fadas(data)
	soup = BeautifulSoup(data, features='xml')
	p_elements = soup.find_all('p')
	text = ''
	for p_element in p_elements:
		text = text + '\n' + p_element.get_text(separator='\n', strip=True).replace('\n', ' ')
	return text

# Function to get paragraphs of text from html document (EU):
def html_to_txt(data):
	soup = BeautifulSoup(data, features='xml')
	p_elements = soup.find_all('p')
	paras = []
	# TODO merge pars in same table row...
	for p_element in p_elements:
		p = p_element.get_text(separator='\n', strip=True).replace('\n', ' ')
		if p:
			paras.append(p)

	# Heuristic: for corrigenda-style EU docs, the meaningful bilingual content
	# often begins at the first numbered list item ("1. ..."). Keeping the
	# preceding boilerplate header can cause SaT to split "1. On ..." into a bare
	# "1." segment in some contexts, which then breaks hunalign alignment.
	for i, p in enumerate(paras):
		if re.match(r'^\d+\.\s+\S', p):
			paras = paras[i:]
			break

	return '\n' + '\n'.join(paras) if paras else ''

# Function to download large file in chunks:
def download_url(url, save_path, chunk_size=128):
	r = requests.get(url, stream=True)
	with open(save_path, 'wb') as fd:
		for chunk in r.iter_content(chunk_size=chunk_size):
			fd.write(chunk)


def count_nonempty_lines(path: Path | str) -> int:
	path = Path(path)
	count = 0
	with open(path, encoding="utf-8") as f:
		for line in f:
			if line.strip():
				count += 1
	return count


def _repo_root() -> Path:
	return Path(__file__).resolve().parent


def _default_hunalign_exe() -> Path:
	return _repo_root() / "hunalign.exe"


def _path_for_hunalign(path: Path) -> str:
	# Cygwin hunalign on Windows is sensitive to backslashes in batch files.
	return path.resolve().as_posix()


def _run_hunalign_batch(
	hunalign_exe: Path,
	dic_path: Path,
	batch_path: Path,
	cwd: Path,
) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		[
			str(hunalign_exe),
			str(dic_path),
			"-text",
			"-utf",
			"-bisent",
			"-realign",
			"-batch",
			str(batch_path),
		],
		cwd=str(cwd),
		capture_output=True,
		text=True,
		encoding="utf-8",
		errors="replace",
	)


def _parse_batch_output_paths(batch_text: str) -> list[Path]:
	paths: list[Path] = []
	for line in batch_text.strip().splitlines():
		parts = line.split("\t")
		if len(parts) >= 3:
			paths.append(Path(parts[2]))
	return paths


def _verify_hunalign_outputs(
	proc: subprocess.CompletedProcess[str],
	output_paths: list[Path],
	label: str,
) -> None:
	missing = [p for p in output_paths if not p.is_file() or p.stat().st_size == 0]
	if missing:
		raise RuntimeError(
			f"hunalign produced empty or missing output for {label}:\n"
			+ "\n".join(str(p) for p in missing)
			+ f"\nstderr:\n{proc.stderr}"
		)
	if proc.returncode != 0:
		raise RuntimeError(
			f"hunalign failed for {label} (exit {proc.returncode}).\n"
			f"stderr:\n{proc.stderr}"
		)


def _stitch_align_files(chunk_paths: list[Path], out_tsv: Path) -> None:
	out_tsv.parent.mkdir(parents=True, exist_ok=True)
	with open(out_tsv, "w", encoding="utf-8") as out_f:
		for chunk_path in chunk_paths:
			content = chunk_path.read_text(encoding="utf-8")
			if not content.strip():
				raise RuntimeError(f"empty chunk alignment file: {chunk_path}")
			out_f.write(content)
			if not content.endswith("\n"):
				out_f.write("\n")


def _partial_align_batch_lines(stdout: str) -> tuple[list[str], list[Path]]:
	batch_lines: list[str] = []
	chunk_out_paths: list[Path] = []
	for line in stdout.strip().splitlines():
		if not line.strip():
			continue
		ga_chunk, en_chunk, out_align = line.split("\t")
		out_tsv_chunk = out_align.replace(".align", ".tsv")
		ga_p = _path_for_hunalign(Path(ga_chunk))
		en_p = _path_for_hunalign(Path(en_chunk))
		out_p = _path_for_hunalign(Path(out_tsv_chunk))
		batch_lines.append(f"{ga_p}\t{en_p}\t{out_p}")
		chunk_out_paths.append(Path(out_tsv_chunk))
	return batch_lines, chunk_out_paths


def run_hunalign(
	ga_path: Path | str,
	en_path: Path | str,
	out_tsv: Path | str,
	dic_path: Path | str,
	hunalign_exe: Path | str | None = None,
	*,
	threshold: int = HUNALIGN_PARTIAL_THRESHOLD,
	chunk_size: int = HUNALIGN_CHUNK_SIZE,
) -> subprocess.CompletedProcess[str]:
	"""Align one GA/EN sentence file pair with hunalign, using partialAlign when large.

	Uses ``partialAlign`` (chunk + batch + stitch) when
	``max(line_count(ga), line_count(en)) > threshold`` (default 8000). Otherwise
	runs hunalign on the full pair in a single batch job.

	Environment variables:

	``GAOISALIGN_KEEP_CHUNKS``
	    If set to any non-empty value (e.g. ``1``), intermediate chunk files,
	    the hunalign chunk batch file, and the ``.poset`` anchor log are kept under
	    ``{parent_of_out_tsv}/_chunks/{celex}/`` instead of a temporary directory
	    that is deleted when alignment finishes. Use this to inspect chunk
	    boundaries and partialAlign anchor decisions when debugging large acts.

	Raises ``RuntimeError`` on hunalign failure or empty output.
	"""
	ga_path = Path(ga_path)
	en_path = Path(en_path)
	out_tsv = Path(out_tsv)
	dic_path = Path(dic_path)
	hunalign_exe = Path(hunalign_exe) if hunalign_exe else _default_hunalign_exe()
	cwd = _repo_root()
	label = out_tsv.stem

	n = max(count_nonempty_lines(ga_path), count_nonempty_lines(en_path))
	ga_abs = _path_for_hunalign(ga_path)
	en_abs = _path_for_hunalign(en_path)
	out_abs = _path_for_hunalign(out_tsv)

	if n <= threshold:
		with tempfile.NamedTemporaryFile(
			mode="w",
			suffix=".batch",
			delete=False,
			encoding="utf-8",
			newline="\n",
			dir=cwd,
		) as batch_f:
			batch_f.write(f"{ga_abs}\t{en_abs}\t{out_abs}")
			batch_file = Path(batch_f.name)
		try:
			proc = _run_hunalign_batch(hunalign_exe, dic_path, batch_file, cwd)
			_verify_hunalign_outputs(proc, [out_tsv], label)
			return proc
		finally:
			batch_file.unlink(missing_ok=True)

	keep_chunks = bool(os.environ.get("GAOISALIGN_KEEP_CHUNKS", "").strip())
	chunk_parent = out_tsv.parent / "_chunks" / label
	if keep_chunks:
		chunk_parent.mkdir(parents=True, exist_ok=True)
		output_basename = str((chunk_parent / label).resolve())
		tmp_ctx = None
	else:
		tmp_ctx = tempfile.TemporaryDirectory(prefix="gaoisalign-chunks-", dir=cwd)
		output_basename = str((Path(tmp_ctx.name) / label).resolve())

	try:
		_chain, stdout = partialAlignWithIO(
			_path_for_hunalign(ga_path),
			_path_for_hunalign(en_path),
			_path_for_hunalign(Path(output_basename)),
			"ga",
			"en",
			chunk_size,
		)
		if not stdout.strip():
			raise RuntimeError(f"partialAlign produced no batch jobs for {label}")

		batch_lines, chunk_out_paths = _partial_align_batch_lines(stdout)
		chunk_count = len(batch_lines)
		batch_file = Path(f"{output_basename}.hunalign.batch.txt")
		batch_file.write_text("\n".join(batch_lines), encoding="utf-8", newline="\n")

		print(f"  {label}: partialAlign -> {chunk_count} chunk(s), running hunalign...")
		proc = _run_hunalign_batch(hunalign_exe, dic_path, batch_file, cwd)
		_verify_hunalign_outputs(proc, chunk_out_paths, label)
		_stitch_align_files(chunk_out_paths, out_tsv)
		if not out_tsv.is_file() or out_tsv.stat().st_size == 0:
			raise RuntimeError(f"stitched TSV is empty for {label}")
		return proc
	finally:
		if tmp_ctx is not None:
			tmp_ctx.cleanup()
