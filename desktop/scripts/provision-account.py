"""Run by the server administrator after allocating a dedicated Linux user. Never moves user data."""
import argparse,hashlib,json,os,pwd,stat,uuid
from pathlib import Path
from urllib.parse import urlsplit

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub',required=True,help='Canonical service origin shown by the client')
    parser.add_argument('--user-id',required=True,help='Login service user UUID, not username')
    parser.add_argument('--linux-user',required=True)
    parser.add_argument('--data-ownership-reviewed',action='store_true',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Run as server administrator')
    url=urlsplit(args.hub)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):raise SystemExit('Use a service origin only')
    host=url.hostname.lower();host='['+host+']' if ':' in host else host
    port='' if url.port is None or url.port=={'http':80,'https':443}[url.scheme] else ':'+str(url.port)
    origin=url.scheme+'://'+host+port
    user=pwd.getpwnam(args.linux_user)
    if user.pw_uid<1000:raise SystemExit('Allocate a dedicated non-system user first')
    home=Path(user.pw_dir)
    if any(p.is_symlink() for p in [home,*home.parents]):raise SystemExit('Home path must not contain links')
    info=home.stat()
    if not home.is_dir() or info.st_uid!=user.pw_uid or stat.S_IMODE(info.st_mode)!=0o700:raise SystemExit('Administrator must first set dedicated home ownership and mode 0700')
    owner=hashlib.sha256(origin.encode()).hexdigest()+':'+str(uuid.UUID(args.user_id))
    folder=Path('/etc/omaax/accounts');folder.mkdir(parents=True,mode=0o755,exist_ok=True)
    for p in [folder,*folder.parents]:
        st=p.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise SystemExit('Binding directory must be root owned and not writable by others')
    target=folder/(str(user.pw_uid)+'.json')
    # Exclusive creation: no automatic reassignment or adoption by first login.
    with target.open('x',encoding='utf-8') as handle:json.dump({'uid':user.pw_uid,'owner':owner},handle)
    target.chmod(0o644)
    print('Created binding:',target)

if __name__=='__main__':main()
