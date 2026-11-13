"""Small CPU-only residual CTC adapter trained on local, confirmed numeral crops.

The generic ONNX recognizer stays immutable. Only this installation's residual
head is trained; image features help distinguish decoration from digit strokes.
"""
import math
import hashlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image,ImageOps,ImageDraw
from .sequence_digits import load_model
from .paths import assets

BASE='paddle-v6-small'
FORMAT=1


@lru_cache(maxsize=2)
def _digest(path,modified,size):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def base_digest():
    path=assets()/'models/PP-OCRv6_small_rec_onnx/inference.onnx';stat=path.stat()
    return _digest(str(path),stat.st_mtime_ns,stat.st_size)


def features(path):
    root=assets()/'models/PP-OCRv6_small_rec_onnx'
    session,characters,shape=load_model(str(root));height=shape[1]
    with Image.open(path) as image:rgb=np.array(image.convert('RGB'))
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    if gray.std()<3:raise ValueError('האזור המסומן ריק')
    h,w=gray.shape;rw=min(1600,max(1,math.ceil(height*w/h)));width=max(shape[2],rw)
    tensor=np.zeros((1,3,height,width),np.float32)
    tensor[0,:,:,:rw]=(cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),(rw,height)).astype(np.float32)/127.5-1).transpose(2,0,1)
    raw=session.run(None,{session.get_inputs()[0].name:tensor})[0][0]
    if raw.min()<0 or not np.allclose(raw.sum(-1),1,atol=.02):raw=softmax(raw)
    indexes=[characters.index(str(i)) for i in range(10)]
    probs=np.column_stack([raw[:,0]+np.maximum(0,1-raw[:,indexes].sum(-1)-raw[:,0]),raw[:,indexes]])
    probs=np.maximum(probs,1e-7);probs/=probs.sum(-1,keepdims=True)
    prior=np.log(probs).astype(np.float32)
    context=np.concatenate([np.roll(np.clip(prior,-12,0)/6,s,axis=0) for s in (-2,-1,0,1,2)],axis=1)
    ink=np.zeros((height,width),np.float32);ink[:,:rw]=1-cv2.resize(gray,(rw,height)).astype(np.float32)/255
    visual=cv2.resize(ink,(len(prior),8),interpolation=cv2.INTER_AREA).T
    visual=np.concatenate([np.roll(visual,s,axis=0) for s in (-1,0,1)],axis=1)
    return np.concatenate([context,visual],axis=1).astype(np.float32),prior


