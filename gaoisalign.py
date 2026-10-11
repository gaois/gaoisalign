# brian.oraghallaigh@dcu.ie

# Requires hunalign.exe & cygwin1.dll from LF Aligner in same directory

import argparse
import ast
from bs4 import BeautifulSoup # pip install lxml
import datetime
import math
import os
from pathlib import Path
import re
import requests
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import quote
from wtpsplit import SaT # pip install torch
import xml.etree.ElementTree as ET
import zipfile

from scripts.partialAlign import partialAlignWithIO

# Comment out to skip sentence splitting using SaT (EU):
sat = SaT("sat-3l-sm") # Select SaT Model for sentence segmentation. See: https://github.com/segment-any-text/wtpsplit

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
	segment = segment.replace('&', '&amp;') # Must come first so the entities below are not double-escaped
	segment = segment.replace('<', '&lt;')
	segment = segment.replace('>', '&gt;')
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
	text = ''
	# TODO merge pars in same table row...
	for p_element in p_elements:
		p = p_element.get_text(separator='\n', strip=True).replace('\n', ' ')
		# Skip empty paragraphs:
		if p:
			text = text + '\n' + p
	return text

# Function to download large file in chunks:
def download_url(url, save_path, chunk_size=128):
	r = requests.get(url, stream=True)
	with open(save_path, 'wb') as fd:
		for chunk in r.iter_content(chunk_size=chunk_size):
			fd.write(chunk)

# Function to evaluate f-string variables within template string:
def fstr(template):
    return eval(f'f"""{template}"""')

# Functions to reattach list markers that SaT splits off as their own segment (e.g. "(a)", "1.", "—"):
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

# Function to check for any character in Unicode general category "Letter" (any script):
def contains_unicode_letter(s):
	return any(unicodedata.category(ch).startswith("L") for ch in s)

# Functions to run hunalign per file, using partialAlign for large files:
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
		tmp_ctx.cleanup()

jurisdiction = 'eu' # EDIT/CONFIGURE THIS LINE ('ie' or 'eu')
data_dir = r'C:\Users\oraghab\Documents\BOR\zzz\gaoisalign' # EDIT/CONFIGURE THIS LINE

# Optional CELEX IDs on the command line (EU), e.g.: python gaoisalign.py 32025L0001 32025R0032
parser = argparse.ArgumentParser(description='Run the gaoisalign pipeline.')
parser.add_argument('celex', nargs='*', help='Optional CELEX ID(s) to process, e.g. 32025L0001')
args = parser.parse_args()

if jurisdiction == 'ie':

	# Directories where data files will be stored locally:
	ie_ga_dir = 'ie_ga' # From Rannóg an Aistriúcháin -- Create automatically then populate MANUALLY (when prompted)
	ie_en_dir = 'ie_en' # From Irish Statute Book (eISB) -- Create and populate automatically
	ie_bi_dir = 'ie_bi' # For aligned data -- Create and populate automatically

	# Create above directories if they do not already exist:
	Path(data_dir).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, ie_ga_dir)).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, ie_en_dir)).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, ie_bi_dir)).mkdir(parents=True, exist_ok=True)

	input(f'\nPut the Irish-language XML files you wish to align into "{data_dir}\\{ie_ga_dir}" and press Enter...\n')

	# Prepare xml and txt files:
	print('Preparing xml and txt files...\n')

	# Create list of ie_ga xml files:
	files_ga_xml = []
	for file in os.listdir(os.path.join(data_dir, ie_ga_dir)):
		if file.endswith('.xml'):
			files_ga_xml.append(file)

	batch = []

	# For each ie_ga xml file:
	for file in files_ga_xml:
		print(file)
		file_no_extension, file_extension = os.path.splitext(file)
		file_ga_xml = os.path.join(data_dir, ie_ga_dir, file_no_extension+file_extension)
		file_ga_txt = os.path.join(data_dir, ie_ga_dir, file_no_extension+'.txt')
		file_en_xml = os.path.join(data_dir, ie_en_dir, file_no_extension+file_extension)
		file_en_txt = os.path.join(data_dir, ie_en_dir, file_no_extension+'.txt')
		
		# Generate ie_ga txt file from xml source:
		# Read xml data:
		with open(file_ga_xml, 'r', encoding='utf-8') as f:
			data = f.read()
			text_ga = ''
			text_ga = xml_to_txt(data)
		# Write txt data:
		with open(file_ga_txt, 'w', encoding='utf-8') as f:
			f.write(text_ga)

		# Get ie_en xml file from IrishStatuteBook.ie:
		num_year_padded = re.findall(r'(\d+)', file)[0].zfill(4)
		act_num = str(int(num_year_padded[:2]))
		if int(num_year_padded[2:]) > int(datetime.datetime.now().strftime("%y")):
			act_year = '19'+num_year_padded[2:]
		else:
			act_year = '20'+num_year_padded[2:]
		# e.g. https://www.irishstatutebook.ie/eli/2018/act/7/enacted/en/xml
		url = f'https://www.irishstatutebook.ie/eli/{act_year}/act/{act_num}/enacted/en/xml'
		x = requests.get(url)
		with open(file_en_xml, 'w', encoding='utf-8') as f:
			f.write(x.text)
		
		# Generate ie_en txt file from xml source:
		with open(file_en_txt, 'w', encoding='utf-8') as f:
			data = x.text
			text_en = ''
			text_en = xml_to_txt(data)
			f.write(text_en)
		
		# Build batch for hunalign:
		aligned_file = os.path.join(data_dir, ie_bi_dir, file_no_extension+'.tsv')
		batch.append((file_ga_txt, file_en_txt, aligned_file))

