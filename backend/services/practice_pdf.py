"""Bounded PDF ingestion subprocess; no credentials, file persistence or network."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_BYTES=5*1024*1024


def read_pdf(value):
    if not value or len(value)>MAX_BYTES or not value.startswith(b'%PDF-'):raise ValueError('Upload a PDF up to 5 MB.')
    safe={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--extract'],input=value,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,env=safe)
    if result.returncode or len(result.stdout)>300000:raise ValueError('PDF could not be read. Paste text instead; scanned or encrypted files are unsupported.')
    data=json.loads(result.stdout)
    if len(data['text'].strip())<30:raise ValueError('No readable PDF text found. Paste notes instead; OCR is not available.')
    return data


def main():
    import pdfplumber
    value=sys.stdin.buffer.read(MAX_BYTES+1)
    if len(value)>MAX_BYTES or not value.startswith(b'%PDF-'):raise ValueError()
    pieces=[];warnings=[];size=0
    with pdfplumber.open(BytesIO(value)) as document:
        if len(document.pages)>100:raise ValueError()
        for index,page in enumerate(document.pages,1):
            body=page.extract_text() or ''
            if not body.strip():warnings.append('Page '+str(index)+': no readable text; diagrams/scans are not interpreted.')
            piece='Page '+str(index)+'\n'+body
            if size+len(piece)+(2 if pieces else 0)>30000:
                warnings.append('Extraction truncated at 30,000 characters. Select the relevant section.');break
            size+=len(piece)+(2 if pieces else 0);pieces.append(piece)
    text='\n\n'.join(pieces)
    # Page labels alone are not evidence of usable text.
    if not any(len(p.split('\n',1)[-1].strip())>=30 for p in pieces):raise ValueError()
    print(json.dumps({'text':text,'warnings':warnings[:20]+['PDF diagrams and reading order may be incomplete; original binary is not retained.']}))


if __name__=='__main__':
    try:main()
    except Exception:sys.exit(1)
