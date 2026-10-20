"""Собирает index.html из шаблона и CSV-выгрузки в data/.

Запуск: python3 scripts/build.py
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PARTY_SHORT = [
    ("ЛДПР", "ldpr", "ЛДПР"),
    ("ЕДИНАЯ РОССИЯ", "er", "Единая Россия"),
    ("ЯБЛОКО", "yabloko", "Яблоко"),
    ("Коммунистическая", "kprf", "КПРФ"),
    ("СПРАВЕДЛИВАЯ РОССИЯ", "sr", "Справедливая Россия"),
]

DISTRICT_NAMES = {
    1: "Первый районный", 2: "Второй районный", 3: "Третий районный",
    4: "Четвёртый районный", 5: "Пятый районный", 6: "Шестой районный",
    7: "Седьмой районный", 8: "Восьмой районный",
}


def read(name):
    with open(DATA / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def party_key(full):
    for needle, key, _ in PARTY_SHORT:
        if needle in full:
            return key
    raise ValueError(full)


def main():
    status = json.loads((DATA / "status.json").read_text(encoding="utf-8"))

    # Контрольные строки протоколов: (бюллетень, округ, комиссия) -> {строка: значение}
    control = defaultdict(dict)
    for r in read("protocol_control_rows.csv"):
        key = (r["Бюллетень"], int(r["Округ"]), r["УИК"])
        control[key][int(r["Строка"])] = int(r["Значение"])

    urls = {}
    for r in read("all_protocols.csv"):
        urls[(r["Бюллетень"], int(r["Округ"]), r["УИК"])] = r["URL"]

    district_of = {}
    for r in read("candidate_expected_uik_manifest.csv"):
        district_of[r["УИК"]] = int(r["Одномандатный_округ"])

    def party_turnout(c):
        return {
            "voters": c[1], "issued": c[5] + c[6], "issuedOut": c[6],
            "valid": c[11], "invalid": c[10],
        }

    def cand_turnout(c):
        return {
            "voters": c[1], "issued": c[3] + c[4], "issuedOut": c[4],
            "valid": c[9], "invalid": c[8],
        }

    # Единый округ
    party_votes = defaultdict(dict)
    full_names = {}
    for r in read("party_votes_by_uik.csv"):
        k = party_key(r["Партия"])
        full_names[k] = r["Партия"]
        party_votes[r["УИК"]][k] = int(r["Голоса"])

    tik_party = {}
    for r in read("TIK_vs_UIK_control.csv"):
        if r["Бюллетень"] == "party":
            tik_party[party_key(r["Партия_или_кандидат"])] = int(r["ТИК"])

    parties = [
        {"key": key, "short": short, "full": full_names[key], "votes": tik_party[key]}
        for _, key, short in PARTY_SHORT
    ]

    uiks = []
    for uik in sorted(party_votes, key=int):
        uiks.append({
            "uik": int(uik),
            "district": district_of[uik],
            **party_turnout(control[("party", 0, uik)]),
            "votes": party_votes[uik],
            "url": urls[("party", 0, uik)],
        })

    # Округа
    cand_votes = defaultdict(lambda: defaultdict(dict))
    for r in read("candidate_votes_by_district_uik.csv"):
        cand_votes[int(r["Округ"])][r["УИК"]][r["Кандидат"]] = int(r["Голоса"])

    tik_cand = defaultdict(dict)
    for r in read("TIK_vs_UIK_control.csv"):
        if r["Бюллетень"] == "candidate":
            tik_cand[int(r["Округ"])][r["Партия_или_кандидат"]] = int(r["ТИК"])

    districts = []
    for n in sorted(tik_cand):
        cands = sorted(tik_cand[n].items(), key=lambda kv: -kv[1])
        districts.append({
            "n": n,
            "name": DISTRICT_NAMES[n],
            **cand_turnout(control[("candidate", n, "TIK")]),
            "url": urls[("candidate", n, "TIK")],
            "candidates": [{"name": name, "votes": v} for name, v in cands],
            "uiks": [
                {
                    "uik": int(u),
                    **cand_turnout(control[("candidate", n, u)]),
                    "votes": cand_votes[n][u],
                    "url": urls[("candidate", n, u)],
                }
                for u in sorted(cand_votes[n], key=int)
            ],
        })

    payload = {
        "election": status["election"],
        "date": status["date"],
        "total": party_turnout(control[("party", 0, "TIK")]),
        "partyUrl": urls[("party", 0, "TIK")],
        "parties": parties,
        "uiks": uiks,
        "districts": districts,
    }

    # Сверка: суммы по УИК должны совпадать с итогами ТИК
    for p in parties:
        assert sum(u["votes"][p["key"]] for u in uiks) == p["votes"], p["key"]
    assert sum(p["votes"] for p in parties) == payload["total"]["valid"]
    for d in districts:
        for c in d["candidates"]:
            assert sum(u["votes"][c["name"]] for u in d["uiks"]) == c["votes"], c
        assert sum(c["votes"] for c in d["candidates"]) == d["valid"], d["n"]

    template = (ROOT / "scripts" / "template.html").read_text(encoding="utf-8")
    data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body = template.replace("/*__DATA__*/null", data_js)
    head, sep, rest = body.partition("</style>")
    html = (
        '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + head + sep + "\n</head>\n<body>\n" + rest + "</body>\n</html>\n"
    )
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    if len(sys.argv) > 1:
        # Вариант без обёртки <html>/<head> — для публикации как Artifact
        Path(sys.argv[1]).write_text(body, encoding="utf-8")
    print(f"index.html: {len(uiks)} УИК, {len(districts)} округов")


if __name__ == "__main__":
    main()