elif jurisdiction == 'eu':
	
	# Directories where data files will be stored locally:
	eu_ga_dir = 'eu_ga' # From EUR-Lex -- Create and populate automatically
	eu_en_dir = 'eu_en' # From EUR-Lex -- Create and populate automatically
	eu_bi_dir = 'eu_bi' # For aligned data -- Create and populate automatically

	# Create above directories if they do not already exist:
	Path(data_dir).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, eu_ga_dir)).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, eu_en_dir)).mkdir(parents=True, exist_ok=True)
	Path(os.path.join(data_dir, eu_bi_dir)).mkdir(parents=True, exist_ok=True)
	
	# Prepare html and txt files:
	print('Preparing html and txt files...\n')
	
	# Empty list = full 2025 SOAP query. Pass CELEX IDs on the command line for test runs.
	celex_list = args.celex

	celex_list_path = os.path.join(data_dir, 'celex_list.txt')

	# Populate celex_list from cache or EUR-Lex SOAP API if empty:
	if not celex_list:

		# Load cached results if available:
		if os.path.isfile(celex_list_path):
			print(f'Loading CELEX list from {celex_list_path}...\n')
			with open(celex_list_path, 'r', encoding='utf-8') as f:
				celex_list = [line.strip() for line in f if line.strip()]

		else:
			# Namespaces for EUR-Lex SOAP API:
			namespaces = {
				'soap': 'http://www.w3.org/2003/05/soap-envelope',
				'eurlex': 'http://eur-lex.europa.eu/search'
			}

			# URL and headers required for EUR-Lex SOAP API:
			url="https://eur-lex.europa.eu/EURLexWebService"
			headers = {
				'Accept': 'text/xml, multipart/*',
				'Content-Type': 'application/soap+xml',
				'SOAPAction': 'https://eur-lex.europa.eu/EURLexWebService/doQuery'
			}

			# Expert query for EUR-Lex SOAP API. Create account on EUR-Lex and use GUI to build query:
			#expert_query = '(DTS_SUBDOM = ("LEGISLATION" OR "EFTA" OR "CONSLEG" OR "PRE_ACTS" OR "EU_CASE_LAW" OR "PAR_QUESTION" OR "LEGAL_PROCEDURE" OR "TREATIES" OR "INTER_AGREE") OR FM = ("BUDGET" OR "COMMUNIC" OR "CONCL" OR "DEC_DEL" OR "DIR_DEL" OR "REG_DEL" OR "PAPER_GREEN" OR "AGREE_INTERNATION" OR "RECO_DEC" OR "RECO_DIR" OR "RECO_REG" OR "RECO_RES" OR "PAPER_WHITE")) AND (PD >= 01/01/2025 <= 31/12/2025)' # ró-leathan [10,063 thoradh]
			expert_query = '((DTS_SUBDOM = "LEGISLATION") AND (PD >= 01/01/2025 <= 31/12/2025)) NOT FM_CODED = CORRIGENDUM' # níos cúinge [2,604 thoradh]

			# Read credentials from file containing "{'usr': 'YOUR_EURLEX-USERNAME', 'pwd': 'YOUR_EURLEX-PASSWORD'}":
			with open('credentials.txt', 'r', encoding='utf-8') as data:
				credentials = ast.literal_eval(data.read())

			# Credentials for EUR-Lex SOAP API:
			usr = credentials['usr']
			pwd = credentials['pwd']

			# Envelope for EUR-Lex SOAP API (built per page so each request asks for the right page):
			def soap_body(page_num):
				return f"""<?xml version="1.0" encoding="UTF-8"?>
		<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:sear="http://eur-lex.europa.eu/search">
		  <soap:Header>
			<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" soap:mustUnderstand="true">
			  <wsse:UsernameToken xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" wsu:Id="UsernameToken-1">
				<wsse:Username>{usr}</wsse:Username>
				<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">{pwd}</wsse:Password>
			  </wsse:UsernameToken>
			</wsse:Security>
		  </soap:Header>
		  <soap:Body>
			<sear:searchRequest>
			  <sear:expertQuery><![CDATA[{expert_query}]]></sear:expertQuery>
			  <sear:page>{page_num}</sear:page>
			  <sear:pageSize>100</sear:pageSize>
			  <sear:searchLanguage>en</sear:searchLanguage>
			  <sear:showDocumentsAvailableIn>ga</sear:showDocumentsAvailableIn>
			  <sear:excludeAllConsleg>false</sear:excludeAllConsleg>
			  <sear:limitToLatestConsleg>false</sear:limitToLatestConsleg>
			</sear:searchRequest>
		  </soap:Body>
		</soap:Envelope>"""

			print('Getting celex numbers of search results...')

			# Loop to make request for each page (total pages calculated from first response):
			page = 1
			total_pages = None
			while total_pages is None or page <= total_pages:

				# Make request to EUR-Lex SOAP API for current page:
				response = requests.request('POST', url, headers=headers, data=soap_body(page))
				root = ET.ElementTree(ET.fromstring(response.text)).getroot()

				# Find total hits and calculate total pages (rounded up):
				if total_pages is None:
					total_hits = root.find('./soap:Body/eurlex:searchResults/eurlex:totalhits', namespaces)
					total_pages = math.ceil(int(total_hits.text)/100)

				# Get results from current page:
				results = root.findall('./soap:Body/eurlex:searchResults/eurlex:result', namespaces)

				# Get celex from each result on page and add to celex_list:
				for result in results:
					try:
						celex = result.find('eurlex:content/eurlex:NOTICE/eurlex:WORK/eurlex:ID_CELEX/eurlex:VALUE', namespaces).text
					except:
						celex = ''
					if celex:
						celex_list.append(celex)
				page += 1

			# Cache results to file:
			with open(celex_list_path, 'w', encoding='utf-8') as f:
				for celex in celex_list:
					f.write(celex + '\n')

	langs = ['GA', 'EN']
	batch = []

	for celex in celex_list:
		print(celex)
		for lang in langs:
			html_dir = eu_ga_dir if lang == 'GA' else eu_en_dir
			html_path = os.path.join(data_dir, html_dir, celex+'_'+lang+'.html')
			txt_path = os.path.join(data_dir, html_dir, celex+'_'+lang+'.txt')
			if os.path.isfile(txt_path) and os.path.getsize(txt_path) > 0:
				print(f'  skipping {celex} {lang} (txt already exists)')
				continue
			cellar_url = f'https://publications.europa.eu/resource/celex/{quote(celex)}'
			data = ''
			if os.path.isfile(html_path) and os.path.getsize(html_path) > 500:
				print(f'  using existing HTML for {celex} {lang}')
				with open(html_path, 'r', encoding='utf-8') as f:
					data = f.read()
			else:
				s = requests.Session()
				req_headers = {
					'Accept': 'application/xhtml+xml',
					'Accept-Language': lang.lower(),
				}
				for attempt in range(8):
					x = s.get(cellar_url, headers=req_headers, allow_redirects=True)
					print(f'  {celex} {lang} attempt {attempt+1}: status={x.status_code}, length={len(x.text)}')
					if x.status_code == 200 and len(x.text) > 500:
						data = x.text.replace(u'\xa0', ' ') # Replace non-breaking space character
						break
					elif x.status_code == 202:
						wait = int(x.headers.get('Retry-After', 5))
						print(f'  202 — waiting {wait}s...')
						time.sleep(wait)
					else:
						print(f'  unexpected status {x.status_code}')
						break
				# TODO: On-disk HTML fallback below has no freshness check (ETag, Last-Modified,
				# or max-age) vs EUR-Lex; after exhausting 200/202 retries we may reuse stale XHTML.
				if not data:
					if os.path.isfile(html_path):
						print(f'  using cached HTML for {celex} {lang}')
						with open(html_path, 'r', encoding='utf-8') as f:
							data = f.read()
					else:
						print(f'  WARNING: no content retrieved for {celex} {lang}, skipping')
						continue
			if lang == 'GA':
				with open(html_path, 'w', encoding='utf-8') as f:
					f.write(data)
				with open(txt_path, 'w', encoding='utf-8') as f:
					text_ga = ''
					text_ga = html_to_txt(data)
					if sat:
						# Split paragraphs into sentences using SaT, drop empty segments, reattach orphan list markers:
						sentences_ga = [sentence_ga for sentence_ga in sat.split(text_ga) if sentence_ga.strip()]
						sentences_ga = reattach_orphan_markers(sentences_ga)
						for sentence_ga in sentences_ga:
							f.write(sentence_ga + '\n')
					else:
						f.write(text_ga)
			if lang == 'EN':
				with open(html_path, 'w', encoding='utf-8') as f:
					f.write(data)
				with open(txt_path, 'w', encoding='utf-8') as f:
					text_en = ''
					text_en = html_to_txt(data)
					if sat:
						# Split paragraphs into sentences using SaT, drop empty segments, reattach orphan list markers:
						sentences_en = [sentence_en for sentence_en in sat.split(text_en) if sentence_en.strip()]
						sentences_en = reattach_orphan_markers(sentences_en)
						for sentence_en in sentences_en:
							f.write(sentence_en + '\n')
					else:
						f.write(text_en)
		
		file_ga_txt = os.path.join(data_dir, eu_ga_dir, celex+'_GA.txt')
		file_en_txt = os.path.join(data_dir, eu_en_dir, celex+'_EN.txt')
		if not os.path.isfile(file_ga_txt) or not os.path.isfile(file_en_txt):
			print(f'  WARNING: missing txt for {celex}, skipping alignment')
			continue
		aligned_file = os.path.join(data_dir, eu_bi_dir, celex+'.tsv')
		batch.append((file_ga_txt, file_en_txt, aligned_file))

