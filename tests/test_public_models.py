"""GitHub-only weights, multipart integrity, resumable downloads and sizing guidance."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from scripts import public_models as models
from scripts.model_catalog import catalog
from app import model_guidance
from app.model_runtime import LABELS,downloads


class Response(io.BytesIO):
    def __init__(self,data,status=200):super().__init__(data);self.status=status


def fake_model(monkeypatch,data=b'abcdefghijkl',part_size=4):
    sha=hashlib.sha256(data).hexdigest();assets=[f'{sha}-test.part{i+1:03}' for i in range((len(data)+part_size-1)//part_size)]
    row={'size':len(data),'assets':assets};monkeypatch.setattr(models,'manifest',lambda:{sha:row});monkeypatch.setattr(models,'PART_SIZE',part_size)
    return sha,row


def test_catalog_and_all_runtime_profiles_use_complete_github_mirror(tmp_path):
    inventory={r['sha256'] for r in catalog()}
    assert inventory==set(models.manifest()) and len(inventory)==39
    for sha in inventory:
        assert all(url.startswith(models.RELEASE_URL) for url in models.mirror_urls(sha))
    for profile in LABELS:
        assert all(url.startswith(models.RELEASE_URL) for url,_,_ in downloads(profile,tmp_path))


@pytest.mark.parametrize('range_supported',[True,False])
def test_multipart_resume_and_range_fallback_verify_whole_model(monkeypatch,tmp_path,range_supported):
    data=b'abcdefghijkl';sha,row=fake_model(monkeypatch,data)
    target=tmp_path/'weights.onnx';partial=target.with_suffix('.onnx.download');partial.write_bytes(data[:5]);partial.with_suffix('.download.json').write_text(json.dumps({'sha256':sha}))
    calls=[]
    def open_url(request,timeout):
        assert request.full_url.startswith(models.RELEASE_URL);name=request.full_url.rsplit('/',1)[1];index=row['assets'].index(name);content=data[index*4:(index+1)*4];header=request.get_header('Range');calls.append((name,header))
        return Response(content[int(header.split('=')[1].split('-')[0]):],206) if header and range_supported else Response(content)
    monkeypatch.setattr(models,'github_open',open_url)
    models.download_model(sha,target)
    assert target.read_bytes()==data and calls[0]==(row['assets'][1],'bytes=1-')
    assert not partial.exists() and not partial.with_suffix('.download.json').exists()


def test_github_checksum_failure_never_installs_or_falls_back(monkeypatch,tmp_path):
    sha,row=fake_model(monkeypatch,b'abcd');calls=[]
    def open_url(request,timeout):calls.append(request.full_url);return Response(b'xxxx')
    monkeypatch.setattr(models,'github_open',open_url)
    target=tmp_path/'weights.onnx'
    with pytest.raises(ValueError,match='GitHub'):models.download_model(sha,target)
    assert not target.exists() and all(url.startswith(models.RELEASE_URL) for url in calls)


def test_confirmed_cached_model_works_offline(monkeypatch,tmp_path):
    target=tmp_path/'weights.onnx';target.write_bytes(b'cached');sha=hashlib.sha256(b'cached').hexdigest()
    monkeypatch.setattr(models,'manifest',lambda:pytest.fail('Offline cache must not need the index'))
    monkeypatch.setattr(models,'github_open',lambda *a,**kw:pytest.fail('Offline cache must not access network'))
    models.download_model(sha,target)


def test_cancelled_download_keeps_resumable_partial_and_no_target(monkeypatch,tmp_path):
    sha,_=fake_model(monkeypatch,b'abcdefgh');cancel={'value':False}
    monkeypatch.setattr(models,'github_open',lambda *a,**kw:Response(b'abcd'))
    target=tmp_path/'weights.onnx'
    with pytest.raises(InterruptedError):models.download_model(sha,target,progress=lambda *a:cancel.update(value=True),cancelled=lambda:cancel['value'])
    assert not target.exists() and target.with_suffix('.onnx.download').read_bytes()==b'abcd'


@pytest.mark.parametrize('ram,free,cpus,expected',[(4,1,2,None),(8,5,2,None),(16,8,8,'glm-ocr')])
def test_guidance_is_conservative_for_actual_machine(monkeypatch,ram,free,cpus,expected):
    monkeypatch.setattr(model_guidance,'hardware',lambda:{'ram_gib':ram,'available_gib':free,'logical_cpus':cpus,'architecture':'x64','gpu_required':False})
    status={'profiles':[{'id':key,'installed':key in ('rivok','best')} for key in LABELS]}
    value=model_guidance.guidance(status)
    assert value['recommended_next']==expected and value['download_source']=='GitHub'
    assert next(p for p in value['profiles'] if p['id']=='rivok')['recommended']
    assert all(p['download_bytes']>0 for p in value['profiles'])


def test_indra_download_requires_terms_and_offline_license_is_available(client):
    assert client.post('/api/local-models/indra/install').status_code==400
    assert 'USE RESTRICTIONS' in client.get('/api/local-models/indra/license').text
    result=client.get('/api/local-models/guidance')
    assert result.status_code==200 and result.json()['base_included']


def test_download_redirect_cannot_leave_github():
    request=models.urllib.request.Request(models.RELEASE_URL+'weights.onnx')
    with pytest.raises(ValueError,match='GitHub'):models.GitHubRedirect().redirect_request(request,None,302,'redirect',{},'https://huggingface.co/file')


def test_guide_reports_missing_base_instead_of_claiming_hebrew_installed(monkeypatch,tmp_path):
    monkeypatch.setattr(model_guidance,'assets',lambda:tmp_path)
    monkeypatch.setattr(model_guidance.ocr,'capabilities',lambda:{'hebrew':False,'available':False,'languages':[],'reason':'binary_missing'})
    value=model_guidance.base_status()
    assert not value['ocr']['hebrew'] and not any(row['ready'] for row in value['components'][:-1])
    assert 'אינו זמין' in value['message'] and 'Windows' in value['next_step']
    base=downloads('base',tmp_path)
    assert len(base)==8 and any(path.name=='heb.traineddata' for _,path,_ in base)


def test_task_rankings_and_free_memory_requirements(monkeypatch):
    monkeypatch.setattr(model_guidance,'hardware',lambda:{'ram_gib':32,'available_gib':3.5,'logical_cpus':12,'architecture':'x64','gpu_required':False})
    value=model_guidance.guidance({'profiles':[{'id':key,'installed':False} for key in LABELS]})
    assert len(value['task_rankings'])==3 and 'סדר ניסיון' in value['ranking_basis']
    profiles={p['id']:p for p in value['profiles']}
    assert profiles['glm-ocr']['recommended'] and not profiles['multilingual']['recommended']
    assert profiles['multilingual']['free_ram_gib']==6 and profiles['paddle-vl16']['free_ram_gib']==5
