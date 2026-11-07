
# Uses synthetic Radarr state and an ephemeral test phrase, never production data.
def synthetic_e2e():
    import io, tarfile, tempfile, json, sqlite3, subprocess, time, uuid, xml.etree.ElementTree as ET
    from pathlib import Path
    try:
        import issue33_backup as b
    except ModuleNotFoundError:
        import importlib.util
        spec = importlib.util.spec_from_file_location('issue33_backup', Path(__file__).with_name('backup.py'))
        b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
    b.FIVE_GIB = 1024
    name = 'issue33-e2e-' + uuid.uuid4().hex
    ids = []
    def invoke(a, data=None):
        p = subprocess.run(a, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        if p.returncode: raise RuntimeError('fixture operation failed: ' + a[0])
        return p.stdout
    def fixture(suffix):
        image = invoke(['docker','inspect','--format','{{.Image}}','radarr']).decode().strip()
        cid = invoke(['docker','run','-d','--pull','never','--name',name+suffix,'--label','issue33.e2e=1',
           '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
           '--pids-limit','128','--memory','768m','--log-driver','none',
           '--tmpfs','/config:rw,nosuid,nodev,noexec,size=512m',
           '--tmpfs','/tmp:rw,nosuid,nodev,size=128m','--tmpfs','/run:rw,nosuid,nodev,size=64m',
           '--entrypoint','/bin/sh',image,'-c','while :; do sleep 60; done']).decode().strip()
        ids.append(cid)
        meta=json.loads(invoke(['docker','inspect',cid]))[0]
        assert meta['HostConfig']['NetworkMode']=='none' and meta['HostConfig']['ReadonlyRootfs']
        assert all(x.get('Type')=='tmpfs' for x in meta.get('Mounts',[]))
        return cid
    def start(cid):
        invoke(['docker','exec','-d','-e','HOME=/tmp','-e','DOTNET_CLI_HOME=/tmp',cid,
                '/app/radarr/bin/Radarr','-nobrowser','-data=/config'])
        for _ in range(45):
            p=subprocess.run(['docker','exec',cid,'curl','--noproxy','*','-fsS','--max-time','2','http://127.0.0.1:7878/ping'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if p.returncode==0: return
            time.sleep(1)
        raise RuntimeError('fixture startup timeout')
    try:
        first=fixture('a'); start(first)
        # Stop only the isolated application while keeping its tmpfs mounted.
        invoke(['docker','exec',first,'pkill','-TERM','-f','^/app/radarr/bin/Radarr'])
        for _ in range(30):
            p=subprocess.run(['docker','exec',first,'pgrep','-f','^/app/radarr/bin/Radarr'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if p.returncode==1: break
            time.sleep(1)
        else: raise RuntimeError('fixture app did not stop')
        config=invoke(['docker','exec',first,'cat','/config/config.xml'])
        invoke(['docker','exec',first,'sh','-c','test ! -s /config/radarr.db-wal'])
        db=invoke(['docker','exec',first,'cat','/config/radarr.db'])
        inspect_db=bytearray(db); inspect_db[18:20]=b'\x01\x01'
        check=sqlite3.connect(':memory:'); check.deserialize(bytes(inspect_db))
        assert check.execute('pragma integrity_check').fetchone()[0]=='ok'
        expected=check.execute('select count(*) from Movies').fetchone()[0]; db=check.serialize(); check.close()
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w') as tar:
            for n,d in [('./radarr/config.xml',config),('./radarr/radarr.db',db)]:
                ti=tarfile.TarInfo(n); ti.size=len(d); tar.addfile(ti,io.BytesIO(d))
        empty=io.BytesIO()
        with tarfile.open(fileobj=empty,mode='w'): pass
        with tempfile.TemporaryDirectory(prefix='issue33-synthetic-') as tmp:
            stage=Path(tmp)/'generation'; stage.mkdir(); home=Path(tmp)/'gpghome'; home.mkdir(mode=0o700); secret=b'synthetic-only-test-phrase-2026'
            parts=[]
            for part in sorted(b.expected_part_names()):
                data=(buf.getvalue() if part=='docker-data.tar.gpg' else db if part=='radarr-snapshot.sqlite.gpg'
                      else empty.getvalue() if part.endswith('.tar.gpg') else b'{}')
                parts.append(b.encrypt_bytes(stage,home,secret,part,data))
            sums={p.name:{'bytes':p.stat().st_size,'sha256':b.digest(p)} for p in parts}
            b.encrypt_bytes(stage,home,secret,'checksums.json.gpg',json.dumps(sums).encode())
            b.verify_generation(stage,secret)
            def decrypt(part):
                p=b.start_gpg(home,secret,'decrypt',source=stage/part); data=p.stdout.read(); p.stdout.close()
                if p.wait(): raise RuntimeError('decrypt failed')
                return data
            with tarfile.open(fileobj=io.BytesIO(decrypt('docker-data.tar.gpg'))) as tar:
                restored_config=tar.extractfile('./radarr/config.xml').read()
            restored_db=decrypt('radarr-snapshot.sqlite.gpg')
            inspect_restored=bytearray(restored_db); inspect_restored[18:20]=b'\x01\x01'
            check=sqlite3.connect(':memory:'); check.deserialize(bytes(inspect_restored))
            assert check.execute('pragma integrity_check').fetchone()[0]=='ok'
            assert check.execute('select count(*) from Movies').fetchone()[0]==expected; check.close()
            second=fixture('b')
            for filename,data in [('config.xml',restored_config),('radarr.db',restored_db)]:
                invoke(['docker','exec','-i',second,'sh','-c','umask 077; cat > /config/'+filename],data)
            start(second)
            key=ET.fromstring(restored_config).findtext('ApiKey')
            assert key and all(c not in key for c in '\r\n"\\')
            request=('silent\nfail\nheader = "X-Api-Key: '+key+'"\nurl = "http://127.0.0.1:7878/api/v3/movie"\n').encode()
            movies=json.loads(invoke(['docker','exec','-i',second,'curl','--noproxy','*','-K','-'],request))
            assert len(movies)==expected
            print('SYNTHETIC_ENCRYPTED_GENERATION_RESTORE_API_SQLITE_PASSED')
            subprocess.run(['gpgconf','--homedir',str(home),'--kill','gpg-agent'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    finally:
        for cid in ids:
            meta=json.loads(invoke(['docker','inspect',cid]))[0]
            if meta['Config']['Labels'].get('issue33.e2e')!='1': raise RuntimeError('fixture label mismatch')
            invoke(['docker','rm','-f',cid])
        print('E2E_FIXTURES_REMOVED')

if __name__ == '__main__':
    synthetic_e2e()