# Build dictionary for hunalign:
print('Building dictionary...\n')

# Create directory for dictionary:
dic_dir = 'dic'
Path(os.path.join(data_dir, dic_dir)).mkdir(parents=True, exist_ok=True)

# Build path to save Pota Focal glossary (https://github.com/michmech/pota-focal-gluais):
save_path = os.path.join(data_dir, dic_dir, 'potafocal.xml')
# Check that file has not already been downloaded:
if not os.path.isfile(save_path):
	# Download glossary:
	x = requests.get('https://raw.githubusercontent.com/michmech/pota-focal-gluais/refs/heads/master/lexicon.xml')
	with open(save_path, 'w', encoding='utf-8') as f:
		f.write(x.text)

# Parse Pota Focal glossary:
import xml.etree.ElementTree as ET
potafocal_root = ET.parse(save_path).getroot() # https://github.com/michmech/pota-focal-gluais

# List of lines for new ga hunalign dic:
lines = []

# Add Pota Focal entries to list of lines for new ga hunalign dic:
for entry in potafocal_root.findall('entry'):
	try:
		ga = entry.find('src/scope/ortho')
		ga_string = ET.tostring(ga, encoding='unicode', method='text')
		en = entry.find('trg/scope/ortho')
		en_string = ET.tostring(en, encoding='unicode', method='text')
		lines.append(f'{en_string.strip()} @ {ga_string.strip()}\n')
	except:
		continue

