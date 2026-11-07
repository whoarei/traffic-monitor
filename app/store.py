"""Transactional traffic accounting. Only aggregate byte counts are retained."""
from contextlib import contextmanager
import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
VERSION = "1.3.0"


def reset_bounds(day, clock, now=None):
    now = time.time() if now is None else now
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise ValueError("重置日必须在 1 到 31 之间")
    try:
        hour, minute = map(int, clock.split(":"))
        if clock != f"{hour:02}:{minute:02}" or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError("重置时间格式必须为 HH:MM")
    local = datetime.fromtimestamp(now, TZ)
    def boundary(year, month):
        return datetime(year, month, min(day, calendar.monthrange(year, month)[1]), hour, minute, tzinfo=TZ).timestamp()
    this = boundary(local.year, local.month)
    if this <= now:
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        return this, boundary(year, month)
    year, month = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
    return boundary(year, month), this


@contextmanager
def database(path, readonly=False):
    uri = Path(path).resolve().as_uri() + "?mode=ro" if readonly else str(path)
    db = sqlite3.connect(uri, uri=readonly, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    if readonly:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
    else:
        # A rollback journal supports a strictly read-only web mount without
        # requiring writable WAL shared-memory files. Writes are tiny and rare.
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
    try:
        yield db
        if not readonly:
            db.commit()
    except BaseException:
        if not readonly:
            db.rollback()
        raise
    finally:
        db.close()


def initialize(path, devices):
    with database(path) as db:
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        if schema not in (0, 1, 2, 3):
            raise RuntimeError("Unsupported database schema")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
                email TEXT PRIMARY KEY, label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0,
                raw_up INTEGER NOT NULL DEFAULT 0, raw_down INTEGER NOT NULL DEFAULT 0,
                generation TEXT NOT NULL DEFAULT '', sampled REAL NOT NULL DEFAULT 0,
                up_rate REAL NOT NULL DEFAULT 0, down_rate REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daily (
                day TEXT NOT NULL, email TEXT NOT NULL,
                up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day,email)
            );
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, email TEXT NOT NULL,
                up INTEGER NOT NULL, down INTEGER NOT NULL, seconds REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp);
            CREATE INDEX IF NOT EXISTS samples_identity_time ON samples(email,timestamp);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS limits (
                email TEXT PRIMARY KEY, quota INTEGER NOT NULL DEFAULT 0,
                bonus INTEGER NOT NULL DEFAULT 0,
                period_up INTEGER NOT NULL DEFAULT 0, period_down INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1, blocked TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS reset_schedule (
                id INTEGER PRIMARY KEY CHECK(id=1), reset_day INTEGER NOT NULL,
                reset_time TEXT NOT NULL, cycle_start REAL NOT NULL, next_reset REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS allowance_operations (
                request_id TEXT PRIMARY KEY,email TEXT NOT NULL,amount INTEGER NOT NULL
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in db.execute("PRAGMA table_info(limits)")}
        day, clock = 1, "00:00"
        if "reset_day" in columns:
            old_plans = db.execute("SELECT DISTINCT reset_day,reset_time FROM limits").fetchall()
            if len(old_plans) == 1:
                day, clock = old_plans[0]
            db.execute("ALTER TABLE limits RENAME TO limits_v2")
            db.execute("""CREATE TABLE limits (
                email TEXT PRIMARY KEY,quota INTEGER NOT NULL DEFAULT 0,bonus INTEGER NOT NULL DEFAULT 0,
                period_up INTEGER NOT NULL DEFAULT 0,period_down INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,blocked TEXT NOT NULL DEFAULT '')""")
            db.execute("""INSERT INTO limits(email,quota,period_up,period_down,enabled,blocked)
                SELECT email,quota,period_up,period_down,enabled,blocked FROM limits_v2""")
            db.execute("DROP TABLE limits_v2")
        beginning, following = reset_bounds(day, clock)
        db.execute("INSERT OR IGNORE INTO reset_schedule VALUES (1,?,?,?,?)", (day,clock,beginning,following))
        db.execute("PRAGMA user_version=3")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('started',?)", (str(time.time()),))
        db.execute("UPDATE devices SET active=0")
        for device in devices:
            db.execute("""INSERT INTO devices(email,label,active) VALUES (?,?,1)
                ON CONFLICT(email) DO UPDATE SET label=excluded.label,active=1""",
                (device["email"], device["label"]))
        for row in db.execute("SELECT email,active FROM devices").fetchall():
            ensure_limit(db, row["email"], bool(row["active"]))


def ensure_limit(db, email, enabled=True, now=None):
    if db.execute("SELECT 1 FROM limits WHERE email=?", (email,)).fetchone():
        return
    now = time.time() if now is None else now
    start = schedule(db)["cycle_start"]
    day = datetime.fromtimestamp(start, TZ).date().isoformat()
    totals = db.execute("SELECT COALESCE(SUM(up),0),COALESCE(SUM(down),0) FROM daily WHERE email=? AND day>=?", (email, day)).fetchone()
    db.execute("INSERT INTO limits(email,period_up,period_down,enabled) VALUES (?,?,?,?)", (email,totals[0],totals[1],int(enabled)))


def schedule(db):
    return dict(db.execute("SELECT reset_day,reset_time,cycle_start,next_reset FROM reset_schedule WHERE id=1").fetchone())


def update_schedule(path, day, clock, now=None):
    now = time.time() if now is None else now
    _, following = reset_bounds(day, clock, now)
    with database(path) as db:
        db.execute("UPDATE reset_schedule SET reset_day=?,reset_time=?,next_reset=? WHERE id=1", (day,clock,following))
        db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (now,"system_schedule",f"{day} {clock} Asia/Shanghai"))
        return schedule(db)


def roll_schedule(db, now):
    plan = schedule(db)
    if now < plan["next_reset"]:
        return
    beginning, following = reset_bounds(plan["reset_day"],plan["reset_time"],now)
    db.execute("UPDATE reset_schedule SET cycle_start=?,next_reset=? WHERE id=1", (beginning,following))
    db.execute("UPDATE limits SET period_up=0,period_down=0,bonus=0")
    db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (now,"system_reset","All quota periods reset; temporary allowances expired"))


