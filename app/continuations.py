"""Require a complete, explicit page sequence before proposing a continuation group."""
import re
from collections import defaultdict


def page_marker(text):
    text=re.sub('[\u200e\u200f\u202a-\u202e\u2066-\u2069]','',text)
    values={(int(a),int(b)) for a,b in re.findall(r'(?:\b(?:דף|עמוד)\s*[:.]?\s*(\d{1,2})\s*מתוך\s*(\d{1,2})\b)',text)}
    values.update((int(a),int(b)) for a,b in re.findall(r'\bPage\s*(\d{1,2})\s*of\s*(\d{1,2})\b',text,re.I))
    if len(values)!=1:return None
    current,total=next(iter(values))
    return (current,total) if 1<=current<=total<=50 else None


def groups(documents):
    candidates=defaultdict(list)
    for d in documents:
        marker=page_marker(d['ocr_text']);f=d['fields']
        if not marker or marker[1]<2 or not f.get('number') or d.get('region') or d.get('parent_id'):continue
        kind='invoice-family' if f.get('kind') in ('invoice','tax_receipt') else f.get('kind')
        key=(d['source_id'],kind,f['number'].strip().casefold(),marker[1])
        candidates[key].append((marker[0],d))
    result=[]
    for key,rows in candidates.items():
        total=key[-1]
        if len(rows)!=total or {n for n,d in rows}!=set(range(1,total+1)):continue
        if any(len({d['fields'].get(k) for n,d in rows if d['fields'].get(k)})>1 for k in ('supplier_tax_id','recipient_tax_id','date','currency')):continue
        ordered=[d for n,d in sorted(rows,key=lambda item:item[0])]
        physical=sorted(d['page'] for d in ordered)
        if physical[-1]-physical[0]+1!=total:continue
        result.append(ordered)
    return result
