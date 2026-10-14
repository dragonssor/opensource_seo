#!/usr/bin/env python3
"""서울 금천구 가산동 복합 상가 중 상세주소(동·층·호)가 부여되지 않은 건물 찾기.

도로명주소 안내시스템(juso.go.kr / business.juso.go.kr) '주소정보 제공'에서 내려받은
  1) 건물DB             (예: build_서울특별시.txt)            -- 필수
  2) 상세주소DB          (상세주소 표시 / 상세주소 동 표시)      -- 필수, 여러 개 가능
  3) 건축물대장 표제부     (건축HUB/세움터, CSV)                  -- 선택(용도 판정용)
를 건물 단위로 매칭하여, 가산동 '복합 상가' 후보 중 상세주소DB에 한 건도 없는 건물을 CSV로 출력한다.

건물 매칭 키는 juso 전 데이터셋에 공통으로 들어 있는 도로명주소 키
  도로명코드(12) | 지하여부 | 건물본번 | 건물부번
를 사용한다. 표준 라이브러리만 사용하며, .txt/.zip 모두 그대로 읽는다.
"""

import argparse
import csv
import io
import re
import sys
import zipfile
from collections import defaultdict

GASAN_BJD_CODE = "1154510100"  # 서울특별시 금천구 가산동 법정동코드

# 건물DB(build_*.txt) 기본 레이아웃 (0-based 컬럼 인덱스, '|' 구분, 헤더 없음).
# 레이아웃이 바뀌면 --building-cols 로 덮어쓴다.
BUILDING_LAYOUT = {
    "bjd_code": 0,        # 법정동코드
    "sido": 1,            # 시도명
    "sigungu": 2,         # 시군구명
    "emd": 3,             # 법정읍면동명
    "san": 5,             # 산여부
    "jibun_main": 6,      # 지번본번
    "jibun_sub": 7,       # 지번부번
    "road_code": 8,       # 도로명코드
    "road_name": 9,       # 도로명
    "underground": 10,    # 지하여부
    "bld_main": 11,       # 건물본번
    "bld_sub": 12,        # 건물부번
    "bld_name": 13,       # 건축물대장 건물명
    "bld_name_detail": 14,  # 상세건물명
    "bd_mgt_sn": 15,      # 건물관리번호
    "sigungu_bld_name": 25,  # 시군구용 건물명
    "apt_yn": 26,         # 공동주택여부
    "detail_yn": 28,      # 상세주소부여여부
}
BUILDING_REQUIRED = ("bjd_code", "road_code", "underground", "bld_main", "bld_sub")

# 건축물대장 표제부 CSV 헤더 별칭 (건축HUB 일괄제공 한글 컬럼명 / Open API 필드명)
REGISTER_ALIASES = {
    "road_code": ["새주소_도로_코드", "새주소도로코드", "naRoadCd"],
    "underground": ["새주소_지상지하_코드", "새주소지상지하코드", "naUgrndCd"],
    "bld_main": ["새주소_본_번", "새주소본번", "naMainBun"],
    "bld_sub": ["새주소_부_번", "새주소부번", "naSubBun"],
    "bjd_code": ["법정동_코드", "법정동코드", "bjdongCd"],
    "sigungu_code": ["시군구_코드", "시군구코드", "sigunguCd"],
    "regstr_kind": ["대장_종류_코드_명", "대장종류코드명", "regstrKindCdNm"],
    "main_purpose": ["주_용도_코드_명", "주용도코드명", "mainPurpsCdNm"],
    "etc_purpose": ["기타_용도", "기타용도", "etcPurps"],
    "ho_cnt": ["호_수", "호수", "hoCnt"],
    "hhld_cnt": ["가구_수", "가구수", "fmlyCnt"],
    "ground_floors": ["지상_층_수", "지상층수", "grndFlrCnt"],
    "bld_name": ["건물_명", "건물명", "bldNm"],
    "dong_name": ["동_명", "동명칭", "dongNm"],
}

# 복합 상가 판정 기본값
COMMERCIAL_KEYWORDS = ("근린생활시설", "판매시설", "업무시설", "숙박시설", "위락시설", "지원시설")
NAME_KEYWORDS = ("상가", "프라자", "플라자", "타워", "빌딩", "센터", "몰", "스퀘어",
                 "테크노", "밸리", "디지털", "지식산업", "아울렛", "오피스")