# Download Téarma zipped txt termbase (https://www.tearma.ie/ioslodail/):
url = 'https://www.tearma.ie/ioslodail/25.04.01-tearma.ie-concepts.txt.zip'
save_path = os.path.join(data_dir, dic_dir, 'tearma.zip')
# Check that file has not already been downloaded:
if not os.path.isfile(save_path):
	download_url(url, save_path, chunk_size=128)

# Unzip Téarma termbase and save as txt:
zip = zipfile.ZipFile(os.path.join(data_dir, dic_dir, 'tearma.zip'))
file = zip.read('25.04.01-tearma.ie-concepts.txt')
with open(os.path.join(data_dir, dic_dir, 'tearma.txt'), 'wb') as f:
	f.write(file)

# Read lines from Téarma txt file:
with open(os.path.join(data_dir, dic_dir, 'tearma.txt'), 'r', encoding='utf-8') as f:
	tearma_data = f.readlines()

# Add Téarma entries to list of lines for new ga hunalign dic:
for line in tearma_data:
	en = line.split('\t')[0]
	ga = line.split('\t')[1]
	lines.append(f'{en.strip()} @ {ga.strip()}\n')

# Remove duplicates and sort:
lines_unique_sorted = sorted(list(set(lines)))

# Create new ga hunalign dic and write lines from Pota Focal glossary and Téarma termbase to it:
with open(os.path.join(data_dir, dic_dir, 'ga-en.dic'), 'w', encoding='utf-8') as f:
	for line in lines_unique_sorted:
		f.write(line)

