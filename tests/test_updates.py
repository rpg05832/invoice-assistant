import hashlib
import io
import json
import sqlite3
from types import SimpleNamespace
import pytest
from app.updates import select_release,helper_script


def release(version='0.3.0',sha='b'*40,published='2026-12-01T00:00:00Z',data=b'installer'):
    tag=f'windows-{version}-{sha[:8]}';name=f'InvoiceAssistant-Setup-{version}-x64.exe';root=f'https://github.com/rpg05832/invoice-assistant/releases/download/{tag}/'
    digest=hashlib.sha256(data).hexdigest()
    return {'tag_name':tag,'target_commitish':sha,'published_at':published,'draft':False,'assets':[{'name':name,'size':len(data),'digest':'sha256:'+digest,'browser_download_url':root+name},{'name':name+'.sha256','browser_download_url':root+name+'.sha256'}]}


def test_release_selection_rejects_current_downgrade_and_foreign_assets():
    current={'version':'0.2.0','commit':'a'*40,'built_at':'2026-11-15T00:00:00Z'}
    assert select_release([release('0.1.0'),release('0.2.0','a'*40)],current) is None
    new=release();assert select_release([new],current)['version']=='0.3.0'
    new['assets'][0]['browser_download_url']='https://example.com/install.exe'
    assert select_release([new],current) is None
    assert select_release([release('0.2.0','c'*40,'2026-11-14T00:00:00Z')],current) is None
    assert select_release([release('0.2.0','c'*40)],current)['commit']=='c'*40


@pytest.mark.parametrize('bad_checksum,bad_file',[(False,False),(True,False),(False,True)])
def test_download_verifies_both_release_digest_and_sidecar(client,monkeypatch,bad_checksum,bad_file):
    import app.updates as module
    manager=client.app.state.updates;candidate=select_release([release()],{'version':'0.2.0','commit':'x','built_at':''});manager.release=candidate;manager.job={'state':'downloading','progress':0}
    def opened(request,timeout):
        if request.full_url.endswith('.sha256'):return io.BytesIO((('0'*64 if bad_checksum else candidate['sha256'])+'  '+candidate['name']).encode())
        return io.BytesIO(b'bad-file!' if bad_file else b'installer')
    monkeypatch.setattr(module,'github_open',opened);manager._download(candidate)
    assert manager.status()['job']['state']==('failed' if bad_checksum or bad_file else 'ready')
    assert manager.path(candidate).exists()==(not bad_checksum and not bad_file)


def test_install_makes_database_backup_and_only_launches_fixed_helper(client,company,monkeypatch):
    import app.updates as module
    manager=client.app.state.updates;manager.supported=True
    candidate=select_release([release()],{'version':'0.2.0','commit':'x','built_at':''});manager.release=candidate;manager.job={'state':'ready','progress':100}
    target=manager.path(candidate);target.parent.mkdir(parents=True);target.write_bytes(b'installer')
    marker=manager.data/'files/keep.txt';marker.write_text('private data')
    calls=[];closed=[];client.app.state.desktop_close=lambda:closed.append(True)
    monkeypatch.setattr(module.subprocess,'Popen',lambda args,**kwargs:calls.append((args,kwargs)))
    class Timer:
        def __init__(self,delay,fn):self.fn=fn
        def start(self):self.fn()
    monkeypatch.setattr(module.threading,'Timer',Timer)
    assert client.post('/api/updates/install',json={'confirmed':False}).status_code==400
    result=client.post('/api/updates/install',json={'confirmed':True});assert result.status_code==200,result.text
    assert closed and calls and calls[0][0][-2]=='-EncodedCommand' and calls[0][1]['creationflags']==0x08000000
    backup=next((manager.data/'backups').glob('pre-update-*.sqlite3'))
    with sqlite3.connect(backup) as c:assert c.execute('SELECT name FROM companies').fetchone()[0]==company['name']
    assert marker.read_text()=='private data'


def test_helper_waits_for_app_exit_escapes_paths_and_never_kills():
    script=helper_script("C:/Users/א'ב/update.exe","C:/Program Files/Invoice",123,'C:/logs/update.log')
    assert "א''ב" in script and 'Wait-Process -Id 123' in script and 'Stop-Process' not in script
    assert script.index('Wait-Process')<script.index('Start-Process') and '/VERYSILENT' in script


def test_update_check_errors_preserve_application(client,monkeypatch):
    monkeypatch.setattr('app.updates.urllib.request.urlopen',lambda *a,**k:(_ for _ in ()).throw(OSError('offline')))
    assert client.post('/api/updates/check').status_code==409
    assert client.get('/api/health').json()['ok']
