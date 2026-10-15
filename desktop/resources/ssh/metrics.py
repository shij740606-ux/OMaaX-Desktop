import json, os, platform, shutil, subprocess, time

def read(path):
    with open(path) as f: return f.read()
def cpu():
    values = list(map(int, read('/proc/stat').splitlines()[0].split()[1:]))
    return sum(values[:8]), values[3] + values[4]
def optional(command):
    try: return subprocess.check_output(command, stderr=subprocess.DEVNULL, timeout=3, text=True).strip()
    except (OSError, subprocess.SubprocessError): return ''
if platform.system() != 'Linux':
    raise SystemExit('Resource query currently supports Linux servers')
first = cpu(); time.sleep(1); second = cpu()
mem = {line.split(':')[0]: int(line.split()[1])*1024 for line in read('/proc/meminfo').splitlines()}
info = read('/proc/cpuinfo')
model = next((line.split(':',1)[1].strip() for line in info.splitlines() if line.startswith('model name')), platform.processor())
mounts = []
for line in read('/proc/mounts').splitlines():
    parts = line.split(); device, mount, kind = parts[:3]
    if device.startswith('/dev/') and not mount.startswith(('/snap/', '/boot/efi')):
        mount = mount.replace('\\040', ' ')
        try:
            disk = shutil.disk_usage(mount)
            mounts.append(dict(mount=mount, total=disk.total, used=disk.used, free=disk.free))
        except OSError: pass
if not mounts:
    disk = shutil.disk_usage('/'); mounts.append(dict(mount='/', total=disk.total, used=disk.used, free=disk.free))
network = []
for line in read('/proc/net/dev').splitlines()[2:]:
    name, values = line.split(':'); values = values.split()
    if name.strip() != 'lo': network.append(dict(name=name.strip(), received=int(values[0]), sent=int(values[8])))
gpus = optional(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu', '--format=csv,noheader,nounits'])
delta = second[0]-first[0]
print(json.dumps(dict(sampledAt=time.time(), hostname=platform.node(), system=optional(['uname','-sr']), architecture=platform.machine(), cpuModel=model, logicalCores=os.cpu_count(), physicalCores=len(set((block.split('physical id')[1].splitlines()[0], block.split('core id')[1].splitlines()[0]) for block in info.split('\n\n') if 'physical id' in block and 'core id' in block)) or None, cpuPercent=round(100*(1-(second[1]-first[1])/delta),1) if delta else None, load=list(os.getloadavg()), uptimeSeconds=float(read('/proc/uptime').split()[0]), memoryTotal=mem['MemTotal'], memoryUsed=mem['MemTotal']-mem.get('MemAvailable',mem.get('MemFree',0)), swapTotal=mem.get('SwapTotal',0), swapUsed=mem.get('SwapTotal',0)-mem.get('SwapFree',0), disks=mounts, network=network, gpu=gpus or None)))