def grant_bonus(path, email, amount, request_id=None):
    if isinstance(amount,bool) or not isinstance(amount,int) or not 0 < amount <= 1024**5:
        raise ValueError("临时流量必须是大于 0 且不超过 1 PiB 的整数字节数")
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        policy = db.execute("SELECT * FROM limits WHERE email=?", (email,)).fetchone()
        if policy is None:
            raise ValueError("连接不存在")
        if not policy["quota"]:
            raise ValueError("该连接不限额，无需追加临时流量")
        used = policy["period_up"] + policy["period_down"]
        previous = db.execute("SELECT * FROM allowance_operations WHERE request_id=?",(request_id,)).fetchone() if request_id else None
        if previous:
            if previous["email"] != email or previous["amount"] != amount:
                raise ValueError("操作标识已被其他加额请求使用")
            return {"bonus":policy["bonus"],"quota_effective":policy["quota"]+policy["bonus"],"available":max(0,policy["quota"]+policy["bonus"]-used),"enabled":bool(policy["enabled"])}
        bonus = max(policy["bonus"],used-policy["quota"]) + amount
        if bonus > 1024**5:
            raise ValueError("本期临时额度过大")
        db.execute("UPDATE limits SET bonus=? WHERE email=?", (bonus,email))
        if request_id:
            db.execute("INSERT INTO allowance_operations VALUES (?,?,?)",(request_id,email,amount))
        db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (time.time(),"temporary_allowance",email+":"+str(amount)))
        return {"bonus":bonus,"quota_effective":policy["quota"]+bonus,"available":policy["quota"]+bonus-used,"enabled":bool(policy["enabled"])}


def period_record(db, email, start, end, up, down):
    ensure_limit(db, email, now=end)
    beginning = schedule(db)["cycle_start"]
    proportion = max(0,min(1,(end-max(start,beginning))/(end-start))) if end > start else 1
    db.execute("UPDATE limits SET period_up=period_up+?,period_down=period_down+? WHERE email=?", (int(up*proportion),int(down*proportion),email))


def meta_set(db, key, value):
    db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def day_parts(start, end, amount):
    """Split a sample across Beijing midnights, proportionally within the interval."""
    if end <= start or end - start > 86400 * 31:
        return [(datetime.fromtimestamp(end, TZ).date().isoformat(), amount)]
    result, remaining, cursor = [], amount, start
    while cursor < end:
        local = datetime.fromtimestamp(cursor, TZ)
        boundary = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), TZ).timestamp()
        stop = min(end, boundary)
        allocated = remaining if stop == end else int(amount * ((stop - cursor) / (end - start)))
        result.append((local.date().isoformat(), allocated))
        remaining -= allocated
        cursor = stop
    return result