# --------------------------------------------------------------------------- I/O
def _decode(raw, encoding):
    encodings = [encoding] if encoding else ["utf-8-sig", "cp949"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


def iter_text_files(path, encoding=None):
    """(이름, 텍스트) 를 생성. zip 이면 내부의 .txt/.csv 를 모두 읽는다."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith((".txt", ".csv")):
                    continue
                yield f"{path}!{info.filename}", _decode(zf.read(info), encoding)
    else:
        with open(path, "rb") as f:
            yield path, _decode(f.read(), encoding)


def iter_pipe_rows(path, encoding=None):
    for _, text in iter_text_files(path, encoding):
        for line in text.splitlines():
            if line.strip():
                yield [c.strip() for c in line.split("|")]


def _int_str(v):
    v = (v or "").strip()
    try:
        return str(int(float(v)))
    except ValueError:
        return v


def make_key(road_code, underground, main, sub):
    """도로명코드|지하여부|건물본번|건물부번 (숫자 정규화)."""
    ug = _int_str(underground)
    if ug in ("", "N"):
        ug = "0"
    elif ug == "Y":
        ug = "1"
    return "|".join([(road_code or "").strip(), ug, _int_str(main), _int_str(sub or "0")])


def parse_cols(spec):
    """'road_code=8,underground=10' -> dict (1-based 입력을 0-based 로)."""
    out = {}
    if not spec:
        return out
    for part in spec.split(","):
        name, idx = part.split("=")
        out[name.strip()] = int(idx) - 1
    return out


# ---------------------------------------------------------------- 건물DB
def load_buildings(paths, bjd_code, cols_override=None, encoding=None):
    layout = dict(BUILDING_LAYOUT)
    layout.update(cols_override or {})
    buildings = {}
    for path in paths:
        for row in iter_pipe_rows(path, encoding):
            def g(name):
                i = layout.get(name)
                return row[i] if i is not None and i < len(row) else ""
            if g("bjd_code") != bjd_code:
                continue
            road = g("road_code")
            if not re.fullmatch(r"\d{12}", road):
                raise SystemExit(
                    f"[건물DB] 도로명코드 컬럼({layout['road_code'] + 1}번째) 값이 12자리 숫자가 아닙니다: {road!r}\n"
                    "  --building-cols 로 컬럼 위치(1부터)를 지정하세요. 예) --building-cols road_code=9,underground=11")
            key = make_key(road, g("underground"), g("bld_main"), g("bld_sub"))
            rec = buildings.get(key)
            if rec is None:
                main, sub = _int_str(g("bld_main")), _int_str(g("bld_sub"))
                ug = "지하 " if key.split("|")[1] == "1" else ""
                jm, js = _int_str(g("jibun_main")), _int_str(g("jibun_sub"))
                rec = {
                    "key": key,
                    "bd_mgt_sn": g("bd_mgt_sn"),
                    "road_address": f"{g('sido')} {g('sigungu')} {g('road_name')} {ug}{main}"
                                    + (f"-{sub}" if sub not in ("", "0") else ""),
                    "jibun_address": f"{g('sido')} {g('sigungu')} {g('emd')} "
                                     + ("산" if g("san") == "1" else "") + jm
                                     + (f"-{js}" if js not in ("", "0") else ""),
                    "bld_names": [],
                    "apt_yn": g("apt_yn"),
                    "detail_yn_flag": g("detail_yn"),
                }
                buildings[key] = rec
            for n in (g("bld_name"), g("bld_name_detail"), g("sigungu_bld_name")):
                if n and n not in rec["bld_names"]:
                    rec["bld_names"].append(n)
    return buildings


# ---------------------------------------------------------------- 상세주소DB
def detect_detail_layout(rows, sigungu_prefix):
    """상세주소DB 레이아웃 자동 탐지: 도로명코드(12자리, 시군구코드로 시작) 다음에
    오는 첫 번째 0/1 컬럼을 지하여부, 그 뒤 숫자 두 컬럼을 건물본번·부번으로 본다."""
    sample = [r for r in rows if r][:2000]
    if not sample:
        raise SystemExit("[상세주소DB] 데이터가 비어 있습니다.")
    width = min(len(r) for r in sample)

    def all_match(i, pat):
        return all(re.fullmatch(pat, r[i] or "") for r in sample)

    road_cols = [i for i in range(width)
                 if all_match(i, r"\d{12}") and any(r[i].startswith(sigungu_prefix[:2]) for r in sample)]
    for rc in road_cols:
        rest = range(rc + 1, width)
        ug = next((i for i in rest if all_match(i, r"[012]")), None)
        if ug is None:
            continue
        nums = [i for i in range(ug + 1, width) if all_match(i, r"\d{1,5}")]
        if len(nums) >= 2:
            return {"road_code": rc, "underground": ug, "bld_main": nums[0], "bld_sub": nums[1]}
    raise SystemExit("[상세주소DB] 도로명코드/지하여부/건물본번/건물부번 컬럼을 자동 탐지하지 못했습니다.\n"
                     "  --detail-cols road_code=?,underground=?,bld_main=?,bld_sub=? (1부터) 로 지정하세요.")


def load_detail_counts(paths, sigungu_prefix, cols_override=None, encoding=None, verbose=True):
    """건물 키별 상세주소 건수를 센다 (금천구 도로명코드만)."""
    counts = defaultdict(int)
    for path in paths:
        rows = (r for r in iter_pipe_rows(path, encoding))
        head = []
        for r in rows:
            head.append(r)
            if len(head) >= 2000:
                break
        data_head = [r for r in head if any(re.fullmatch(r"\d{12}", c) for c in r)]
        layout = cols_override if cols_override else detect_detail_layout(data_head, sigungu_prefix)
        if verbose:
            print(f"[상세주소DB] {path}: 컬럼(1부터) "
                  + ", ".join(f"{k}={v + 1}" for k, v in layout.items()), file=sys.stderr)
        for r in head + list(rows):
            try:
                road = r[layout["road_code"]]
            except IndexError:
                continue
            if not road.startswith(sigungu_prefix):
                continue
            key = make_key(road, r[layout["underground"]], r[layout["bld_main"]], r[layout["bld_sub"]])
            counts[key] += 1
    return counts


# ---------------------------------------------------------------- 건축물대장
def load_register(paths, encoding=None):
    """표제부 CSV(헤더 있음) -> 키별 레코드 목록."""
    reg = defaultdict(list)
    for path in paths:
        for _, text in iter_text_files(path, encoding):
            reader = csv.reader(io.StringIO(text), delimiter="|" if text.split("\n", 1)[0].count("|") > 3 else ",")
            header = next(reader, [])
            idx = {}
            for field, aliases in REGISTER_ALIASES.items():
                for a in aliases:
                    if a in header:
                        idx[field] = header.index(a)
                        break
            missing = [f for f in ("road_code", "underground", "bld_main", "bld_sub") if f not in idx]
            if missing:
                raise SystemExit(f"[건축물대장] {path}: 필요한 컬럼을 찾지 못했습니다: {missing}")
            for row in reader:
                rec = {f: (row[i].strip() if i < len(row) else "") for f, i in idx.items()}
                if not re.fullmatch(r"\d{12}", rec["road_code"]):
                    # 일부 데이터는 새주소 도로코드가 시군구코드와 분리되어 있음(7자리)
                    if re.fullmatch(r"\d{7}", rec["road_code"]) and rec.get("sigungu_code"):
                        rec["road_code"] = rec["sigungu_code"] + rec["road_code"]
                    else:
                        continue
                key = make_key(rec["road_code"], rec["underground"], rec["bld_main"], rec["bld_sub"])
                reg[key].append(rec)
    return reg


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def classify(building, reg_recs, min_ho, min_floors):
    """복합 상가 여부와 근거를 반환."""
    if reg_recs:
        purposes = " ".join(r.get("main_purpose", "") + " " + r.get("etc_purpose", "") for r in reg_recs)
        commercial = any(k in purposes for k in COMMERCIAL_KEYWORDS)
        ho = sum(_to_int(r.get("ho_cnt")) for r in reg_recs)
        floors = max(_to_int(r.get("ground_floors")) for r in reg_recs)
        multi = ho >= min_ho or floors >= min_floors or len(reg_recs) >= 2
        ok = commercial and multi
        reason = (f"용도:{'상업' if commercial else '비상업'}, 호수:{ho}, 지상층수:{floors}, 동수:{len(reg_recs)}")
        return ok, "건축물대장", reason
    names = " ".join(building["bld_names"])
    hit = [k for k in NAME_KEYWORDS if k in names]
    return bool(hit), "건물명(대장 미매칭)", ("건물명 키워드:" + ",".join(hit)) if hit else "키워드 없음"


# ---------------------------------------------------------------- main
def run(args):
    bld_cols = parse_cols(args.building_cols)
    det_cols = parse_cols(args.detail_cols)
    buildings = load_buildings(args.building, args.bjd_code, bld_cols, args.encoding)
    if not buildings:
        raise SystemExit(f"[건물DB] 법정동코드 {args.bjd_code} 건물이 없습니다. 파일/코드를 확인하세요.")
    counts = load_detail_counts(args.detail, args.bjd_code[:5], det_cols or None, args.encoding)
    register = load_register(args.register, args.encoding) if args.register else {}

    out_rows, stats = [], defaultdict(int)
    for key, b in sorted(buildings.items()):
        recs = register.get(key, [])
        is_mixed, basis, reason = classify(b, recs, args.min_ho, args.min_floors)
        n_detail = counts.get(key, 0)
        stats["가산동 건물"] += 1
        stats["상세주소 있음"] += n_detail > 0
        if not is_mixed:
            continue
        stats["복합상가 후보"] += 1
        if n_detail > 0 and not args.all:
            continue
        stats["복합상가 & 상세주소 없음"] += n_detail == 0
        out_rows.append({
            "건물관리번호": b["bd_mgt_sn"],
            "도로명주소": b["road_address"],
            "지번주소": b["jibun_address"],
            "건물명": " / ".join(b["bld_names"]),
            "대장종류": ",".join(sorted({r.get("regstr_kind", "") for r in recs} - {""})),
            "주용도": ",".join(sorted({r.get("main_purpose", "") for r in recs} - {""})),
            "기타용도": " / ".join(sorted({r.get("etc_purpose", "") for r in recs} - {""})),
            "판정근거": basis,
            "판정상세": reason,
            "상세주소건수": n_detail,
            "건물DB_상세주소부여여부": b["detail_yn_flag"],
            "결과": "상세주소 미부여" if n_detail == 0 else "상세주소 부여",
            "매칭키": key,
        })

    fields = list(out_rows[0].keys()) if out_rows else ["건물관리번호", "도로명주소", "결과"]
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    for k, v in stats.items():
        print(f"{k}: {v}", file=sys.stderr)
    print(f"결과 저장: {args.output} ({len(out_rows)}건)", file=sys.stderr)
    return out_rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--building", nargs="+", required=True, help="건물DB 파일(.txt/.zip)")
    p.add_argument("--detail", nargs="+", required=True, help="상세주소DB 파일(.txt/.zip), 여러 개 가능")
    p.add_argument("--register", nargs="*", default=[], help="건축물대장 표제부 CSV(선택)")
    p.add_argument("--output", default="gasan_no_detail_address.csv")
    p.add_argument("--bjd-code", default=GASAN_BJD_CODE, help="법정동코드 (기본: 가산동 1154510100)")
    p.add_argument("--building-cols", help="건물DB 컬럼 위치 덮어쓰기(1부터), 예: road_code=9,bld_main=12")
    p.add_argument("--detail-cols", help="상세주소DB 컬럼 위치(1부터) 직접 지정")
    p.add_argument("--encoding", help="강제 인코딩 (기본: utf-8 → cp949 자동)")
    p.add_argument("--min-ho", type=int, default=2, help="복합 상가로 볼 최소 호수 (기본 2)")
    p.add_argument("--min-floors", type=int, default=2, help="복합 상가로 볼 최소 지상층수 (기본 2)")
    p.add_argument("--all", action="store_true", help="상세주소가 있는 복합상가도 함께 출력")
    run(p.parse_args(argv))


if __name__ == "__main__":
    main()
