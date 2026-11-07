"""Read host resource use from procfs; guest CPU ticks are not counted twice."""
from pathlib import Path
import shutil
import time


def cpu_ticks(text):
    line=next(line for line in text.splitlines() if line.startswith("cpu "))
    values=list(map(int,line.split()[1:]))
    if len(values)<4:raise ValueError("Invalid CPU counters")
    return sum(values[:8]),values[3]+(values[4] if len(values)>4 else 0)


def cpu_percent(previous,current):
    total,idle=current[0]-previous[0],current[1]-previous[1]
    if total<=0 or idle<0:return None
    return round(max(0,min(100,(total-idle)*100/total)),1)


def memory_usage(text):
    values={}
    for line in text.splitlines():
        key,_,rest=line.partition(":")
        if rest.strip():values[key]=int(rest.split()[0])*1024
    total=values["MemTotal"]
    available=values.get("MemAvailable",values.get("MemFree",0)+values.get("Buffers",0)+values.get("Cached",0))
    used=max(0,min(total,total-available))
    if total<=0:raise ValueError("Invalid memory total")
    return used,total,round(used*100/total,1)


class Sampler:
    def __init__(self):self.previous=None

    def sample(self):
        result={"sampled_at":time.time(),"cpu_percent":None,"memory_percent":None,"memory_used":None,"memory_total":None,"disk_percent":None,"disk_used":None,"disk_total":None}
        try:
            current=cpu_ticks(Path('/proc/stat').read_text())
            if self.previous is None:
                self.previous=current
                time.sleep(.1)
                current=cpu_ticks(Path('/proc/stat').read_text())
            result["cpu_percent"]=cpu_percent(self.previous,current)
            self.previous=current
        except (OSError,ValueError,StopIteration):pass
        try:
            used,total,percentage=memory_usage(Path('/proc/meminfo').read_text())
            result.update(memory_used=used,memory_total=total,memory_percent=percentage)
        except (OSError,ValueError,KeyError):pass
        try:
            disk=shutil.disk_usage('/')
            result.update(disk_used=disk.used,disk_total=disk.total,disk_percent=round(disk.used*100/disk.total,1))
        except (OSError,ZeroDivisionError):pass
        return result
