# -*- coding: utf-8 -*-
"""KBO DB Builder — 공식 기록 수집(연도별) → SQLite + JSON + CSV + 사진/로고 + 게임 데이터.

사용법:
    python build.py                          # 2025+2026 전체 수집
    python build.py --years 2026             # 특정 연도만
    python build.py --photos --logos         # 선수 사진·팀 로고 다운로드(개인 이용 한정)
    python build.py --limit 2 --details-limit 5   # 빠른 테스트
출력: output/kbo.db, output/json/*.json, output/csv/*.csv,
      output/photos/, output/logos/, ../kbo_data.js (게임 연동용)
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys

import requests as _rq

from kbo_core import (Kbo, TEAM_CODES, iter_record_pages, parse_player_detail,
                      parse_record_table)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
GAME_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kbo_data.js")
IMG_BASE = "https://6ptotvmi5753.edge.naverncp.com/KBO_IMAGE"

PATHS = {
    "hit1": "/Record/Player/HitterBasic/Basic1.aspx",
    "hit2": "/Record/Player/HitterBasic/Basic2.aspx",
    "pit1": "/Record/Player/PitcherBasic/Basic1.aspx",
    "pit2": "/Record/Player/PitcherBasic/Basic2.aspx",
    "def":  "/Record/Player/Defense/Basic.aspx",
    "rank": "/Record/TeamRank/TeamRank.aspx",
}
TEAMS = [
    {"code": "LG", "team": "LG",   "fullName": "LG 트윈스",     "stadium": "잠실"},
    {"code": "OB", "team": "두산", "fullName": "두산 베어스",   "stadium": "잠실"},
    {"code": "SK", "team": "SSG",  "fullName": "SSG 랜더스",    "stadium": "인천"},
    {"code": "KT", "team": "KT",   "fullName": "KT 위즈",       "stadium": "수원"},
    {"code": "HT", "team": "KIA",  "fullName": "KIA 타이거즈",  "stadium": "광주"},
    {"code": "SS", "team": "삼성", "fullName": "삼성 라이온즈", "stadium": "대구"},
    {"code": "LT", "team": "롯데", "fullName": "롯데 자이언츠", "stadium": "부산"},
    {"code": "HH", "team": "한화", "fullName": "한화 이글스",   "stadium": "대전"},
    {"code": "NC", "team": "NC",   "fullName": "NC 다이노스",   "stadium": "창원"},
    {"code": "WO", "team": "키움", "fullName": "키움 히어로즈", "stadium": "고척"},
]
TEAM_BY_NAME = {t["team"]: t for t in TEAMS}


def f(v, default=0.0):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return default


def i(v, default=0):
    try:
        return int(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return default


def ip_to_float(ip):
    s = str(ip).strip()
    m = re.match(r"^(\d+)\s+(\d)/3$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 3.0
    m = re.match(r"^(\d)/3$", s)
    if m:
        return int(m.group(1)) / 3.0
    return f(s)


# ---------------- 수집 ----------------
def collect_records(kbo, path, label, max_pages, year=None):
    """규정타석/이닝 필터 회피를 위해 연도 선택 후 팀별 순회로 전 선수 수집."""
    print(f"[수집] {label} ({year})")
    merged = {}
    base = kbo.get(path)
    if year:
        sel = kbo.select_option(path, base, "ddlSeason", year)
        if sel is not None:
            base = sel
    for code, tname in TEAM_CODES.items():
        soup = kbo.select_team(path, base, code)
        if soup is None:
            soup = base
        n0 = len(merged)
        for _, rows in iter_record_pages(kbo, path, max_pages, log=lambda *_: None,
                                         soup=soup):
            for r in rows:
                pid = r.get("playerId")
                if pid:
                    merged.setdefault(pid, {}).update(r)
        print(f"    {tname}: +{len(merged)-n0}")
        if soup is base:
            break
    print(f"  → {len(merged)}명")
    return merged


def merge_two(a, b):
    out = dict(a)
    for pid, r in b.items():
        out.setdefault(pid, {}).update(r)
    return out


# ---------------- 파생 스탯 ----------------
def enrich_batting(b):
    pa = i(b.get("PA")); ab = i(b.get("AB")); h = i(b.get("H"))
    bb = i(b.get("BB")); so = i(b.get("SO")); hr = i(b.get("HR"))
    avg = f(b.get("AVG")); slg = f(b.get("SLG")); obp = f(b.get("OBP"))
    b["ISO"] = round(slg - avg, 3) if slg else 0.0
    b["BBpct"] = round(bb / pa * 100, 1) if pa else 0.0
    b["Kpct"] = round(so / pa * 100, 1) if pa else 0.0
    b["OPS"] = b.get("OPS") or (round(obp + slg, 3) if obp and slg else 0.0)
    b["HRrate"] = round(hr / pa * 100, 2) if pa else 0.0
    b["BABIP"] = round((h - hr) / max(1, ab - so - hr + i(b.get("SF"))), 3) if ab else 0.0
    return b


def enrich_pitching(p, lg_fip_const):
    ip = ip_to_float(p.get("IP", 0))
    bb = i(p.get("BB")); so = i(p.get("SO")); hr = i(p.get("HR"))
    hbp = i(p.get("HBP"))
    p["IPf"] = round(ip, 2)
    p["K9"] = round(so / ip * 9, 2) if ip else 0.0
    p["BB9"] = round(bb / ip * 9, 2) if ip else 0.0
    p["HR9"] = round(hr / ip * 9, 2) if ip else 0.0
    p["FIP"] = round((13 * hr + 3 * (bb + hbp) - 2 * so) / ip + lg_fip_const, 2) if ip else 0.0
    return p


def league_fip_const(pitchers):
    tot_ip = sum(ip_to_float(p.get("IP", 0)) for p in pitchers.values())
    if not tot_ip:
        return 3.2
    lg_era = sum(i(p.get("ER")) for p in pitchers.values()) / tot_ip * 9
    comp = (13 * sum(i(p.get("HR")) for p in pitchers.values())
            + 3 * sum(i(p.get("BB")) + i(p.get("HBP")) for p in pitchers.values())
            - 2 * sum(i(p.get("SO")) for p in pitchers.values())) / tot_ip
    return lg_era - comp


# ---------------- 게임 능력치 ----------------
def pct_rank(sorted_vals, v):
    if not sorted_vals:
        return 0.5
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def scale(p):
    return int(round(40 + 59 * max(0.0, min(1.0, p))))


def shrink(rating, n, full_n):
    w = min(1.0, n / full_n)
    return int(round(rating * w + 50 * (1 - w)))


def seeded(pid, salt, options):
    hsh = int(hashlib.md5(f"{pid}:{salt}".encode()).hexdigest(), 16)
    return options[hsh % len(options)]


def calc_age(birth, year):
    m = re.search(r"(\d{4})", str(birth))
    return year - int(m.group(1)) if m else None


def build_abilities(year, players, batting, pitching, fielding):
    b_avg = sorted(f(r.get("AVG")) for r in batting.values())
    b_iso = sorted(r["ISO"] for r in batting.values())
    b_bb = sorted(r["BBpct"] for r in batting.values())
    b_k = sorted(-r["Kpct"] for r in batting.values())
    b_spd = sorted(i(r.get("SB")) * 2 + i(r.get("3B")) * 3 for r in batting.values())
    p_k9 = sorted(r["K9"] for r in pitching.values())
    p_bb9 = sorted(-r["BB9"] for r in pitching.values())
    p_fip = sorted(-r["FIP"] for r in pitching.values())
    p_sta = sorted(ip_to_float(r.get("IP", 0)) / max(1, i(r.get("G"))) for r in pitching.values())
    f_fpct = sorted(f(r.get("FPCT")) for r in fielding.values())
    f_apg = sorted(i(r.get("A")) / max(1, i(r.get("G"))) for r in fielding.values())

    out = {}
    for pid in set(batting) | set(pitching) | set(fielding):
        pl = players.get(pid, {})
        a = {"playerId": pid, "year": year}
        b = batting.get(pid); p = pitching.get(pid); fd = fielding.get(pid)
        pa = i(b.get("PA")) if b else 0
        ip = ip_to_float(p.get("IP", 0)) if p else 0.0
        if b and pa > 0:
            a["Contact"] = shrink(scale(0.65 * pct_rank(b_avg, f(b.get("AVG"))) +
                                        0.35 * pct_rank(b_k, -b["Kpct"])), pa, 300)
            a["Power"] = shrink(scale(pct_rank(b_iso, b["ISO"])), pa, 300)
            a["Eye"] = shrink(scale(pct_rank(b_bb, b["BBpct"])), pa, 300)
            a["Speed"] = shrink(scale(pct_rank(b_spd, i(b.get("SB")) * 2 + i(b.get("3B")) * 3)),
                                pa, 300)
        if p and ip > 0:
            a["Fastball"] = shrink(scale(pct_rank(p_k9, p["K9"])), ip, 100)
            a["Control"] = shrink(scale(pct_rank(p_bb9, -p["BB9"])), ip, 100)
            a["BreakingBall"] = shrink(scale(0.5 * pct_rank(p_fip, -p["FIP"]) +
                                             0.5 * pct_rank(p_k9, p["K9"])), ip, 100)
            a["Stamina"] = shrink(scale(pct_rank(p_sta, ip / max(1, i(p.get("G"))))), ip, 100)
        if fd:
            g = i(fd.get("G"))
            a["Defense"] = shrink(scale(pct_rank(f_fpct, f(fd.get("FPCT")))), g, 60)
            a["Arm"] = shrink(scale(pct_rank(f_apg, i(fd.get("A")) / max(1, g))), g, 60)
        a.setdefault("Defense", 55); a.setdefault("Arm", 55)
        a["Agility"] = int(round(a.get("Speed", 55) * 0.6 + a["Defense"] * 0.4))
        age = calc_age(pl.get("birth", ""), year) or 27
        a["Potential"] = int(min(99, 55 + max(0, 30 - age) * 2.2 +
                                 int(hashlib.md5(f"{pid}:pot".encode()).hexdigest(), 16) % 15))
        if p and ip > 0 and (not b or ip * 3 > pa):
            a["Overall"] = int(round(a.get("Fastball", 50) * 0.30 + a.get("Control", 50) * 0.25 +
                                     a.get("BreakingBall", 50) * 0.20 + a.get("Stamina", 50) * 0.15 +
                                     a["Defense"] * 0.10))
            a["Role"] = "P"
        else:
            a["Overall"] = int(round(a.get("Contact", 50) * 0.28 + a.get("Power", 50) * 0.25 +
                                     a.get("Eye", 50) * 0.15 + a.get("Speed", 50) * 0.12 +
                                     a["Defense"] * 0.12 + a["Arm"] * 0.08))
            a["Role"] = "B"
        a["GrowthType"] = seeded(pid, "growth", ["조숙형", "정상형", "대기만성형"])
        a["Personality"] = seeded(pid, "pers", ["침착", "열정", "승부사", "분위기메이커", "노력파", "천재형"])
        out[pid] = a
    return out


# ---------------- 출력 ----------------
def export_json(name, data):
    d = os.path.join(OUT, "json")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)
    print(f"  json/{name}.json ({len(data)})")


def export_csv(name, rows, key_first="playerId"):
    d = os.path.join(OUT, "csv")
    os.makedirs(d, exist_ok=True)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not rows:
        return
    cols = [key_first] + sorted({k for r in rows for k in r} - {key_first})
    with open(os.path.join(d, f"{name}.csv"), "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  csv/{name}.csv")


def export_sqlite(tables):
    path = os.path.join(OUT, "kbo.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    for name, rows in tables.items():
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not rows:
            continue
        cols = sorted({k for r in rows for k in r})
        con.execute(f'CREATE TABLE {name} ({",".join(chr(34)+c+chr(34) for c in cols)})')
        con.executemany(f'INSERT INTO {name} VALUES ({",".join("?"*len(cols))})',
                        [[r.get(c) for c in cols] for r in rows])
    con.commit()
    con.close()
    print("  kbo.db")


def dl(url, path):
    try:
        r = _rq.get(url, timeout=15, headers={"User-Agent": "KBO-DB-Builder(personal)"})
        if r.ok and len(r.content) > 500:
            open(path, "wb").write(r.content)
            return True
    except _rq.RequestException:
        pass
    return False


def download_photos(players):
    d = os.path.join(OUT, "photos")
    n = 0
    for pid, pl in players.items():
        url = pl.get("photoUrl")
        if not url:
            continue
        team = pl.get("team") or "misc"
        td = os.path.join(d, team)
        os.makedirs(td, exist_ok=True)
        fp = os.path.join(td, f"{pid}.jpg")
        if os.path.exists(fp) or dl(url, fp):
            pl["photoFile"] = f"photos/{team}/{pid}.jpg"
            n += 1
    print(f"  photos: {n}/{len(players)}")


def download_logos(year):
    d = os.path.join(OUT, "logos")
    os.makedirs(d, exist_ok=True)
    n = 0
    for t in TEAMS:
        for kind in ("initial", "emblem"):
            url = f"{IMG_BASE}/emblem/regular/{year}/{kind}_{t['code']}.png"
            fp = os.path.join(d, f"{kind}_{t['code']}.png")
            if os.path.exists(fp) or dl(url, fp):
                t[f"logo_{kind}"] = f"logos/{kind}_{t['code']}.png"
                n += 1
    print(f"  logos: {n}")


def parse_hand(pos):
    """'내야수(우투좌타)' → (투, 타). 언더핸드('우언')도 처리."""
    m = re.search(r"([좌우양])[투언]", pos or "")
    t = m.group(1) if m else "우"
    m2 = re.search(r"([좌우양])타", pos or "")
    b = m2.group(1) if m2 else "우"
    return t, b


def export_game_js(year, players, batting, pitching, abilities):
    """게임(index.html)이 file://에서 바로 읽는 kbo_data.js 생성."""
    teams_out = []
    for t in TEAMS:
        tname = t["team"]
        bats = [(pid, b) for pid, b in batting.items()
                if b.get("팀명") == tname and abilities.get(pid, {}).get("Role") == "B"]
        bats.sort(key=lambda x: -i(x[1].get("PA")))
        lineup = []
        for pid, b in bats[:9]:
            a = abilities.get(pid, {})
            pl = players.get(pid, {})
            lineup.append({"id": pid, "name": pl.get("name") or b.get("선수명", ""),
                           "pos": (pl.get("position") or "").split("(")[0],
                           "bats": parse_hand(pl.get("position"))[1],
                           "photo": pl.get("photoFile", ""),
                           "AVG": b.get("AVG"), "HR": i(b.get("HR")), "OPS": b.get("OPS"),
                           **{k: a.get(k, 55) for k in
                              ("Contact", "Power", "Eye", "Speed", "Defense", "Overall")}})
        pits = [(pid, p) for pid, p in pitching.items() if p.get("팀명") == tname]
        pits.sort(key=lambda x: -x[1]["IPf"])
        rot = []
        for pid, p in pits[:5]:
            a = abilities.get(pid, {})
            pl = players.get(pid, {})
            rot.append({"id": pid, "name": pl.get("name") or p.get("선수명", ""),
                        "throws": parse_hand(pl.get("position"))[0],
                        "photo": pl.get("photoFile", ""),
                        "ERA": p.get("ERA"), "FIP": p.get("FIP"),
                        **{k: a.get(k, 55) for k in
                           ("Fastball", "Control", "BreakingBall", "Stamina", "Overall")}})
        teams_out.append({"code": t["code"], "team": tname, "fullName": t["fullName"],
                          "logo": t.get("logo_initial", ""),
                          "lineup": lineup, "pitchers": rot})
    data = {"year": year, "base": "KBO_DB_Builder/output/", "teams": teams_out}
    js = "window.KBO=" + json.dumps(data, ensure_ascii=False) + ";\n"
    with open(os.path.join(OUT, "kbo_game.js"), "w", encoding="utf-8") as fp:
        fp.write(js)
    with open(GAME_JS, "w", encoding="utf-8") as fp:
        fp.write(js)
    print(f"  kbo_game.js + ../kbo_data.js ({len(teams_out)}팀)")


# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2025,2026")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-details", action="store_true")
    ap.add_argument("--details-limit", type=int, default=None)
    ap.add_argument("--photos", action="store_true")
    ap.add_argument("--logos", action="store_true")
    args = ap.parse_args()
    years = [int(y) for y in args.years.split(",")]
    latest = max(years)

    os.makedirs(OUT, exist_ok=True)
    kbo = Kbo()
    players = {}
    season = {}  # year → dict(hit/pit/fld/abilities)
    for y in years:
        hit = merge_two(collect_records(kbo, PATHS["hit1"], "타자 기본1", args.limit, y),
                        collect_records(kbo, PATHS["hit2"], "타자 기본2", args.limit, y))
        pit = merge_two(collect_records(kbo, PATHS["pit1"], "투수 기본1", args.limit, y),
                        collect_records(kbo, PATHS["pit2"], "투수 기본2", args.limit, y))
        fld = collect_records(kbo, PATHS["def"], "수비", args.limit, y)
        for b in hit.values():
            enrich_batting(b)
        c = league_fip_const(pit)
        for p in pit.values():
            enrich_pitching(p, c)
        for pid, r in list(hit.items()) + list(pit.items()) + list(fld.items()):
            pl = players.setdefault(pid, {"playerId": pid})
            pl.setdefault("name", r.get("선수명", ""))
            if y == latest or "team" not in pl:
                pl["team"] = r.get("팀명", pl.get("team", ""))
        season[y] = {"hit": hit, "pit": pit, "fld": fld}
    print(f"[선수 마스터] {len(players)}명 (연도 합산)")

    if not args.skip_details:
        pids = list(players)
        if args.details_limit:
            pids = pids[:args.details_limit]
        print(f"[수집] 선수 상세 프로필 {len(pids)}명 (약 {len(pids)*0.9/60:.0f}분 예상)")
        for n, pid in enumerate(pids, 1):
            kind = "HitterDetail" if any(pid in season[y]["hit"] for y in years) else "PitcherDetail"
            try:
                soup = kbo.get(f"/Record/Player/{kind}/Basic.aspx?playerId={pid}")
                prof = parse_player_detail(soup)
                prof.pop("name", None)
                players[pid].update(prof)
                players[pid]["age"] = calc_age(prof.get("birth", ""), latest)
            except Exception as e:
                print(f"    ! {pid}: {e}")
            if n % 50 == 0:
                print(f"    {n}/{len(pids)}")

    standings = []
    try:
        _, standings = parse_record_table(kbo.get(PATHS["rank"]))
    except Exception as e:
        print(f"[순위] 실패: {e}")

    print("[출력]")
    if args.photos:
        download_photos(players)
    if args.logos:
        download_logos(latest)

    bat_rows, pit_rows, fld_rows, abl_rows = [], [], [], []
    abilities_latest = {}
    for y in years:
        s = season[y]
        abl = build_abilities(y, players, s["hit"], s["pit"], s["fld"])
        if y == latest:
            abilities_latest = abl
        for pid, r in s["hit"].items():
            bat_rows.append({"year": y, **r})
        for pid, r in s["pit"].items():
            pit_rows.append({"year": y, **r})
        for pid, r in s["fld"].items():
            fld_rows.append({"year": y, **r})
        abl_rows.extend(abl.values())

    export_json("players", list(players.values()))
    export_json("batting", bat_rows)
    export_json("pitching", pit_rows)
    export_json("fielding", fld_rows)
    export_json("abilities", abl_rows)
    export_json("teams", TEAMS)
    export_json("standings", standings)
    export_csv("players", players)
    export_csv("batting", bat_rows)
    export_csv("pitching", pit_rows)
    export_csv("fielding", fld_rows)
    export_csv("abilities", abl_rows)
    export_sqlite({"players": players, "batting": bat_rows, "pitching": pit_rows,
                   "fielding": fld_rows, "abilities": abl_rows,
                   "teams": TEAMS, "standings": standings})
    export_game_js(latest, players, season[latest]["hit"], season[latest]["pit"],
                   abilities_latest)
    print(f"완료: {OUT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
