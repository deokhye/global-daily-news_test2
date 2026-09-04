"""
build_site.py
--------------
data/latest.json (collector.py 결과) 을 template/template.html (Jinja2) 에 주입하여
docs/index.html 을 생성한다. docs/ 폴더는 GitHub Pages 배포 소스로 사용한다.

collector.py 단계에서 뉴스/산업 동향 항목마다 Tailwind 클래스(label_class, tag_class)를
이미 확정해 저장하므로, 이 스크립트는 순수하게 "데이터 → 템플릿 주입" 역할만 담당한다.
(환율 등락 방향(is_up) 판정, 소수점 포맷 등 표시 직전 변환만 수행)

실행:
    python src/collector.py   # 데이터 수집 -> data/latest.json
    python src/build_site.py  # 템플릿 렌더 -> docs/index.html
"""

import os
import json
import logging

from jinja2 import Environment, FileSystemLoader, select_autoescape

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("build_site")

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_PATH = os.path.join(BASE_DIR, "data", "latest.json")
TEMPLATE_DIR = os.path.join(BASE_DIR, "template")
TEMPLATE_NAME = "template.html"
OUTPUT_PATH = os.path.join(BASE_DIR, "docs", "index.html")


def load_data() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build(data: dict) -> None:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(TEMPLATE_NAME)

    er = data["exchange_rate"]

    ctx = {
        "generated_at_display": data["generated_at_display"],
        "current_rate": f"{er['current_rate']:,.2f}",
        "change_pct": abs(er.get("change_pct", 0)),
        "is_up": er.get("change_pct", 0) >= 0,
        "chart_labels": json.dumps(er.get("history_labels", []), ensure_ascii=False),
        "chart_values": json.dumps(er.get("history_values", [])),
        "profile": data["country_profile"],
        "headlines": data["headlines"],
        "hr_trends": data["hr_trends"],
    }

    html = template.render(**ctx)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"생성 완료 → {OUTPUT_PATH}")


def main():
    build(load_data())


if __name__ == "__main__":
    main()
