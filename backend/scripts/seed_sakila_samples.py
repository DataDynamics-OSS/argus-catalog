# SPDX-License-Identifier: Apache-2.0
"""Sakila 데이터셋 샘플 parquet 시드 생성기.

각 sakila 데이터셋의 미리보기용 샘플 데이터를 제품 규약 경로에 기록한다:

    {data_dir}/samples/{datasource_id}/{dataset_name}/sample.parquet
    (예: /var/lib/argus-catalog-server/samples/sakila-mysql/sakila.film/sample.parquet)

제품 규약(metadata-sync 어댑터)과 동일하게 **모든 컬럼을 STRING 으로 평면화**해
저장한다 — 미리보기 API 가 값을 문자열로 렌더링하기 때문.

실행 (backend/ 에서):
    .venv/bin/python scripts/seed_sakila_samples.py            # settings.data_dir 사용
    .venv/bin/python scripts/seed_sakila_samples.py --data-dir ./data

재실행 안전: 동일 내용으로 덮어쓴다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATASOURCE_ID = "sakila-mysql"

# ---------------------------------------------------------------------------
# Sakila 대표 샘플 (원본 데이터 첫 행들, 전 컬럼 문자열)
# ---------------------------------------------------------------------------

SAMPLES: dict[str, dict[str, list[str | None]]] = {
    "sakila.actor": {
        "actor_id":    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "first_name":  ["PENELOPE", "NICK", "ED", "JENNIFER", "JOHNNY", "BETTE", "GRACE", "MATTHEW", "JOE", "CHRISTIAN"],
        "last_name":   ["GUINESS", "WAHLBERG", "CHASE", "DAVIS", "LOLLOBRIGIDA", "NICHOLSON", "MOSTEL", "JOHANSSON", "SWANK", "GABLE"],
        "last_update": ["2006-02-15 04:34:33"] * 10,
    },
    "sakila.film": {
        "film_id":          ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "title":            ["ACADEMY DINOSAUR", "ACE GOLDFINGER", "ADAPTATION HOLES", "AFFAIR PREJUDICE", "AFRICAN EGG",
                             "AGENT TRUMAN", "AIRPLANE SIERRA", "AIRPORT POLLOCK", "ALABAMA DEVIL", "ALADDIN CALENDAR"],
        "description":      ["A Epic Drama of a Feminist And a Mad Scientist who must Battle a Teacher in The Canadian Rockies",
                             "A Astounding Epistle of a Database Administrator And a Explorer who must Find a Car in Ancient China",
                             "A Astounding Reflection of a Lumberjack And a Car who must Sink a Lumberjack in A Baloon Factory",
                             "A Fanciful Documentary of a Frisbee And a Lumberjack who must Chase a Monkey in A Shark Tank",
                             "A Fast-Paced Documentary of a Pastry Chef And a Dentist who must Pursue a Forensic Psychologist in The Gulf of Mexico",
                             "A Intrepid Panorama of a Robot And a Boy who must Escape a Sumo Wrestler in Ancient China",
                             "A Touching Saga of a Hunter And a Butler who must Discover a Butler in A Jet Boat",
                             "A Epic Tale of a Moose And a Girl who must Confront a Monkey in Ancient India",
                             "A Thoughtful Panorama of a Database Administrator And a Mad Scientist who must Outgun a Mad Scientist in A Jet Boat",
                             "A Action-Packed Tale of a Man And a Lumberjack who must Reach a Feminist in Ancient China"],
        "release_year":     ["2006"] * 10,
        "language_id":      ["1"] * 10,
        "original_language_id": [None] * 10,
        "rental_duration":  ["6", "3", "7", "5", "6", "3", "6", "6", "3", "6"],
        "rental_rate":      ["0.99", "4.99", "2.99", "2.99", "2.99", "2.99", "4.99", "4.99", "2.99", "4.99"],
        "length":           ["86", "48", "50", "117", "130", "169", "62", "54", "114", "63"],
        "replacement_cost": ["20.99", "12.99", "18.99", "26.99", "22.99", "17.99", "28.99", "15.99", "21.99", "24.99"],
        "rating":           ["PG", "G", "NC-17", "G", "G", "PG", "PG-13", "R", "PG-13", "NC-17"],
        "special_features": ["Deleted Scenes,Behind the Scenes", "Trailers,Deleted Scenes", "Trailers,Deleted Scenes",
                             "Commentaries,Behind the Scenes", "Deleted Scenes", "Deleted Scenes",
                             "Trailers,Deleted Scenes", "Trailers", "Trailers,Deleted Scenes", "Deleted Scenes"],
        "last_update":      ["2006-02-15 05:03:42"] * 10,
    },
    "sakila.customer": {
        "customer_id": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "store_id":    ["1", "1", "1", "2", "1", "2", "1", "2", "2", "1"],
        "first_name":  ["MARY", "PATRICIA", "LINDA", "BARBARA", "ELIZABETH", "JENNIFER", "MARIA", "SUSAN", "MARGARET", "DOROTHY"],
        "last_name":   ["SMITH", "JOHNSON", "WILLIAMS", "JONES", "BROWN", "DAVIS", "MILLER", "WILSON", "MOORE", "TAYLOR"],
        "email":       ["MARY.SMITH@sakilacustomer.org", "PATRICIA.JOHNSON@sakilacustomer.org", "LINDA.WILLIAMS@sakilacustomer.org",
                        "BARBARA.JONES@sakilacustomer.org", "ELIZABETH.BROWN@sakilacustomer.org", "JENNIFER.DAVIS@sakilacustomer.org",
                        "MARIA.MILLER@sakilacustomer.org", "SUSAN.WILSON@sakilacustomer.org", "MARGARET.MOORE@sakilacustomer.org",
                        "DOROTHY.TAYLOR@sakilacustomer.org"],
        "address_id":  ["5", "6", "7", "8", "9", "10", "11", "12", "13", "14"],
        "active":      ["1"] * 10,
        "create_date": ["2006-02-14 22:04:36"] * 10,
        "last_update": ["2006-02-15 04:57:20"] * 10,
    },
    "sakila.rental": {
        "rental_id":    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "rental_date":  ["2005-05-24 22:53:30", "2005-05-24 22:54:33", "2005-05-24 23:03:39", "2005-05-24 23:04:41",
                         "2005-05-24 23:05:21", "2005-05-24 23:08:07", "2005-05-24 23:11:53", "2005-05-24 23:31:46",
                         "2005-05-25 00:00:40", "2005-05-25 00:02:21"],
        "inventory_id": ["367", "1525", "1711", "2452", "2079", "2792", "3995", "2346", "2580", "1824"],
        "customer_id":  ["130", "459", "408", "333", "222", "549", "269", "239", "126", "399"],
        "return_date":  ["2005-05-26 22:04:30", "2005-05-28 19:40:33", "2005-06-01 22:12:39", "2005-06-03 01:43:41",
                         "2005-06-02 04:33:21", "2005-05-27 01:32:07", "2005-05-29 20:34:53", "2005-05-27 23:33:46",
                         "2005-05-28 00:22:40", None],
        "staff_id":     ["1", "1", "1", "2", "1", "1", "2", "2", "1", "2"],
        "last_update":  ["2006-02-15 21:30:53"] * 10,
    },
    "sakila.payment": {
        "payment_id":   ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "customer_id":  ["1", "1", "1", "1", "1", "1", "1", "1", "1", "1"],
        "staff_id":     ["1", "1", "1", "2", "2", "1", "1", "2", "1", "2"],
        "rental_id":    ["76", "573", "1185", "1422", "1476", "1725", "2308", "2363", "3284", "4526"],
        "amount":       ["2.99", "0.99", "5.99", "0.99", "9.99", "4.99", "4.99", "0.99", "3.99", "5.99"],
        "payment_date": ["2005-05-25 11:30:37", "2005-05-28 10:35:23", "2005-06-15 00:54:12", "2005-06-15 18:02:53",
                        "2005-06-15 21:08:46", "2005-06-16 15:18:57", "2005-06-18 08:41:48", "2005-06-18 13:33:59",
                        "2005-06-21 06:24:45", "2005-07-08 03:17:05"],
        "last_update":  ["2006-02-15 22:12:30"] * 10,
    },
    "sakila.inventory": {
        "inventory_id": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "film_id":      ["1", "1", "1", "1", "1", "1", "1", "1", "2", "2"],
        "store_id":     ["1", "1", "1", "1", "2", "2", "2", "2", "2", "2"],
        "last_update":  ["2006-02-15 05:09:17"] * 10,
    },
    "sakila.store": {
        "store_id":         ["1", "2"],
        "manager_staff_id": ["1", "2"],
        "address_id":       ["1", "2"],
        "last_update":      ["2006-02-15 04:57:12"] * 2,
    },
    "sakila.staff": {
        "staff_id":    ["1", "2"],
        "first_name":  ["Mike", "Jon"],
        "last_name":   ["Hillyer", "Stephens"],
        "address_id":  ["3", "4"],
        "picture":     [None, None],
        "email":       ["Mike.Hillyer@sakilastaff.com", "Jon.Stephens@sakilastaff.com"],
        "store_id":    ["1", "2"],
        "active":      ["1", "1"],
        "username":    ["Mike", "Jon"],
        "password":    ["8cb2237d0679ca88db6464eac60da96345513964", "8cb2237d0679ca88db6464eac60da96345513964"],
        "last_update": ["2006-02-15 03:57:16"] * 2,
    },
    "sakila.address": {
        "address_id":  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "address":     ["47 MySakila Drive", "28 MySQL Boulevard", "23 Workhaven Lane", "1411 Lillydale Drive",
                        "1913 Hanoi Way", "1121 Loja Avenue", "692 Joliet Street", "1566 Inegl Manor",
                        "53 Idfu Parkway", "1795 Santiago de Compostela Way"],
        "address2":    [None, None, None, None, "", "", "", "", "", ""],
        "district":    ["Alberta", "QLD", "Alberta", "QLD", "Nagasaki", "California", "Attika", "Mandalay",
                        "Nantou", "Texas"],
        "city_id":     ["300", "576", "300", "576", "463", "449", "38", "349", "361", "295"],
        "postal_code": ["", "", "", "", "35200", "17886", "83579", "53561", "42399", "18743"],
        "phone":       ["", "", "", "", "28303384290", "838635286649", "448477190408", "705814003527",
                        "10655648674", "860452626434"],
        "location":    ["POINT(-112.8185647 49.6999986)", "POINT(153.1408538 -27.633986)", "POINT(-112.8185647 49.6999986)",
                        "POINT(153.1408538 -27.633986)", "POINT(108.4586 11.9462)", "POINT(-117.2871 33.9163)",
                        "POINT(23.7166667 37.9833333)", "POINT(96.0833333 21.9833333)", "POINT(120.6833333 23.9166667)",
                        "POINT(-94.4196 31.7508)"],
        "last_update": ["2014-09-25 22:30:27"] * 10,
    },
    "sakila.city": {
        "city_id":     ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "city":        ["A Corua (La Corua)", "Abha", "Abu Dhabi", "Acua", "Adana", "Addis Abeba", "Aden",
                        "Adoni", "Ahmadnagar", "Akishima"],
        "country_id":  ["87", "82", "101", "60", "97", "31", "107", "44", "44", "50"],
        "last_update": ["2006-02-15 04:45:25"] * 10,
    },
    "sakila.country": {
        "country_id":  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "country":     ["Afghanistan", "Algeria", "American Samoa", "Angola", "Anguilla", "Argentina",
                        "Armenia", "Australia", "Austria", "Azerbaijan"],
        "last_update": ["2006-02-15 04:44:00"] * 10,
    },
    "sakila.category": {
        "category_id": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "name":        ["Action", "Animation", "Children", "Classics", "Comedy", "Documentary", "Drama",
                        "Family", "Foreign", "Games"],
        "last_update": ["2006-02-15 04:46:27"] * 10,
    },
    "sakila.language": {
        "language_id": ["1", "2", "3", "4", "5", "6"],
        "name":        ["English", "Italian", "Japanese", "Mandarin", "French", "German"],
        "last_update": ["2006-02-15 05:02:19"] * 6,
    },
    "sakila.film_actor": {
        "actor_id":    ["1", "1", "1", "1", "1", "1", "1", "1", "1", "1"],
        "film_id":     ["1", "23", "25", "106", "140", "166", "277", "361", "438", "499"],
        "last_update": ["2006-02-15 05:05:03"] * 10,
    },
    "sakila.film_category": {
        "film_id":     ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "category_id": ["6", "11", "6", "11", "8", "9", "5", "11", "11", "15"],
        "last_update": ["2006-02-15 05:07:09"] * 10,
    },
    "sakila.film_text": {
        "film_id":     ["1", "2", "3", "4", "5"],
        "title":       ["ACADEMY DINOSAUR", "ACE GOLDFINGER", "ADAPTATION HOLES", "AFFAIR PREJUDICE", "AFRICAN EGG"],
        "description": ["A Epic Drama of a Feminist And a Mad Scientist who must Battle a Teacher in The Canadian Rockies",
                        "A Astounding Epistle of a Database Administrator And a Explorer who must Find a Car in Ancient China",
                        "A Astounding Reflection of a Lumberjack And a Car who must Sink a Lumberjack in A Baloon Factory",
                        "A Fanciful Documentary of a Frisbee And a Lumberjack who must Chase a Monkey in A Shark Tank",
                        "A Fast-Paced Documentary of a Pastry Chef And a Dentist who must Pursue a Forensic Psychologist in The Gulf of Mexico"],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sakila 샘플 parquet 시드 생성")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="데이터 디렉터리 (기본: app settings 의 data_dir)")
    args = parser.parse_args()

    if args.data_dir is not None:
        data_dir = args.data_dir
    else:
        from app.core.config import settings
        data_dir = settings.data_dir

    base = Path(data_dir) / "samples" / DATASOURCE_ID
    try:
        base.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"오류: {base} 생성 권한이 없습니다. 다음을 먼저 실행하세요:\n"
              f"  sudo mkdir -p {data_dir} && sudo chown $(whoami) {data_dir}", file=sys.stderr)
        return 1

    for dataset_name, columns in SAMPLES.items():
        table = pa.table({k: pa.array(v, type=pa.string()) for k, v in columns.items()})
        out_dir = base / dataset_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "sample.parquet"
        pq.write_table(table, out_path)
        print(f"  {out_path} ({table.num_rows} rows, {table.num_columns} cols)")

    print(f"\n완료: {len(SAMPLES)}개 데이터셋 샘플 생성 → {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
