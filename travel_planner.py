"""
travel_planner.py - 국내 여행 추천 프로그램 v3.1
변경: 
1. 1번 대표 도시 + 2, 3번 숨은 소도시/군 단위 추천
2. 위도/경도 소수점 5자리 반올림
3. 맛집 테이블에 별점(rating) 컬럼 추가
4. 일정별 추천 장소와 가장 가까운 맛집 거리 매칭 표시
"""

import os
import sys
import json
import time
import math
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


def calculate_distance(lat1, lon1, lat2, lon2):
    """두 좌표 간의 직선 거리 계산 (단위: km)"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)


def get_place_coordinate(place_name):
    """장소 이름으로 카카오 검색 후 첫 번째 결과의 위도/경도 반환"""
    if not place_name or place_name == "-":
        return None
    query = urllib.parse.urlencode({"query": place_name, "size": 1})
    url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + query
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"KakaoAK {CONFIG['KAKAO_REST_API_KEY']}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            docs = data.get("documents", [])
            if docs:
                return {
                    "latitude": float(docs[0].get("y", 0)),
                    "longitude": float(docs[0].get("x", 0))
                }
    except Exception:
        pass
    return None


# =============================================
# 1단계: 도시 추천 (1곳 대표 도시 + 2곳 숨은 소도시)
# 👈 [여기가 1단계 코드 위치입니다!]
# =============================================
def recommend_cities(date_str):
    """1곳의 대표 도시 + 2곳의 숨은 명소/소도시 추천"""
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])

    prompt = (
        "당신은 대한민국 구석구석을 잘 아는 국내 여행 전문가입니다.\n"
        f"입력된 날짜: {date_str}\n"
        f"해당 날짜의 계절감, 축제, 날씨를 깊이 반영하여 여행하기 좋은 대한민국 도시/지역 정확히 3곳을 매번 새롭고 다채롭게 추천해주세요.\n\n"
        "추천 구성 및 필수 조건:\n"
        "1. [1번 도시]: 해당 날짜/계절에 전국적으로 가장 인기 있는 대표 유명 관광 도시 1곳\n"
        "2. [2번, 3번 도시]: 인파가 덜 붐비고 고즈넉하며 자연이나 고유의 정취가 살아있는 숨은 군 단위 지역 또는 로컬 소도시 2곳\n"
        "3. 3곳 모두 서로 다른 광역자치단체(도/광역시)에 속해야 함 (지역 중복 절대 불가)\n"
        "4. 3곳의 여행 테마(자연, 역사, 힐링, 미식, 해양 등)가 서로 겹치지 않아야 함\n"
        "5. 특정 고정 도시에 편중되지 않도록 날짜와 계절에 최적화된 다양한 지역을 폭넓게 탐색해 선정할 것\n\n"
        "반드시 아래 JSON 배열 형식으로만 답변 (추가 텍스트 없이 JSON만 반환):\n"
        "[\n"
        "  {\n"
        '    "city": "도시/지역명 (예: 시/군 명확히 기재)",\n'
        '    "region": "행정구역",\n'
        '    "theme": "여행 테마",\n'
        '    "weather": "예상 날씨",\n'
        '    "events": ["추천 스팟 또는 행사1", "추천 스팟 또는 행사2"],\n'
        '    "reason": "추천 이유 (계절적 특성과 매력 포인트 포함 2~3문장)"\n'
        "  }\n"
        "]"
    )

    for i in range(CONFIG["MAX_RETRIES"]):
        try:
            resp = client.chat.completions.create(
                model=CONFIG["MODEL"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.95,          # 창의성/다양성 대폭 상향
                presence_penalty=0.6,     # 새로운 주제/도시 탐색 유도
            )
            content = resp.choices[0].message.content
            parsed  = _safe_json(content)

            if isinstance(parsed, list) and len(parsed) >= 3:
                cities = [c.get("city", "") for c in parsed]
                logging.info(f"도시 추천 성공: {', '.join(cities)}")
                return parsed[:3]

        except Exception as e:
            logging.warning(f"도시추천 재시도 {i+1}/{CONFIG['MAX_RETRIES']}: {e}")
            time.sleep(1)

    logging.error("도시 추천 실패 → 기본값 사용")
    return [
        {"city": "속초시", "region": "강원도", "theme": "해양/미식", "weather": "정보 없음", "events": [], "reason": "대표 인기 바다 여행지"},
        {"city": "태안군", "region": "충청남도", "theme": "자연/일몰", "weather": "정보 없음", "events": [], "reason": "서해안의 고즈넉한 해변과 자연 휴양"},
        {"city": "하동군", "region": "경상남도", "theme": "힐링/다원", "weather": "정보 없음", "events": [], "reason": "섬진강변의 차밭과 힐링 소도시"},
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
# 3단계: 카카오 맛집 검색 (5~10곳, 별점 및 좌표 반올림)
# =============================================
def search_restaurants(city):
    """카카오 API 맛집 검색 - 5~10곳 반환"""
    if not city:
        return []

    query  = f"{city} 맛집"
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
                "rating":        d.get("rating", "-"),  # 별점 필드
                "longitude":     round(float(d.get("x", 0)), 5),  # 경도 (5자리)
                "latitude":      round(float(d.get("y", 0)), 5),  # 위도 (5자리)
            })
        except Exception as e:
            logging.warning(f"맛집 파싱 실패: {e}")
            continue

    total = len(results)
    if total < CONFIG["RESTAURANT_MIN"]:
        logging.warning(f"[{city}] 맛집 {total}곳 (5곳 미만)")
    else:
        results = results[:CONFIG["RESTAURANT_MAX"]]

    logging.info(f"[{city}] 맛집 {len(results)}곳 반환")
    return results


# =============================================
# 4단계: 도시별 최종 리포트 생성 (가까운 맛집 매칭 포함)
# =============================================
def build_city_report(date_str, city_info, schedule, restaurants):
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
    md += f"| 항목 | 내용 |\n|------|------|\n"
    md += f"| 도시 | **{city}** |\n| 행정구역 | {region} |\n| 테마 | {theme} |\n\n"
    md += f"**추천 이유:**  \n{reason}\n\n---\n\n"

    # 2) 날씨 요약
    md += "## 2️⃣ 날씨 요약\n\n"
    md += f"🌤️ {weather}\n\n---\n\n"

    # 3) 행사/축제 목록
    md += "## 3️⃣ 행사/축제 목록\n\n"
    if events:
        for e in events:
            md += f"- 🎉 {e}\n"
    else:
        md += "- 정보 없음\n"
    md += "\n---\n\n"

    # 4) 맛집 리스트
    md += "## 4️⃣ 맛집 리스트\n\n"
    if not restaurants:
        md += "> 데이터 없음\n\n"
    else:
        md += f"총 {len(restaurants)}곳 추천\n\n"
        md += "| # | 상호명 | 카테고리 | 별점 | 주소 | 위도 | 경도 | 링크 |\n"
        md += "|---|--------|----------|------|------|------|------|------|\n"
        for i, r in enumerate(restaurants, 1):
            rating_val = r.get('rating', '-')
            md += (
                f"| {i} "
                f"| {r['place_name']} "
                f"| {r['category_name']} "
                f"| {rating_val} "
                f"| {r['address_name']} "
                f"| {r['latitude']:.5f} "
                f"| {r['longitude']:.5f} "
                f"| [링크]({r['place_url']}) |\n"
            )
    md += "\n---\n\n"

    # 5) 1일 일정 (가까운 맛집 매칭)
    md += "## 5️⃣ 1일 여행 일정\n\n"
    periods = [
        ("morning",   "🌅 오전"),
        ("afternoon", "☀️ 오후"),
        ("evening",   "🌙 저녁"),
    ]
    for key, icon in periods:
        s = schedule.get(key, {})
        place_name = s.get('place', '-')
        
        nearby_text = "매칭된 맛집 정보 없음"
        if place_name != "-" and restaurants:
            target_coord = get_place_coordinate(f"{city} {place_name}")
            if target_coord:
                dist_list = []
                for rest in restaurants:
                    dist = calculate_distance(
                        target_coord["latitude"], target_coord["longitude"],
                        rest["latitude"], rest["longitude"]
                    )
                    dist_list.append((dist, rest))
                dist_list.sort(key=lambda x: x[0])
                closest_dist, closest_rest = dist_list[0]
                nearby_text = f"**{closest_rest['place_name']}** ({closest_rest['category_name']}, 약 {closest_dist}km 거리)"

        md += f"### {icon} ({s.get('time', '')})\n\n"
        md += f"- **활동:** {s.get('activity', '-')}\n"
        md += f"- **장소:** {place_name}\n"
        md += f"- **팁:**   {s.get('tip', '-')}\n"
        md += f"- **🍽️ 가까운 추천 맛집:** {nearby_text}\n\n"
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

    for item in cities_data:
        city     = item["info"].get("city", "unknown")
        city_md  = item["city_report"]

        md_path = os.path.join(out_dir, f"report_{date_str}_{city}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(city_md)
        saved_files.append(md_path)
        logging.info(f"[{city}] 리포트 저장: {md_path}")

    summary_path = os.path.join(out_dir, f"summary_{date_str}.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_md)
    saved_files.append(summary_path)

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

    if not CONFIG["OPENAI_API_KEY"]:
        logging.error("OPENAI_API_KEY 미설정")
        sys.exit(1)
    if not CONFIG["KAKAO_REST_API_KEY"]:
        logging.error("KAKAO_REST_API_KEY 미설정")
        sys.exit(1)

    # 1단계: 도시 3개 추천 (1곳 대표 + 2곳 숨은 소도시)
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
            "city_report": city_report,
        })

        time.sleep(0.5)

    # 5단계: 전체 요약 리포트
    logging.info("5단계: 전체 요약 리포트 생성 중...")
    summary_md = build_summary_report(date_str, cities_data)

    # 6단계: 저장
    logging.info("6단계: 결과 저장 중...")
    saved_files = save_results(date_str, cities_data, summary_md)

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