# Align files with hunalign:
print('Aligning files...\n')

# Call hunalign on each file pair (See: https://github.com/danielvarga/hunalign):
dic_path = os.path.join(data_dir, dic_dir, 'ga-en.dic')
hunalign_exe = os.path.join(data_dir, 'hunalign.exe')
for file_ga_txt, file_en_txt, aligned_file in batch:
	print(os.path.basename(aligned_file))
	try:
		proc = run_hunalign(file_ga_txt, file_en_txt, aligned_file, dic_path, hunalign_exe)
		if proc.stderr:
			sys.stdout.buffer.write(proc.stderr.rstrip().encode("utf-8", errors="replace") + b"\n")
	except Exception as e:
		print(f'  WARNING: alignment failed for {os.path.basename(aligned_file)}: {e}')
		continue

# Write aligned data to tmx format:
print('\nWriting aligned data to tmx format...')

# TMX markup prefix:
tmx_frame_prefix = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE tmx SYSTEM "tmx14.dtd">
<tmx version="1.4">

<header creationtool="gaoisalign.py" creationtoolversion="1.0" segtype="sentence" o-tmf="tsv" adminlang="ga" srclang="ga" datatype="plaintext">
	<prop type="name">{file}</prop>
</header>

<body>
"""

# TMX markup suffix:
tmx_frame_suffix = """
</body>
</tmx>
"""

# TMX translation unit markup:
tu_frame = """
<tu>
	<tuv xml:lang="ga">
		<seg>
		{ga}
		</seg>
	</tuv>
	<tuv xml:lang="en">
		<seg>
		{en}
		</seg>
	</tuv>
</tu>
"""

# Only write TMX for files aligned in this run:
files = [os.path.basename(row[2]) for row in batch]
files = [x for x in files if '.tsv' in x] # Filter filelist

for file in files:
	file_no_extension, file_extension = os.path.splitext(file)
	tsv_path = os.path.join(data_dir, f'{jurisdiction}_bi', file)
	if not os.path.isfile(tsv_path):
		print(f'  WARNING: missing tsv for {file}, skipping TMX')
		continue
	try:
		with open(tsv_path, 'r', encoding='utf-8') as f:
			data = f.readlines()
		with open(os.path.join(data_dir, f'{jurisdiction}_bi', file_no_extension+'.tmx'), 'w', encoding='utf-8') as f:
			f.write(fstr(tmx_frame_prefix))
			for line in data:
				ga = clean_for_xml(line.split('\t')[0])
				en = clean_for_xml(line.split('\t')[1])
				# Filter empty and non-letter results (any Unicode script):
				if ga and contains_unicode_letter(ga):
					f.write(fstr(tu_frame))
			f.write(fstr(tmx_frame_suffix))
	except Exception as e:
		print(f'  WARNING: TMX writing failed for {file}: {e}')
		continue
