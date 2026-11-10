"""Bounded text-only parser. No original files are persisted or executed."""
import resource
resource.setrlimit(resource.RLIMIT_AS, (192*1024**2, 192*1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
import io
import json
import sys
import zipfile

MAX_TEXT = 40000
try:
    data = sys.stdin.buffer.read(5*1024**2+1)
    if len(data)>5*1024**2: raise ValueError('El documento supera los 5 MB.')
    extension = sys.argv[1]
    if extension in {'.txt', '.md', '.csv'}:
        text = data.decode('utf-8-sig')
        if '\x00' in text: raise ValueError('El archivo no es texto UTF-8 válido.')
    elif extension == '.docx':
        from defusedxml.ElementTree import fromstring
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries)>1000 or sum(e.file_size for e in entries)>16*1024**2:
                raise ValueError('El documento comprimido es demasiado grande.')
            if any('vbaproject' in e.filename.lower() for e in entries):
                raise ValueError('No se permiten documentos con macros.')
            entry = archive.getinfo('word/document.xml')
            if entry.file_size>4*1024**2: raise ValueError('El documento es demasiado complejo.')
            root = fromstring(archive.read(entry))
            paragraphs=[]
            for node in root.iter():
                if node.tag.rsplit('}',1)[-1]=='p':
                    paragraphs.append(''.join(t.text or '' for t in node.iter() if t.tag.rsplit('}',1)[-1]=='t'))
            text='\n'.join(paragraphs)
    elif extension == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted: raise ValueError('Desbloquea el PDF antes de adjuntarlo.')
        if len(reader.pages)>50: raise ValueError('El PDF supera las 50 páginas.')
        parts=[];count=0
        for page in reader.pages:
            part=page.extract_text() or '';count+=len(part)
            if count>MAX_TEXT: raise ValueError('El documento supera los 40.000 caracteres de texto.')
            parts.append(part)
        text='\n'.join(parts)
    else: raise ValueError('Formato no admitido. Usa PDF, DOCX, TXT, MD o CSV.')
    text=text.strip()
    if not text: raise ValueError('No se encontró texto. Los PDF escaneados necesitan OCR antes de adjuntarlos.')
    if len(text)>MAX_TEXT: raise ValueError('El documento supera los 40.000 caracteres de texto.')
    print(json.dumps({'text':text},ensure_ascii=False))
except ValueError as error:
    print(json.dumps({'error':str(error)}))
except Exception:
    print(json.dumps({'error':'No se pudo leer el documento. Revisa el formato o exporta una copia en texto.'}))
