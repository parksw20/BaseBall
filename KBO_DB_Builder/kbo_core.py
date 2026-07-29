# -*- coding: utf-8 -*-
"""KBO 공식 기록 사이트(koreabaseball.com) 수집 코어.

- 서버 렌더링 HTML 테이블 파싱 (기록 페이지)
- ASP.NET __doPostBack 페이지네이션 처리 (__VIEWSTATE 왕복)
- 선수 상세(프로필/사진 URL) 파싱
robots.txt 기준 /Record, /Player 경로는 수집 허용 범위다 (/ws 등은 사용하지 않음).
"""
import re
import time
import requests
from bs4 import BeautifulSoup

BASE = "https://www.koreabaseball.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 KBO-DB-Builder(personal)")
DELAY = 0.4  # 요청 간 딜레이(초) — 사이트 부담 최소화


class Kbo:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def get(self, path):
        time.sleep(DELAY)
        r = self.s.get(BASE + path, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")

    def postback(self, path, soup, target, extra=None):
        """현재 soup의 hidden 필드를 유지한 채 __doPostBack(target) 재현.
        extra: 드롭다운 선택 변경 등 덮어쓸 폼 값 {name: value}."""
        data = {}
        for inp in soup.select("input[type=hidden]"):
            data[inp.get("name", "")] = inp.get("value", "")
        # select(드롭다운) 현재값 유지
        for sel in soup.select("select"):
            name = sel.get("name")
            if not name:
                continue
            opt = sel.find("option", selected=True) or sel.find("option")
            if opt is not None:
                data[name] = opt.get("value", "")
        if extra:
            data.update(extra)
        data["__EVENTTARGET"] = target
        data["__EVENTARGUMENT"] = ""
        time.sleep(DELAY)
        r = self.s.post(BASE + path, data=data, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")

    def select_option(self, path, soup, ddl_substr, value):
        """이름에 ddl_substr가 포함된 드롭다운을 value로 선택(postback)."""
        ddl = None
        for sel in soup.select("select"):
            if ddl_substr in (sel.get("name") or ""):
                ddl = sel.get("name")
                break
        if not ddl:
            return None
        return self.postback(path, soup, ddl, extra={ddl: str(value)})

    def select_team(self, path, soup, team_code):
        """팀 드롭다운(ddlTeam)을 선택해 해당 팀 전체 선수 목록으로 전환."""
        return self.select_option(path, soup, "ddlTeam", team_code)


def find_postback_target(soup, id_suffix):
    """href의 __doPostBack('타깃','') 중 타깃이 id_suffix로 끝나는 것을 찾는다."""
    for a in soup.select("a[href*=doPostBack]"):
        m = re.search(r"__doPostBack\('([^']+)'", a.get("href", ""))
        if m and m.group(1).replace("$", "_").endswith(id_suffix):
            return m.group(1)
    return None


def parse_record_table(soup):
    """기록 테이블 → (컬럼 리스트, 행 리스트). 행에는 playerId를 붙인다."""
    table = soup.select_one("table.tData01") or soup.select_one("div.record_result table") \
        or soup.select_one("table")
    if table is None:
        return [], []
    cols = [th.get_text(strip=True) for th in table.select("thead th")]
    rows = []
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        vals = [td.get_text(strip=True) for td in tds]
        row = dict(zip(cols, vals))
        a = tr.select_one("a[href*=playerId]")
        if a:
            m = re.search(r"playerId=(\d+)", a["href"])
            if m:
                row["playerId"] = m.group(1)
        rows.append(row)
    return cols, rows


TEAM_CODES = {"SS": "삼성", "KT": "KT", "LG": "LG", "OB": "두산", "HT": "KIA",
              "HH": "한화", "NC": "NC", "LT": "롯데", "SK": "SSG", "WO": "키움"}


def iter_record_pages(kbo, path, max_pages=None, log=print, soup=None):
    """기록 페이지를 1페이지부터 순회. 페이저 postback을 자동 추적.
    soup을 주면(팀 필터 적용 상태 등) 그 지점부터 순회한다."""
    if soup is None:
        soup = kbo.get(path)
    page = 1
    seen_first = None
    while True:
        cols, rows = parse_record_table(soup)
        first_key = rows[0].get("선수명", "") + rows[0].get("playerId", "") if rows else ""
        if not rows or first_key == seen_first:
            break
        seen_first = first_key
        log(f"    page {page}: {len(rows)} rows")
        yield cols, rows
        if max_pages and page >= max_pages:
            break
        # 다음 페이지: btnNo{n+1} → 없으면 btnNext(다음 그룹)
        target = find_postback_target(soup, f"btnNo{page + 1}")
        if not target and len(rows) >= 20:
            target = find_postback_target(soup, "btnNext")
        if not target:
            break
        soup = kbo.postback(path, soup, target)
        page += 1


PROFILE_KEYS = {
    "선수명": "name", "등번호": "backNo", "생년월일": "birth", "포지션": "position",
    "신장/체중": "heightWeight", "경력": "career", "입단 계약금": "signingBonus",
    "연봉": "salary", "지명순위": "draft", "입단년도": "proYear",
}


def parse_player_detail(soup):
    """선수 상세 페이지 → 프로필 dict + 사진 URL."""
    out = {}
    box = soup.select_one("div.player_basic") or soup
    for li in box.select("li"):
        txt = li.get_text(" ", strip=True)
        if ":" in txt:
            k, _, v = txt.partition(":")
            k = k.strip()
            if k in PROFILE_KEYS:
                out[PROFILE_KEYS[k]] = v.strip()
    for dt in box.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            k = dt.get_text(strip=True)
            if k in PROFILE_KEYS:
                out[PROFILE_KEYS[k]] = dd.get_text(strip=True)
    img = None
    for im in soup.select("img"):
        src = im.get("src", "")
        if "KBO_IMAGE/person" in src:
            img = src
            break
    if img and img.startswith("//"):
        img = "https:" + img
    out["photoUrl"] = img or ""
    return out