def record(path, counters, generation, now=None):
    now = time.time() if now is None else now
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        previous = dict(db.execute("SELECT key,value FROM meta"))
        if now < float(previous.get("last_success", 0)):
            raise RuntimeError("System clock moved backwards; sample deferred")
        roll_schedule(db, now)
        if previous.get("generation") not in (None, generation):
            db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (now, "xray_restart", "Detected a new Xray process; cumulative history retained"))
        for email in counters:
            db.execute("INSERT OR IGNORE INTO devices(email,label) VALUES (?,?)", (email, email))
        for row in db.execute("SELECT * FROM devices").fetchall():
            raw = counters.get(row["email"], {})
            same = row["generation"] == generation
            current_up = int(raw.get("uplink", row["raw_up"] if same else 0))
            current_down = int(raw.get("downlink", row["raw_down"] if same else 0))
            if current_up < 0 or current_down < 0:
                raise RuntimeError("Negative source counter")
            delta_up = current_up - row["raw_up"] if same and current_up >= row["raw_up"] else current_up
            delta_down = current_down - row["raw_down"] if same and current_down >= row["raw_down"] else current_down
            elapsed = max(0, now - row["sampled"]) if row["sampled"] else 0
            rate_up, rate_down = (delta_up / elapsed, delta_down / elapsed) if elapsed and same else (0, 0)
            db.execute("""UPDATE devices SET up=up+?,down=down+?,raw_up=?,raw_down=?,generation=?,sampled=?,up_rate=?,down_rate=? WHERE email=?""",
                       (delta_up, delta_down, current_up, current_down, generation, now, rate_up, rate_down, row["email"]))
            start = row["sampled"] if elapsed and same else now
            period_record(db, row["email"], start, now, delta_up, delta_down)
            up_days = dict(day_parts(start, now, delta_up))
            down_days = dict(day_parts(start, now, delta_down))
            for day in up_days:
                db.execute("""INSERT INTO daily(day,email,up,down) VALUES (?,?,?,?)
                    ON CONFLICT(day,email) DO UPDATE SET up=up+excluded.up,down=down+excluded.down""",
                           (day, row["email"], up_days[day], down_days[day]))
            db.execute("INSERT INTO samples(timestamp,email,up,down,seconds) VALUES (?,?,?,?,?)", (now, row["email"], delta_up, delta_down, elapsed if same else 0))
        meta_set(db, "generation", generation)
        meta_set(db, "last_success", now)
        meta_set(db, "last_error", "")
        if now - float(previous.get("last_prune", 0)) > 86400:
            db.execute("DELETE FROM samples WHERE timestamp<?", (now - 86400 * 30,))
            db.execute("DELETE FROM events WHERE timestamp<?", (now - 86400 * 365,))
            meta_set(db, "last_prune", now)


def record_error(path, message):
    with database(path) as db:
        meta_set(db, "last_error", message[:300])


