"""합성(가짜) 픽스처로 매칭 로직을 검증한다. 실제 juso 데이터가 아님."""
import csv
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import match_detail_address as m  # noqa: E402


def bld_row(bjd, road, ug, main, sub, name, mgt, detail_yn="0"):
    cols = [""] * 31
    cols[0], cols[1], cols[2], cols[3] = bjd, "서울특별시", "금천구", "가산동"
    cols[5], cols[6], cols[7] = "0", "1", "0"
    cols[8], cols[9], cols[10], cols[11], cols[12] = road, "가산디지털1로", ug, main, sub
    cols[13], cols[15], cols[28] = name, mgt, detail_yn
    return "|".join(cols)


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        road = "115453005001"
        rows = [
            bld_row("1154510100", road, "0", "10", "0", "가산프라자", "1154510100100010000000001"),
            bld_row("1154510100", road, "0", "12", "0", "대륭상가", "1154510100100010000000002"),
            bld_row("1154510100", road, "0", "14", "0", "", "1154510100100010000000003"),
            bld_row("1154510200", road, "0", "99", "0", "독산상가", "1154510200100010000000004"),
        ]
        self.bld = os.path.join(self.d, "build_seoul.txt")
        with open(self.bld, "w", encoding="cp949") as f:
            f.write("\n".join(rows))
        # 상세주소DB: 앞에 임의 컬럼이 있어도 자동 탐지돼야 함 (12번지에만 상세주소 존재)
        detail = ["11545|가산동|%s|0|12|0|가동|1|101" % road,
                  "11545|가산동|%s|0|12|0|가동|1|102" % road,
                  "11680|역삼동|116803122001|0|5|0|가동|1|101"]
        self.det = os.path.join(self.d, "detail.zip")
        with zipfile.ZipFile(self.det, "w") as zf:
            zf.writestr("detail_seoul.txt", "\n".join(detail).encode("cp949"))
        self.reg = os.path.join(self.d, "register.csv")
        with open(self.reg, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["새주소_도로_코드", "새주소_지상지하_코드", "새주소_본_번", "새주소_부_번",
                        "대장_종류_코드_명", "주_용도_코드_명", "기타_용도", "호_수", "지상_층_수"])
            w.writerow([road, "0", "10", "0", "일반건축물", "제2종근린생활시설", "", "0", "5"])
            w.writerow([road, "0", "12", "0", "일반건축물", "판매시설", "", "0", "4"])
            w.writerow([road, "0", "14", "0", "일반건축물", "단독주택", "", "0", "2"])

    def run_tool(self, *extra):
        out = os.path.join(self.d, "out.csv")
        m.main(["--building", self.bld, "--detail", self.det, "--output", out, *extra])
        with open(out, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_with_register(self):
        rows = self.run_tool("--register", self.reg)
        self.assertEqual([r["건물명"] for r in rows], ["가산프라자"])
        self.assertEqual(rows[0]["결과"], "상세주소 미부여")
        self.assertEqual(rows[0]["판정근거"], "건축물대장")

    def test_all_flag_includes_assigned(self):
        rows = self.run_tool("--register", self.reg, "--all")
        got = {r["건물명"]: (r["결과"], r["상세주소건수"]) for r in rows}
        self.assertEqual(got, {"가산프라자": ("상세주소 미부여", "0"),
                               "대륭상가": ("상세주소 부여", "2")})

    def test_name_fallback_and_dong_filter(self):
        rows = self.run_tool()
        self.assertEqual([r["건물명"] for r in rows], ["가산프라자"])  # 독산동 건물 제외

    def test_key_normalization(self):
        self.assertEqual(m.make_key("115453005001", "0", "0012", "00"), "115453005001|0|12|0")


if __name__ == "__main__":
    unittest.main()
