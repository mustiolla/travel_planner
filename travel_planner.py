"""
travel_planner.py - 국내 여행 추천 프로그램 v3
변경: 도시별 최종 리포트 + 맛집 5~10곳 + 위도/경도 출력
"""

import os
import sys
import json
import time
import logging
import argparse
import urllib.parse
import urllib.request
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CONFIG = {
    "OPENAI_API_KEY":    os.getenv("OPENAI_API_KEY", ""),
    "KAKAO_REST_API_KEY": os.getenv("KAKAO_REST_API_KEY", ""),
    "MODEL":        "gpt-4o-mini",
    "MAX_RETRIES":  3,
    "RESULTS_DIR":  "results",
    "LOG_DIR":      "logs",
    "RESTAURANT_MIN": 5,   # 맛집 최소
    "RESTAURANT_MAX": 10,  # 맛집 최대
}

os.makedirs(CONFIG["RESULTS_DIR"], exist_ok=True)
os.makedirs(CONFIG["LOG_DIR"], exist_ok=True)

log_file = os.path.join(
    CONFIG["LOG_DIR"],
    f"run_{datetime.now().strftime('%Y%m%d')}.log"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)


# =============================================
# 유틸
# =============================================
def parse_args():
    parser = argparse.ArgumentParser(description="국내 여행 추천")
    parser.add_argument("--date", required=True, help="여행 날짜 (YYYY-MM-DD)")
    return parser.parse_args()


def _safe_json(text):
    """JSON 안전 파싱"""
    if not text:
        return None
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        if text.lstrip().startswith("["):
            start = text.index("[")
            end   = text.rindex("]") + 1
            return json.loads(text[start:end])
        start = text.index("{")
        end   = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception as e:
        logging.error(f"JSON 파싱 실패: {e}\n원문: {text[:200]}")
        return None


