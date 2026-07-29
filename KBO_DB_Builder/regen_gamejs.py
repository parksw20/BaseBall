# -*- coding: utf-8 -*-
"""수집 없이 output/json/*에서 kbo_data.js만 재생성 (게임 데이터 포맷 변경 시 사용)."""
import json
import os
import sys

import build

J = os.path.join(build.OUT, "json")


def load(name):
    with open(os.path.join(J, f"{name}.json"), encoding="utf-8") as fp:
        return json.load(fp)


def main():
    year = 2026
    # 로고 경로는 다운로드 단계에서 TEAMS에 붙으므로, 재생성 시 teams.json에서 병합
    saved = {t["code"]: t for t in load("teams")}
    for t in build.TEAMS:
        for k in ("logo_initial", "logo_emblem"):
            if k in saved.get(t["code"], {}):
                t[k] = saved[t["code"]][k]
    players = {p["playerId"]: p for p in load("players")}
    batting = {r["playerId"]: r for r in load("batting") if r.get("year") == year}
    pitching = {r["playerId"]: r for r in load("pitching") if r.get("year") == year}
    abilities = {a["playerId"]: a for a in load("abilities") if a.get("year") == year}
    build.export_game_js(year, players, batting, pitching, abilities)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
