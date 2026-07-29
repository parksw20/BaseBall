# KBO DB Builder

KBO 공식 기록 사이트(koreabaseball.com)에서 2026 시즌 데이터를 수집해
**SQLite + JSON + CSV**로 만드는 파이프라인. 야구 게임용 능력치 자동 산정 포함.

## 실행

```bash
pip install -r requirements.txt
python build.py                          # 2025+2026 전체 수집 (약 15~25분)
python build.py --years 2026             # 특정 연도만
python build.py --photos --logos         # + 선수 사진·팀 로고 (개인 이용 한정)
python build.py --limit 2 --details-limit 5   # 빠른 테스트
```

수집이 끝나면 게임 폴더에 `kbo_data.js`가 생성되어 **게임(index.html)에서 자동으로
실명 팀·라인업·능력치·선수 사진을 사용**한다(파일이 없으면 기본 팀으로 동작).

## 출력 (output/)

| 파일 | 내용 |
|---|---|
| `kbo.db` | SQLite — players/batting/pitching/fielding/abilities(연도 컬럼 포함)/teams/standings |
| `json/*.json` | 위 테이블들의 JSON (시즌 기록·능력치는 `year` 필드로 연도 구분) |
| `csv/*.csv` | UTF-8(BOM) CSV — 엑셀/Unity/Unreal DataTable 임포트용 (첫 컬럼 playerId=RowName) |
| `photos/팀/선수ID.jpg` | 공식 프로필 사진 (`--photos` 시) |
| `logos/initial_XX.png` 등 | 팀 엠블럼/이니셜 로고 (`--logos` 시) |
| `kbo_game.js` + `../kbo_data.js` | 게임 연동용 — 최신 연도 팀별 라인업 9명 + 투수 5명 + 능력치 |

## 수집 항목

- **타자**: AVG G PA AB R H 2B 3B HR TB RBI SAC SF BB IBB HBP SO GDP SLG OBP OPS SB CS 등
  + 파생: ISO, BB%, K%, HR율, BABIP
- **투수**: ERA W L SV HLD WPCT IP H HR BB HBP SO R ER WHIP QS 등
  + 파생: K/9, BB/9, HR/9, K%, BB%, **FIP**(리그 상수 자동 산출)
- **수비**: G PO A E FPCT 등
- **프로필**: 생년월일/나이, 포지션, 신장/체중, 경력(출신교), 지명순위, 입단년도, 연봉, 등번호, 사진 URL
- **게임 능력치(40~99, 리그 백분위 기반)**: Contact Power Eye Speed / Fastball Control
  BreakingBall Stamina / Defense Arm Agility / Potential(나이 기반) Overall Role
  GrowthType·Personality(선수ID 시드 고정 랜덤 — 실행할 때마다 동일)
  - 표본 부족(타석/이닝 적음)은 50 쪽으로 수축해 과대평가 방지

## 한계 (정직하게)

- **wRC+/WAR/UZR/DRS 없음** — KBO 공식 기록에 없는 지표. 사설 사이트(STATIZ) 크롤링은
  약관·구조 취약성 문제로 제외. FIP는 공식 데이터로 직접 계산해 포함.
- **타석별/투구별 기록 미포함** — 문자중계 데이터 대량 크롤링은 서버 부담이 커서 v1 제외.
- **경기 일정** — 일정 페이지는 JS 렌더링이라 v1 제외(순위표는 포함).
- 혈액형·부상 등 비정형 정보 제외.

## ⚠️ 이용 범위

선수 실명·사진·팀명/로고에는 **저작권 및 퍼블리시티권**이 있습니다.
개인 프로토타입/학습 용도로만 사용하고, 게임을 배포·판매하려면 KBO/KBOPA 라이선스가 필요합니다.
수집기는 요청 간 0.4초 딜레이로 서버 부담을 최소화합니다.

## 자동 갱신

Windows 작업 스케줄러에 `python build.py --skip-details` 를 매일 등록하면
시즌 기록이 자동 갱신됩니다(프로필은 자주 안 바뀌므로 주 1회 전체 실행 권장).
