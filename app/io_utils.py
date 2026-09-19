from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from core import normalize_row


def load_file(path: str, fallback_platform='Unknown'):
    p=Path(path)
    if p.suffix.lower()=='.csv':
        df=pd.read_csv(p)
        rows=df.to_dict(orient='records')
    elif p.suffix.lower() in ('.json','.jsonl'):
        if p.suffix.lower()=='.jsonl':
            rows=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
        else:
            data=json.loads(p.read_text(encoding='utf-8'))
            rows=data if isinstance(data,list) else data.get('items', data.get('data', []))
    elif p.suffix.lower() in ('.xlsx','.xls'):
        df=pd.read_excel(p); rows=df.to_dict(orient='records')
    else:
        raise ValueError(f'Unsupported file type: {p.suffix}')
    return [normalize_row(r, fallback_platform) for r in rows]


def load_uploaded(uploaded, fallback_platform='Unknown'):
    name=uploaded.name.lower()
    raw=uploaded.getvalue()
    if name.endswith('.csv'):
        import io
        df=pd.read_csv(io.BytesIO(raw)); rows=df.to_dict(orient='records')
    elif name.endswith('.xlsx') or name.endswith('.xls'):
        import io
        df=pd.read_excel(io.BytesIO(raw)); rows=df.to_dict(orient='records')
    elif name.endswith('.jsonl'):
        rows=[json.loads(x) for x in raw.decode('utf-8').splitlines() if x.strip()]
    elif name.endswith('.json'):
        data=json.loads(raw.decode('utf-8')); rows=data if isinstance(data,list) else data.get('items', data.get('data', []))
    else:
        raise ValueError('Use CSV, JSON, JSONL, XLSX or XLS')
    return [normalize_row(r, fallback_platform) for r in rows]