# =============================================
# 1단계: 도시 2~3개 추천
# =============================================
def recommend_cities(date_str):
    """행정구역/테마 다양하게 2~3개 도시 추천"""
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])

    prompt = (
        "당신은 국내 여행 전문가입니다.\n"
        f"{date_str} 날짜에 여행하기 좋은 대한민국 도시 2~3곳을 추천해주세요.\n\n"
        "추천 조건 (반드시 지켜주세요):\n"
        "1. 서로 다른 행정구역(도/광역시)에서 선택\n"
        "   예: 강원도, 경상남도, 전라북도 등 겹치지 않게\n"
        "2. 각 도시의 테마가 달라야 함\n"
        "   예: 자연/역사/해양/도시 중 서로 다르게\n"
        "3. 계절과 날짜를 고려한 추천\n\n"
        "반드시 아래 JSON 배열 형식으로만 답변 (다른 말 금지):\n"
        "[\n"
        "  {\n"
        '    "city": "도시명",\n'
        '    "region": "행정구역 (예: 강원도)",\n'
        '    "theme": "여행 테마 (예: 자연)",\n'
        '    "weather": "예상 날씨",\n'
        '    "events": ["행사1", "행사2"],\n'
        '    "reason": "추천 이유 (2~3문장)"\n'
        "  }\n"
        "]"
    )

    for i in range(CONFIG["MAX_RETRIES"]):
        try:
            resp = client.chat.completions.create(
                model=CONFIG["MODEL"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            content = resp.choices[0].message.content
            parsed  = _safe_json(content)

            if isinstance(parsed, list) and len(parsed) > 0:
                cities = [c.get("city", "") for c in parsed]
                logging.info(f"도시 추천 성공: {', '.join(cities)}")
                return parsed

        except Exception as e:
            logging.warning(f"도시추천 재시도 {i+1}/{CONFIG['MAX_RETRIES']}: {e}")
            time.sleep(1)

    logging.error("도시 추천 실패 → 기본값 사용")
    return [
        {"city": "강릉", "region": "강원도",   "theme": "자연/해양",
         "weather": "정보 없음", "events": [], "reason": "기본값"},
        {"city": "경주", "region": "경상북도", "theme": "역사",
         "weather": "정보 없음", "events": [], "reason": "기본값"},
    ]


# =============================================
# 2단계: 하루 일정 추천 (오전/오후/저녁)
# =============================================
def recommend_schedule(city, theme, date_str):
    """도시별 오전/오후/저녁 일정 추천"""
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])

    prompt = (
        f"당신은 {city} 여행 전문가입니다.\n"
        f"{date_str}에 {city}({theme} 테마) 하루 여행 일정을 짜주세요.\n\n"
        "반드시 아래 JSON 형식으로만 답변 (다른 말 금지):\n"
        "{\n"
        '  "morning": {\n'
        '    "time": "09:00 ~ 12:00",\n'
        '    "activity": "활동명",\n'
        '    "place": "장소명",\n'
        '    "tip": "여행 팁"\n'
        "  },\n"
        '  "afternoon": {\n'
        '    "time": "13:00 ~ 17:00",\n'
        '    "activity": "활동명",\n'
        '    "place": "장소명",\n'
        '    "tip": "여행 팁"\n'
        "  },\n"
        '  "evening": {\n'
        '    "time": "18:00 ~ 21:00",\n'
        '    "activity": "활동명",\n'
        '    "place": "장소명",\n'
        '    "tip": "여행 팁"\n'
        "  }\n"
        "}"
    )

    for i in range(CONFIG["MAX_RETRIES"]):
        try:
            resp = client.chat.completions.create(
                model=CONFIG["MODEL"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            content = resp.choices[0].message.content
            parsed  = _safe_json(content)
            if parsed and "morning" in parsed:
                logging.info(f"[{city}] 일정 추천 성공")
                return parsed
        except Exception as e:
            logging.warning(f"[{city}] 일정 재시도 {i+1}: {e}")
            time.sleep(1)

    return {
        "morning":   {"time": "09:00~12:00", "activity": "오전 관광", "place": city, "tip": "-"},
        "afternoon": {"time": "13:00~17:00", "activity": "오후 관광", "place": city, "tip": "-"},
        "evening":   {"time": "18:00~21:00", "activity": "저녁 식사", "place": city, "tip": "-"},
    }


# =============================================
# 3단계: 카카오 맛집 검색 (5~10곳)
# =============================================
def search_restaurants(city):
    """카카오 API 맛집 검색 - 5~10곳 반환"""
    if not city:
        return []

    query  = f"{city} 맛집"
    # size=15 요청 후 5~10개로 슬라이싱
    params = urllib.parse.urlencode({"query": query, "size": 15})
    url    = "https://dapi.kakao.com/v2/local/search/keyword.json?" + params

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"KakaoAK {CONFIG['KAKAO_REST_API_KEY']}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body      = resp.read().decode("utf-8")
            documents = json.loads(body).get("documents", [])
    except Exception as e:
        logging.error(f"[{city}] Kakao API 실패: {e}")
        return []

    results = []
    for d in documents:
        try:
            results.append({
                "place_name":    d.get("place_name", ""),
                "address_name":  d.get("address_name", ""),
                "category_name": d.get("category_name", ""),
                "place_url":     d.get("place_url", ""),
                # ✅ 위도/경도 숫자형으로 저장
                "longitude": float(d.get("x", 0)),  # 경도
                "latitude":  float(d.get("y", 0)),  # 위도
            })
        except Exception as e:
            logging.warning(f"맛집 파싱 실패: {e}")
            continue

    # ✅ 5~10곳 보장: 최소 5곳 확인 후 최대 10곳 반환
    total = len(results)
    if total < CONFIG["RESTAURANT_MIN"]:
        logging.warning(f"[{city}] 맛집 {total}곳 (5곳 미만)")
    else:
        results = results[:CONFIG["RESTAURANT_MAX"]]  # 최대 10곳

    logging.info(f"[{city}] 맛집 {len(results)}곳 반환")
    return results


# =============================================
# 4단계: 도시별 최종 리포트 생성
# =============================================
def build_city_report(date_str, city_info, schedule, restaurants):
    """
    도시 1개의 최종 여행 리포트 생성
    포함 내용:
      1) 추천 지역 + 추천 이유
      2) 날씨 요약
      3) 행사/축제 목록
      4) 맛집 리스트 (5~10곳, 위도/경도 포함)
      5) 1일 일정 (오전/오후/저녁)
    """
    city   = city_info.get("city",   "정보 없음")
    region = city_info.get("region", "")
    theme  = city_info.get("theme",  "")
    weather = city_info.get("weather", "정보 없음")
    events  = city_info.get("events",  [])
    reason  = city_info.get("reason",  "정보 없음")

    md  = f"# 🗺️ 최종 여행 리포트: {city}\n\n"
    md += f"> 여행 날짜: {date_str} | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    md += "---\n\n"

    # 1) 추천 지역 + 추천 이유
    md += "## 1️⃣ 추천 지역 & 추천 이유\n\n"
    md += f"| 항목 | 내용 |\n"
    md += f"|------|------|\n"
    md += f"| 도시 | **{city}** |\n"
    md += f"| 행정구역 | {region} |\n"
    md += f"| 테마 | {theme} |\n\n"
    md += f"**추천 이유:**  \n{reason}\n\n"
    md += "---\n\n"

    # 2) 날씨 요약
    md += "## 2️⃣ 날씨 요약\n\n"
    md += f"🌤️ {weather}\n\n"
    md += "---\n\n"

        # 3) 행사/축제 목록
    md += "## 3️⃣ 행사/축제 목록\n\n"
    if events:
        for e in events:
            md += f"- 🎉 {e}\n"
    else:
        md += "- 정보 없음\n"
    md += "\n---\n\n"

    # 4) 맛집 리스트 (5~10곳, 위도/경도 포함)
    md += "## 4️⃣ 맛집 리스트\n\n"
    if not restaurants:
        md += "> 데이터 없음\n\n"
    else:
        md += f"총 {len(restaurants)}곳 추천\n\n"
        md += "| # | 상호명 | 카테고리 | 주소 | 위도 | 경도 | 링크 |\n"
        md += "|---|--------|----------|------|------|------|------|\n"
        for i, r in enumerate(restaurants, 1):
            md += (
                f"| {i} "
                f"| {r['place_name']} "
                f"| {r['category_name']} "
                f"| {r['address_name']} "
                f"| {r['latitude']} "
                f"| {r['longitude']} "
                f"| [링크]({r['place_url']}) |\n"
            )
    md += "\n---\n\n"

    # 5) 1일 일정 (오전/오후/저녁)
    md += "## 5️⃣ 1일 여행 일정\n\n"
    periods = [
        ("morning",   "🌅 오전"),
        ("afternoon", "☀️ 오후"),
        ("evening",   "🌙 저녁"),
    ]
    for key, icon in periods:
        s = schedule.get(key, {})
        md += f"### {icon} ({s.get('time', '')})\n\n"
        md += f"- **활동:** {s.get('activity', '-')}\n"
        md += f"- **장소:** {s.get('place', '-')}\n"
        md += f"- **팁:**   {s.get('tip', '-')}\n\n"
    md += "---\n\n"

    return md


# =============================================
# 5단계: 전체 요약 리포트 생성
# =============================================
def build_summary_report(date_str, cities_data):
    """2~3개 도시 전체 요약 리포트"""
    md  = f"# 🗺️ {date_str} 국내 여행 추천 요약\n\n"
    md += f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    md += "---\n\n"

    # 도시 비교 테이블
    md += "## 📍 추천 도시 비교\n\n"
    md += "| 도시 | 행정구역 | 테마 | 날씨 | 맛집 수 |\n"
    md += "|------|----------|------|------|--------|\n"
    for item in cities_data:
        info = item["info"]
        cnt  = len(item["restaurants"])
        md += (
            f"| {info['city']} "
            f"| {info['region']} "
            f"| {info['theme']} "
            f"| {info['weather']} "
            f"| {cnt}곳 |\n"
        )
    md += "\n---\n\n"

    # 도시별 요약
    for idx, item in enumerate(cities_data, 1):
        info = item["info"]
        city = info.get("city", "")
        md += f"## {idx}. {city} 요약\n\n"
        md += f"- **추천 이유:** {info.get('reason', '-')}\n"
        md += f"- **날씨:** {info.get('weather', '-')}\n"

        events = info.get("events", [])
        md += f"- **행사:** "
        md += ", ".join(events) if events else "정보 없음"
        md += "\n"

        # 상세 리포트 링크
        md += f"- **상세 리포트:** `report_{date_str}_{city}.md`\n\n"

    md += "---\n\n"
    md += "> 각 도시 상세 리포트를 확인하세요! 🎒\n"
    return md


# =============================================
# 6단계: 결과 저장
# =============================================
def save_results(date_str, cities_data, summary_md):
    out_dir = CONFIG["RESULTS_DIR"]
    saved_files = []

    # 도시별 개별 리포트 저장
    for item in cities_data:
        city     = item["info"].get("city", "unknown")
        city_md  = item["city_report"]

        md_path = os.path.join(out_dir, f"report_{date_str}_{city}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(city_md)
        saved_files.append(md_path)
        logging.info(f"[{city}] 리포트 저장: {md_path}")

    # 전체 요약 리포트 저장
    summary_path = os.path.join(out_dir, f"summary_{date_str}.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_md)
    saved_files.append(summary_path)

    # 원본 JSON 저장
    raw_data = {
        "date": date_str,
        "cities": [
            {
                "info":        item["info"],
                "schedule":    item["schedule"],
                "restaurants": item["restaurants"],
            }
            for item in cities_data
        ]
    }
    raw_path = os.path.join(out_dir, f"raw_{date_str}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    saved_files.append(raw_path)

    logging.info(f"전체 저장 완료: {len(saved_files)}개 파일")
    return saved_files


# =============================================
# 메인 실행
# =============================================
def main():
    args     = parse_args()
    date_str = args.date

    logging.info(f"===== 여행 추천 시작 (날짜: {date_str}) =====")

    # API 키 확인
    if not CONFIG["OPENAI_API_KEY"]:
        logging.error("OPENAI_API_KEY 미설정")
        sys.exit(1)
    if not CONFIG["KAKAO_REST_API_KEY"]:
        logging.error("KAKAO_REST_API_KEY 미설정")
        sys.exit(1)

    # 1단계: 도시 2~3개 추천
    logging.info("1단계: 도시 추천 중...")
    cities_info = recommend_cities(date_str)

    # 2단계: 도시별 처리
    cities_data = []
    for city_info in cities_info:
        city  = city_info.get("city", "서울")
        theme = city_info.get("theme", "도시")
        logging.info(f"--- [{city}] 처리 시작 ---")

        # 일정 추천
        logging.info(f"2단계: [{city}] 일정 추천 중...")
        schedule = recommend_schedule(city, theme, date_str)

        # 맛집 검색
        logging.info(f"3단계: [{city}] 맛집 검색 중...")
        restaurants = search_restaurants(city)

        # 도시별 최종 리포트 생성
        logging.info(f"4단계: [{city}] 리포트 생성 중...")
        city_report = build_city_report(date_str, city_info, schedule, restaurants)

        cities_data.append({
            "info":        city_info,
            "schedule":    schedule,
            "restaurants": restaurants,
            "city_report": city_report,   # ✅ 도시별 리포트 포함
        })

        time.sleep(0.5)  # API 과호출 방지

    # 3단계: 전체 요약 리포트
    logging.info("5단계: 전체 요약 리포트 생성 중...")
    summary_md = build_summary_report(date_str, cities_data)

    # 4단계: 저장
    logging.info("6단계: 결과 저장 중...")
    saved_files = save_results(date_str, cities_data, summary_md)

    # 완료 출력
    print("\n" + "="*40)
    print("✅ 완료!")
    print("="*40)
    print(f"추천 도시: {', '.join([c['info']['city'] for c in cities_data])}")
    print("\n📁 저장된 파일:")
    for f in saved_files:
        print(f"   - {f}")
    print("="*40)


if __name__ == "__main__":
    main()