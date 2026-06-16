# brian.oraghallaigh@dcu.ie

# Requires hunalign.exe & cygwin1.dll from LF Aligner in same directory

import argparse
import ast
import csv
import math
import os
import sys
from pathlib import Path
import requests
from wtpsplit import SaT
import xml.etree.ElementTree as ET
import zipfile
import time
from urllib.parse import quote

from gaoisalign_utils import (
	clean_for_xml,
	contains_unicode_letter,
	decode_xml_fadas,
	download_url,
	html_to_txt,
	reattach_orphan_markers,
	run_hunalign,
	xml_to_txt,
)

# Comment out to skip sentence splitting using SaT (EU):
sat = SaT("sat-3l-sm") # Select SaT Model for sentence segmentation. See: https://github.com/segment-any-text/wtpsplit

# Function to evaluate f-string variables within template string:
def fstr(template):
    return eval(f'f"""{template}"""')

jurisdiction = 'eu' # EDIT/CONFIGURE THIS LINE ('ie' or 'eu')
data_dir = r'C:\Users\nicanucm\Github\gaoisalign' # EDIT/CONFIGURE THIS LINE

parser = argparse.ArgumentParser(description='Run the gaoisalign pipeline.')
parser.add_argument('celex', nargs='*', help='Optional CELEX ID(s) to process, e.g. 32025L0001')
parser.add_argument(
	'--align-only',
	action='store_true',
	help='Skip fetch/segmentation; align existing _GA.txt / _EN.txt files only',
)
parser.add_argument(
	'--list-only',
	action='store_true',
	help='Build celex_list.txt via SOAP query and exit; do not fetch or align',
)
parser.add_argument(
	'--skip-aligned',
	action='store_true',
	help='Skip acts whose .tsv already exists in eu_bi/',
)
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
		act_num = str(int(file[1:3]))
		act_year = '20'+file[3:5]
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
	
	# Empty list = full 2025 SOAP query. Pass CELEX IDs on the CLI for test runs, e.g.:
	#   python gaoisalign.py 32025L0001 32025R0032
	celex_list = []
	if args.celex:
		celex_list = args.celex

	celex_list_path = os.path.join(data_dir, 'celex_list.txt')

	# Populate celex_list from cache or EUR-Lex SOAP API if empty:
	if not celex_list:
		if os.path.isfile(celex_list_path):
			if args.list_only:
				print(f'--list-only: {celex_list_path} already exists; exiting before fetch.')
				sys.exit(0)
			print(f'Loading CELEX list from {celex_list_path}...\n')
			with open(celex_list_path, 'r', encoding='utf-8') as f:
				celex_list = [line.strip() for line in f if line.strip()]
			print(f'  {len(celex_list)} CELEX IDs loaded\n')
		else:
			# Namespaces for EUR-Lex SOAP API:
			namespaces = {
				'soap': 'http://www.w3.org/2003/05/soap-envelope',
				'eurlex': 'http://eur-lex.europa.eu/search'
			}

			# URL and headers required for EUR-Lex SOAP API:
			url = "https://eur-lex.europa.eu/EURLexWebService"
			headers = {
				'Accept': 'text/xml, multipart/*',
				'Content-Type': 'application/soap+xml',
				'SOAPAction': 'https://eur-lex.europa.eu/EURLexWebService/doQuery'
			}

			# Expert query for EUR-Lex SOAP API. Create account on EUR-Lex and use GUI to build query:
			#expert_query = '(DTS_SUBDOM = ("LEGISLATION" OR "EFTA" OR "CONSLEG" OR "PRE_ACTS" OR "EU_CASE_LAW" OR "PAR_QUESTION" OR "LEGAL_PROCEDURE" OR "TREATIES" OR "INTER_AGREE") OR FM = ("BUDGET" OR "COMMUNIC" OR "CONCL" OR "DEC_DEL" OR "DIR_DEL" OR "REG_DEL" OR "PAPER_GREEN" OR "AGREE_INTERNATION" OR "RECO_DEC" OR "RECO_DIR" OR "RECO_REG" OR "RECO_RES" OR "PAPER_WHITE")) AND (PD >= 01/01/2025 <= 31/12/2025)' # ró-leathan [10,063 thoradh]
			expert_query = '((DTS_SUBDOM = "LEGISLATION") AND (PD >= 01/01/2025 <= 31/12/2025)) NOT FM_CODED = CORRIGENDUM' # níos cúinge [2,604 thoradh]
			#expert_query = '(FM = ("REG" OR "DIR" OR "DEC" OR "DEC_DEL" OR "DIR_DEL" OR "REG_DEL" OR "AGREE_INTERNATION")) AND (PD >= 01/01/2025 <= 31/12/2025) NOT FM_CODED = CORRIGENDUM'

			# Read credentials from file containing "{'usr': 'YOUR_EURLEX-USERNAME', 'pwd': 'YOUR_EURLEX-PASSWORD'}":
			with open('credentials.txt', 'r', encoding='utf-8') as f:
				credentials = ast.literal_eval(f.read())

			usr = credentials['usr']
			pwd = credentials['pwd']

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

			page = 1
			total_pages = None
			while total_pages is None or page <= total_pages:
				response = requests.request('POST', url, headers=headers, data=soap_body(page))
				root = ET.ElementTree(ET.fromstring(response.text)).getroot()
				if root is None:
					raise ValueError('EUR-Lex SOAP response did not include an XML root.')

				if total_pages is None:
					total_hits = root.find('./soap:Body/eurlex:searchResults/eurlex:totalhits', namespaces)
					if total_hits is None or total_hits.text is None:
						raise ValueError('EUR-Lex SOAP response did not include total hits.')
					total_pages = math.ceil(int(total_hits.text) / 100)
					print(f'  {total_hits.text} hits, {total_pages} page(s)')

				results = root.findall('./soap:Body/eurlex:searchResults/eurlex:result', namespaces)
				for result in results:
					celex_element = result.find('eurlex:content/eurlex:NOTICE/eurlex:WORK/eurlex:ID_CELEX/eurlex:VALUE', namespaces)
					celex = celex_element.text if celex_element is not None and celex_element.text is not None else ''
					if celex:
						celex_list.append(celex)
				print(f'  page {page}/{total_pages}: {len(results)} results')
				page += 1

			with open(celex_list_path, 'w', encoding='utf-8') as f:
				for celex in celex_list:
					f.write(celex + '\n')
			print(f'  Wrote {len(celex_list)} CELEX IDs to {celex_list_path}\n')
			if args.list_only:
				print('--list-only: exiting before fetch.')
				sys.exit(0)

	langs = ['GA', 'EN']
	batch = []

	for celex in celex_list:
		print(celex)
		aligned_file_check = os.path.join(data_dir, eu_bi_dir, celex + '.tsv')
		if args.skip_aligned and os.path.isfile(aligned_file_check):
			print(f'  skipping {celex} (aligned tsv already exists)')
			continue
		if args.align_only:
			file_ga_txt = os.path.join(data_dir, eu_ga_dir, celex + '_GA.txt')
			file_en_txt = os.path.join(data_dir, eu_en_dir, celex + '_EN.txt')
			if not os.path.isfile(file_ga_txt) or not os.path.isfile(file_en_txt):
				print(f'  WARNING: missing txt for {celex}, skipping')
				continue
			aligned_file = os.path.join(data_dir, eu_bi_dir, celex + '.tsv')
			batch.append((file_ga_txt, file_en_txt, aligned_file))
			continue
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
						data = x.text.replace(u'\xa0', ' ')
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
				with open(os.path.join(data_dir, eu_ga_dir, celex+'_'+lang+'.txt'), 'w', encoding='utf-8') as f:
					text_ga = html_to_txt(data)
					if sat:
						sentences_ga = [sentence_ga for sentence_ga in sat.split(text_ga) if sentence_ga.strip()]
						with open(os.path.join(data_dir, eu_ga_dir, celex+'_'+lang+'.raw.txt'), 'w', encoding='utf-8') as raw_f:
							for sentence_ga in sentences_ga:
								raw_f.write(sentence_ga.strip() + '\n')
						sentences_ga = reattach_orphan_markers(sentences_ga)
						for sentence_ga in sentences_ga:
							f.write(sentence_ga + '\n')
					else:
						f.write(text_ga)
			if lang == 'EN':
				with open(html_path, 'w', encoding='utf-8') as f:
					f.write(data)
				with open(os.path.join(data_dir, eu_en_dir, celex+'_'+lang+'.txt'), 'w', encoding='utf-8') as f:
					text_en = html_to_txt(data)
					if sat:
						sentences_en = [sentence_en for sentence_en in sat.split(text_en) if sentence_en.strip()]
						with open(os.path.join(data_dir, eu_en_dir, celex+'_'+lang+'.raw.txt'), 'w', encoding='utf-8') as raw_f:
							for sentence_en in sentences_en:
								raw_f.write(sentence_en.strip() + '\n')
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

else:
	raise ValueError(f"Unsupported jurisdiction {jurisdiction!r}; expected 'ie' or 'eu'.")

# Prepare xml and txt files:
print('\nPreparing alignment batch...\n')

# Write batch to file:
batch_lines = [('\t'.join(row)).strip() for row in batch]
batch_lines = [line for line in batch_lines if line]
# avoid trailing blank line at EOF
batch_text = '\n'.join(batch_lines).rstrip('\n')
with open(f'batch_{jurisdiction}.txt', 'wb') as f:
	f.write(batch_text.encode('utf-8'))

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
		en = entry.find('trg/scope/ortho')
		if ga is None or en is None:
			continue
		ga_string = ET.tostring(ga, encoding='unicode', method='text')
		en_string = ET.tostring(en, encoding='unicode', method='text')
		lines.append(f'{en_string.strip()} @ {ga_string.strip()}\n')
	except Exception:
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