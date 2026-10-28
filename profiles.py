"""One persistent Chromium profile, protected by an OS-released process lock."""
import json,os,socket,sys
from pathlib import Path

class ProfileBusy(RuntimeError):pass

class ProfileLease:
    def __init__(self,path):
        self.path=Path(path).resolve();self.handle=None;self.metadata={}
        self.lock_path=self.path.parent/(self.path.name+'.adapter.lock')

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.lock_path,os.O_RDWR|os.O_CREAT|getattr(os,'O_BINARY',0),0o600)
        f=os.fdopen(fd,'r+b')
        try:
            if os.fstat(fd).st_size==0:f.write(b'\n');f.flush()
            f.seek(0)
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            f.close()
            raise ProfileBusy(f'The localhost PICO-8 profile is already in use: {self.path}. Close that adapter session first.') from exc
        self.handle=f
        try:
            f.seek(1);raw=f.read()
            self.metadata=json.loads(raw) if raw else {}
        except (ValueError,OSError):self.metadata={}
        port=self.metadata.get('debugPort')
        if port and self.metadata.get('status')!='closed':
            try:
                with socket.create_connection(('127.0.0.1',int(port)),timeout=.3):pass
            except (OSError,ValueError):pass
            else:
                self.release()
                raise ProfileBusy(f'The previous PICO-8 browser is still running on port {port}. Close it before reusing {self.path}.')
        self.path.mkdir(exist_ok=True)
        self.record(status='starting',ownerPid=os.getpid(),debugPort=None)
        return self

    def record(self,**values):
        if not self.handle:return
        self.metadata.update(values)
        self.handle.seek(1);self.handle.write(json.dumps(self.metadata).encode('utf-8'))
        self.handle.truncate();self.handle.flush()

    def release(self):
        if not self.handle:return
        f=self.handle;self.handle=None
        try:
            f.seek(0)
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        finally:f.close()
