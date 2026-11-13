"""Whole-sequence CTC proposals. These models are restricted to digit crops."""
import math
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import yaml
from PIL import Image
from .paths import assets


@lru_cache(maxsize=2)
def load_model(directory):
    root=Path(directory)
    config=yaml.safe_load((root/'inference.yml').read_text())
    characters=['blank']+config['PostProcess']['character_dict']+[' ']
    shape=next((v['RecResizeImg']['image_shape'] for v in config['PreProcess']['transform_ops'] if 'RecResizeImg' in v),[3,48,320])
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1;options.log_severity_level=3
    session=ort.InferenceSession(str(root/'inference.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    return session,characters,shape


def recognize(path,model='paddle-v6-small'):
    names={'paddle-v5':'paddle-digits','paddle-v6-small':'PP-OCRv6_small_rec_onnx'}
    root=assets()/'models'/names[model]
    if not (root/'inference.onnx').is_file():return {'engine':model,'available':False,'number':'','warning':'מודל הרצפים אינו מותקן'}
    session,characters,shape=load_model(str(root));height=shape[1]
    with Image.open(path) as image:rgb=np.array(image.convert('RGB'))
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    if float(gray.std())<3 or int((gray<230).sum())<max(8,int(gray.size*.001)):
        return {'engine':model,'available':True,'number':'','confidence':0,'confirmed':False,'warning':'לא נמצא דיו מספיק באזור המסומן'}
    image=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR);h,w=image.shape[:2]
    width=max(shape[2],min(3200,math.ceil(height*w/h)));rw=min(width,math.ceil(height*w/h))
    array=np.zeros((1,3,height,width),np.float32)
    array[0,:,:,:rw]=(cv2.resize(image,(rw,height)).astype(np.float32)/127.5-1).transpose(2,0,1)
    values=session.run(None,{session.get_inputs()[0].name:array})[0][0]
    choices={}
    for constrained in (False,True):
        output=values.copy()
        if constrained:
            allowed={0}|{i for i,ch in enumerate(characters) if ch.isascii() and (ch.isdigit() or ch==' ')}
            output[:,[i for i in range(output.shape[1]) if i not in allowed]]=0
        result=[];scores=[];last=0
        for position,index in enumerate(output.argmax(-1)):
            if index and index!=last:result.append(characters[index]);scores.append(float(values[position,index]))
            last=index
        text=''.join(result).strip();number=text.replace(' ','') if re.fullmatch(r'[0-9 ]{1,40}',text) else ''
        if number and len(number)<=30:choices[number]=min(scores) if scores else 0
        if not constrained:raw=text
    best=max(choices,key=choices.get) if choices else ''
    return {'engine':model,'available':True,'number':best,'raw':raw,'confidence':choices.get(best,0),'alternatives':[{'number':n,'confidence':c} for n,c in choices.items()],'confirmed':False,'warning':'קריאת רצף שלם; הציון אינו מדידת דיוק של צ׳ק. יש לבדוק אפסים מובילים, קווים וספרות חסרות.'}