def augmented_features(path,reason,seed=2718):
    """Training-only augmentation; preserve all source glyphs and the label."""
    import tempfile
    rng=np.random.default_rng(seed)
    with Image.open(path) as image:
        image=ImageOps.autocontrast(image.convert('RGB'))
        if reason=='extra_stroke':
            # Put a decorative underline OUTSIDE the original image, never erase ink.
            margin=max(6,image.height//8);image=ImageOps.expand(image,border=(0,0,0,margin),fill='white')
            ImageDraw.Draw(image).line((image.width//6,image.height-3,image.width*5//6,image.height-3),fill=(60,70,120),width=1)
        else:image=image.rotate(float(rng.uniform(-2,2)),expand=True,fillcolor='white')
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'augmentation.png';image.save(file);return features(file)


def softmax(logits):
    exp=np.exp(logits-logits.max(-1,keepdims=True));return exp/exp.sum(-1,keepdims=True)


def decode(logits):
    result=[];last=-1
    for value in logits.argmax(-1):
        if value and value!=last:result.append(str(int(value)-1))
        last=value
    return ''.join(result)


def ctc_loss_gradient(logits,text):
    """Log-space forward/backward CTC, including adjacent repeated digits."""
    labels=np.array([0]+[v for ch in text for v in (int(ch)+1,0)],dtype=int)
    tmax=len(logits);states=len(labels)
    if tmax<len(text)+sum(a==b for a,b in zip(text,text[1:])):raise ValueError('החיתוך קצר מדי לרצף שסומן')
    probs=softmax(logits);logp=np.log(np.maximum(probs,1e-30))
    alpha=np.full((tmax,states),-np.inf);alpha[0,0]=logp[0,0]
    if states>1:alpha[0,1]=logp[0,labels[1]]
    skip=np.array([i>=2 and labels[i]!=0 and labels[i]!=labels[i-2] for i in range(states)])
    for t in range(1,tmax):
        previous=alpha[t-1];one=np.r_[-np.inf,previous[:-1]];two=np.r_[[-np.inf,-np.inf],previous[:-2]] if states>1 else np.array([-np.inf])
        alpha[t]=np.logaddexp(np.logaddexp(previous,one),np.where(skip,two,-np.inf))+logp[t,labels]
    total=np.logaddexp(alpha[-1,-1],alpha[-1,-2]) if states>1 else alpha[-1,0]
    if not np.isfinite(total):raise ValueError('לא ניתן להתאים את הרצף לחיתוך')
    beta=np.full_like(alpha,-np.inf);beta[-1,-1]=0
    if states>1:beta[-1,-2]=0
    for t in range(tmax-2,-1,-1):
        following=beta[t+1]+logp[t+1,labels]
        one=np.r_[following[1:],-np.inf];two=np.r_[following[2:],[-np.inf,-np.inf]] if states>1 else np.array([-np.inf])
        allowed=np.r_[skip[2:],[False,False]] if states>1 else np.array([False])
        beta[t]=np.logaddexp(np.logaddexp(following,one),np.where(allowed,two,-np.inf))
    occupancy=np.exp(np.clip(alpha+beta-total,-80,0));gradient=probs.copy()
    for state,label in enumerate(labels):gradient[:,label]-=occupancy[:,state]
    scale=max(1,len(text));return float(-total/scale),(gradient/scale).astype(np.float32)


def init_model(width,seed=2718):
    rng=np.random.default_rng(seed)
    return {'w1':rng.normal(0,.08,(width,32)).astype(np.float32),'b1':np.zeros(32,np.float32),'w2':np.zeros((32,11),np.float32),'b2':np.zeros(11,np.float32)}


def predict(model,x,prior):
    hidden=np.tanh(x@model['w1']+model['b1']);return prior+hidden@model['w2']+model['b2']


def train(samples,validation,epochs=30,progress=None,cancelled=None,initial=None):
    """Samples contain features, prior, text, writer. No network calls or GPU."""
    if not samples:raise ValueError('נדרשות דוגמאות מאושרות לאימון')
    model={k:v.copy() for k,v in initial.items()} if initial else init_model(samples[0]['x'].shape[1])
    rng=np.random.default_rng(2718);mom={k:np.zeros_like(v) for k,v in model.items()};variance={k:np.zeros_like(v) for k,v in model.items()};step=0
    best={k:v.copy() for k,v in model.items()};best_loss=float('inf');last=0
    writers={s.get('writer','unknown') for s in samples};groups={w:[s for s in samples if s.get('writer','unknown')==w] for w in writers}
    for epoch in range(epochs):
        losses=[]
        order=list(rng.permutation(len(samples)))
        for index in order:
            if cancelled and cancelled():raise InterruptedError('האימון נעצר')
            # Rotate through writers rather than letting the largest writer dominate.
            writer=sorted(writers)[index%len(writers)];pool=groups[writer];sample=pool[int(rng.integers(len(pool)))]
            x=sample['x'];prior=sample['prior'];hidden=np.tanh(x@model['w1']+model['b1'])
            loss,gradient=ctc_loss_gradient(prior+hidden@model['w2']+model['b2'],sample['text']);losses.append(loss)
            back=(gradient@model['w2'].T)*(1-hidden**2)
            grads={'w2':hidden.T@gradient,'b2':gradient.sum(0),'w1':x.T@back,'b1':back.sum(0)}
            norm=max(1,float(np.sqrt(sum((g*g).sum() for g in grads.values())))/5);step+=1
            for key in model:
                g=grads[key]/norm+model[key]*.0001;mom[key]=.9*mom[key]+.1*g;variance[key]=.999*variance[key]+.001*g*g
                model[key]-=.004*(mom[key]/(1-.9**step))/(np.sqrt(variance[key]/(1-.999**step))+1e-8)
        # Select epochs using training loss ONLY. Held-out data is never used for fitting or selection.
        last=float(np.mean(losses))
        if last<best_loss:best_loss=last;best={k:v.copy() for k,v in model.items()}
        if progress:progress(epoch+1,epochs,last)
    return best,{'training_loss':best_loss,'epochs':epochs}


def save(path,model):
    with Path(path).open('wb') as f:np.savez_compressed(f,format=np.array([FORMAT]),**model)


def load(path):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist())!={'format.npy','w1.npy','b1.npy','w2.npy','b2.npy'} or sum(f.file_size for f in archive.infolist())>40000:raise ValueError('קובץ מודל אישי לא תקין')
    with np.load(path,allow_pickle=False) as data:
        if data['format'].shape!=(1,) or int(data['format'][0])!=FORMAT:raise ValueError('גרסת מודל אישי לא נתמכת')
        result={key:data[key].astype(np.float32) for key in ('w1','b1','w2','b2')}
    if result['w1'].shape!=(79,32) or result['w2'].shape!=(32,11) or result['b1'].shape!=(32,) or result['b2'].shape!=(11,) or not all(np.isfinite(v).all() for v in result.values()):raise ValueError('מודל אישי לא תקין')
    return result


def evaluate(model,samples):
    rows=[]
    for sample in samples:
        base=decode(sample['prior']);personal=decode(predict(model,sample['x'],sample['prior']))
        rows.append({'id':sample['id'],'writer_id':sample.get('writer'),'expected':sample['text'],'baseline':base,'personal':personal,'baseline_correct':base==sample['text'],'personal_correct':personal==sample['text']})
    return {'tested':len(rows),'baseline_correct':sum(r['baseline_correct'] for r in rows),'personal_correct':sum(r['personal_correct'] for r in rows),'regressions':sum(r['baseline_correct'] and not r['personal_correct'] for r in rows),'rows':rows}
