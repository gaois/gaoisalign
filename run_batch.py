# run_batch.py — read CELEX IDs from a file and pass them to gaoisalign.py
# Usage: python run_batch.py day1.txt
import subprocess
import sys

if len(sys.argv) < 2:
	print('Usage: python run_batch.py <celex_id_file> [extra args for gaoisalign.py]')
	sys.exit(1)

batch_file = sys.argv[1]
extra_args = sys.argv[2:]

with open(batch_file, 'r', encoding='utf-8') as f:
	ids = [line.strip() for line in f if line.strip()]

print(f'Running gaoisalign.py on {len(ids)} CELEX IDs from {batch_file}')
subprocess.run([sys.executable, 'gaoisalign.py', *ids, *extra_args])