def valid_range(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 3660:
        raise ValueError("日期范围必须按先后顺序，且不超过十年")
    return first.isoformat(), last.isoformat()


def history(path, start, end, device=""):
    start, end = valid_range(start, end)
    with database(path, readonly=True) as db:
        if device and not db.execute("SELECT 1 FROM devices WHERE email=?", (device,)).fetchone():
            raise ValueError("未知设备")
        rows = db.execute("""SELECT d.day,d.email,v.label,d.up,d.down FROM daily d
            JOIN devices v ON v.email=d.email WHERE d.day BETWEEN ? AND ?
            AND (?='' OR d.email=?) ORDER BY d.day DESC,v.label""", (start, end, device, device)).fetchall()
        entries = [{**dict(row), "total": row["up"] + row["down"]} for row in rows]
        return {"from": start, "to": end, "device": device, "granularity":"day", "rows": entries,
                "up": sum(x["up"] for x in entries), "down": sum(x["down"] for x in entries)}


def hourly_history(path, device="", now=None):
    """Last 24 hours, split at Beijing clock hours; samples crossing a boundary are apportioned."""
    end = time.time() if now is None else now
    start = end - 86400
    first = int(start // 3600) * 3600
    last = int((end - .000001) // 3600) * 3600
    buckets = list(range(first,last+1,3600))
    with database(path,readonly=True) as db:
        devices = [dict(row) for row in db.execute("SELECT email,label FROM devices WHERE (?='' OR email=?) ORDER BY label",(device,device))]
        if device and not devices:
            raise ValueError("未知设备")
        totals = {(bucket,d["email"]):[0,0] for bucket in buckets for d in devices}
        coverage = set()
        sql = "SELECT timestamp,email,up,down,seconds FROM samples WHERE timestamp>=? AND timestamp<=?"
        parameters = [start,end]
        if device:
            sql += " AND email=?"
            parameters.append(device)
        for sample in db.execute(sql,parameters):
            email = sample["email"]
            if (first,email) not in totals:
                continue
            stamp = sample["timestamp"]
            seconds = max(0,sample["seconds"])
            sample_start = stamp-seconds
            left,right=max(start,sample_start),min(end,stamp)
            amounts=(sample["up"],sample["down"])
            if seconds == 0:
                bucket=min(last,int(stamp//3600)*3600)
                coverage.add((bucket,email))
                for i,amount in enumerate(amounts):totals[(bucket,email)][i]+=amount
                continue
            if right<=left:
                continue
            bucket=int(left//3600)*3600
            while bucket<right:
                coverage.add((bucket,email))
                low,high=max(left,bucket),min(right,bucket+3600)
                for i,amount in enumerate(amounts):
                    before=0 if low<=sample_start else int(amount*(low-sample_start)/seconds)
                    after=amount if high>=stamp else int(amount*(high-sample_start)/seconds)
                    totals[(bucket,email)][i]+=max(0,after-before)
                bucket+=3600
        rows=[]
        for bucket in reversed(buckets):
            low,high=max(start,bucket),min(end,bucket+3600)
            label=datetime.fromtimestamp(low,TZ).strftime("%Y-%m-%d %H:%M")+"–"+datetime.fromtimestamp(high,TZ).strftime("%H:%M")
            for d in devices:
                up,down=totals[(bucket,d["email"])]
                observed=(bucket,d["email"]) in coverage
                rows.append({"day":label,"bucket_start":bucket,"start":low,"end":high,"email":d["email"],"label":d["label"],"up":up,"down":down,"total":up+down,"observed":observed})
        return {"granularity":"hour","timezone":"Asia/Shanghai","window_start":start,"window_end":end,
                "from":datetime.fromtimestamp(start,TZ).isoformat(),"to":datetime.fromtimestamp(end,TZ).isoformat(),"device":device,
                "rows":rows,"up":sum(r["up"] for r in rows),"down":sum(r["down"] for r in rows)}


def write_system(path, metrics):
    with database(path) as db:
        meta_set(db,"system_info",json.dumps(metrics))


def read_system(path, now=None):
    now=time.time() if now is None else now
    with database(path,readonly=True) as db:
        row=db.execute("SELECT value FROM meta WHERE key='system_info'").fetchone()
        result=json.loads(row[0]) if row else {"sampled_at":None}
        result["available"]=bool(result.get("sampled_at") and 0<=now-result["sampled_at"]<=20)
        return result


def summary(path, server, now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, TZ).date().isoformat()
    month = today[:7] + "-01"
    with database(path, readonly=True) as db:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        devices = []
        for row in db.execute("SELECT * FROM devices ORDER BY email").fetchall():
            policy = db.execute("SELECT * FROM limits WHERE email=?", (row["email"],)).fetchone()
            daily = db.execute("SELECT COALESCE(SUM(up),0),COALESCE(SUM(down),0) FROM daily WHERE email=? AND day=?", (row["email"], today)).fetchone()
            monthly = db.execute("SELECT COALESCE(SUM(up),0),COALESCE(SUM(down),0) FROM daily WHERE email=? AND day BETWEEN ? AND ?", (row["email"], month, today)).fetchone()
            devices.append({"id": row["email"], "label": row["label"], "active": bool(row["active"]),
                            "up": row["up"], "down": row["down"], "total": row["up"] + row["down"],
                            "today_up": daily[0], "today_down": daily[1], "month_up": monthly[0], "month_down": monthly[1],
                            "up_rate": row["up_rate"] if now - row["sampled"] < 90 else 0,
                            "down_rate": row["down_rate"] if now - row["sampled"] < 90 else 0,
                            "quota": policy["quota"], "period_up": policy["period_up"], "period_down": policy["period_down"],
                            "period_used": policy["period_up"] + policy["period_down"], "bonus": policy["bonus"],
                            "quota_effective": policy["quota"] + policy["bonus"] if policy["quota"] else 0,
                            "available": max(0,policy["quota"]+policy["bonus"]-policy["period_up"]-policy["period_down"]) if policy["quota"] else None,
                            "enabled": bool(policy["enabled"]), "blocked": policy["blocked"]})
        return {"version": VERSION, "server": server, "timezone": "Asia/Shanghai", "sample_interval": 30,
                "started": float(meta.get("started", now)), "last_sample": float(meta.get("last_success", 0)),
                "healthy": now - float(meta.get("last_success", 0)) < 90 and not meta.get("last_error"),
                "error": meta.get("last_error", ""), "devices": devices, "schedule":schedule(db),
                "manager_error": meta.get("manager_error", ""), "manager_healthy": now - float(meta.get("manager_seen",0)) < 15 and not meta.get("manager_error"), "quota_check_seconds": 2}
