"""
travel_planner.py - 국내 여행 추천 프로그램 v3.2
변경: 
1. 1번 대표 도시 + 2, 3번 숨은 소도시/군 단위 추천
2. 위도/경도 소수점 5자리 반올림, 거리 소수점 2자리 반올림
3. 맛집 테이블에 별점(rating) 컬럼 추가
4. 일정별 추천 장소와 가장 가까운 맛집 거리 매칭 표시
5. 필수 키 변경 (city -> recommended_city) 및 캐싱/에러로그 기능 추가
"""

import re
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
    # 요구사항에 맞게 '-date' 옵션 지원
    parser.add_argument("-date", dest="date", required=True, help="여행 날짜 (YYYY-MM-DD)")
    args = parser.parse_args()
    
    # 📌 날짜 형식 검증 추가
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("❌ 오류: 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력해주세요. (예: 2024-05-05)")
        sys.exit(1)
        
    return args


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
    
    # 리포트 가독성을 위해 소수점 2자리로 반올림
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
# =============================================
def validate_city_recommendations(data):
    """도시 추천 결과 검증"""
    if not isinstance(data, list) or len(data) != 3:
        return False

    # 요구사항에 맞게 city -> recommended_city 로 변경
    required_keys = ["recommended_city", "region", "theme", "weather", "events", "reason"]

    regions = []
    cities = []

    for item in data:
        if not isinstance(item, dict):
            return False

        for key in required_keys:
            if key not in item:
                return False

        if not isinstance(item["recommended_city"], str) or not item["recommended_city"].strip():
            return False
        if not isinstance(item["region"], str) or not item["region"].strip():
            return False
        if not isinstance(item["theme"], str) or not item["theme"].strip():
            return False
        if not isinstance(item["weather"], str):
            return False
        if not isinstance(item["events"], list):
            return False
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            return False

        cities.append(item["recommended_city"].strip())
        regions.append(item["region"].strip())

    # 도시명 중복 방지
    if len(set(cities)) != 3:
        return False

    # 광역자치단체 중복 방지
    if len(set(regions)) != 3:
        return False

    return True

def recommend_cities(date_str, errors=None):
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
        '    "recommended_city": "도시/지역명 (예: 시/군 명확히 기재)",\n'
        '    "region": "행정구역",\n'
        '    "theme": "여행 테마",\n'
        '    "weather": "예상 날씨",\n'
        '    "events": ["추천 스팟 또는 행사1", "추천 스팟 또는 행사2"],\n'
        '    "reason": "추천 이유 (계절적 특성과 매력 포인트 포함 2~3문장)"\n'
        "  }\n"
        "]"
    )

    for i in range(CONFIG["MAX_RETRIES"]):
        # ---------------------------------------------------------
        # 재시도 시 프롬프트 보강 전략
        # ---------------------------------------------------------
        current_prompt = prompt
        if i > 0:
            current_prompt += (
                "\n\n주의: 이전 응답이 형식을 지키지 않았습니다. "
                "반드시 다른 설명 없이 JSON 배열만 반환하세요."
            )

        try:
            resp = client.chat.completions.create(
                model=CONFIG["MODEL"],
                messages=[{"role": "user", "content": current_prompt}],
                temperature=0.95,
                presence_penalty=0.6,
            )
            content = resp.choices[0].message.content
            parsed = _safe_json(content)

            if validate_city_recommendations(parsed):
                cities = [c.get("recommended_city", "") for c in parsed]
                logging.info(f"도시 추천 성공: {', '.join(cities)}")
                return parsed
            else:
                raise ValueError("도시 추천 JSON 구조/필수값 검증 실패")

        except Exception as e:
            logging.warning(f"도시추천 재시도 {i+1}/{CONFIG['MAX_RETRIES']}: {e}")
        if errors is not None:
            errors.append({
                "stage": "recommend_cities_retry",
                "date": date_str,
                "error": str(e)
            })
        time.sleep(1)

    logging.error("도시 추천 실패 → 기본값 사용")
    return [
        {"recommended_city": "속초시", "region": "강원도", "theme": "해양/미식", "weather": "정보 없음", "events": [], "reason": "대표 인기 바다 여행지"},
        {"recommended_city": "태안군", "region": "충청남도", "theme": "자연/일몰", "weather": "정보 없음", "events": [], "reason": "서해안의 고즈넉한 해변과 자연 휴양"},
        {"recommended_city": "하동군", "region": "경상남도", "theme": "힐링/다원", "weather": "정보 없음", "events": [], "reason": "섬진강변의 차밭과 힐링 소도시"},
    ]

# =============================================
# 2단계: 하루 일정 추천 (오전/오후/저녁)
# =============================================
def validate_schedule(data):
    """하루 일정 JSON 검증"""
    if not isinstance(data, dict):
        return False

    periods = ["morning", "afternoon", "evening"]
    fields = ["time", "activity", "place", "tip"]

    for period in periods:
        if period not in data:
            return False
        if not isinstance(data[period], dict):
            return False

        for field in fields:
            if field not in data[period]:
                return False
            if not isinstance(data[period][field], str):
                return False

    return True

def recommend_schedule(city, theme, date_str, errors=None):
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
        current_prompt = prompt
        if i > 0:
            current_prompt += (
                "\n\n주의: 이전 응답이 형식을 지키지 않았습니다. "
                "반드시 다른 설명 없이 JSON 객체만 반환하세요."
            )

        try:
            resp = client.chat.completions.create(
                model=CONFIG["MODEL"],
                messages=[{"role": "user", "content": current_prompt}],
                temperature=0.7,
            )
            content = resp.choices[0].message.content
            parsed = _safe_json(content)
            if validate_schedule(parsed):
                logging.info(f"[{city}] 일정 추천 성공")
                return parsed
            else:
                raise ValueError("일정 추천 JSON 구조 검증 실패")
        except Exception as e:
            logging.warning(f"[{city}] 일정 재시도 {i+1}: {e}")
        if errors is not None:
            errors.append({
                "city": city,
                "stage": "recommend_schedule_retry",
                "error": str(e)
            })
        time.sleep(1)

    if errors is not None:
        errors.append({
            "city": city,
            "stage": "recommend_schedule_fallback",
            "error": "일정 추천 실패로 기본 일정 사용"
        })
    
    return {
        "morning":   {"time": "09:00~12:00", "activity": "오전 관광", "place": city, "tip": "-"},
        "afternoon": {"time": "13:00~17:00", "activity": "오후 관광", "place": city, "tip": "-"},
        "evening":   {"time": "18:00~21:00", "activity": "저녁 식사", "place": city, "tip": "-"},
    }


# =============================================
# 3단계: 카카오 맛집 검색 (5~10곳, 별점 및 좌표 반올림)
# =============================================
def search_restaurants(city, errors=None):
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
        if errors is not None:
            errors.append({
                "city": city,
                "stage": "search_restaurants",
                "error": str(e)
            }) 
        return []

    results = []
    for d in documents:
        try:
            results.append({
                "place_name":    d.get("place_name", ""),
                "address_name":  d.get("address_name", ""),
                "category_name": d.get("category_name", ""),
                "place_url":     d.get("place_url", ""),
                "rating":        None,  # 별점 필드, Kakao keyword search API는 rating 미제공
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
    city   = city_info.get("recommended_city",   "정보 없음")
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
            rating_val = r.get("rating")
            rating_text = rating_val if rating_val not in (None, "") else "제공 안 됨"
            md += (
                f"| {i} "
                f"| {r['place_name']} "
                f"| {r['category_name']} "
                f"| {rating_text} "
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
def build_summary_report(date_str, cities_data, global_errors):
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
            f"| {info['recommended_city']} "
            f"| {info['region']} "
            f"| {info['theme']} "
            f"| {info['weather']} "
            f"| {cnt}곳 |\n"
        )
    md += "\n---\n\n"

    # 도시별 요약
    for idx, item in enumerate(cities_data, 1):
        info = item["info"]
        city = info.get("recommended_city", "")
        md += f"## {idx}. {city} 요약\n\n"
        md += f"- **추천 이유:** {info.get('reason', '-')}\n"
        md += f"- **날씨:** {info.get('weather', '-')}\n"

        events = info.get("events", [])
        md += f"- **행사:** "
        md += ", ".join(events) if events else "정보 없음"
        md += "\n"

        md += f"- **상세 리포트:** `report_{date_str}_{city}.md`\n\n"

    # 에러 내역 추가 (체크리스트 요구사항)
    if global_errors:
        md += "---\n\n"
        md += "## ⚠️ 시스템 알림 (에러 로그)\n\n"
        for err in global_errors:
            md += f"- **[{err.get('stage')}]** {err.get('error')} (대상: {err.get('city', '공통')})\n"
        md += "\n"

    md += "---\n\n"
    md += "> 각 도시 상세 리포트를 확인하세요! 🎒\n"
    return md


# =============================================
# 6단계: 결과 저장
# =============================================
def save_results(date_str, cities_data, summary_md, global_errors):
    out_dir = "results"
    os.makedirs(out_dir, exist_ok=True)
    saved_files = []

    try:
        # 요약 리포트 저장 추가
        summary_path = os.path.join(out_dir, f"summary_report_{date_str}.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary_md)
        saved_files.append(summary_path)
        logging.info(f"[저장 성공] Summary Markdown: {summary_path}")

        # 도시별 markdown 저장
        for item in cities_data:
            city = item["info"].get("recommended_city", "unknown")
            md_path = os.path.join(out_dir, f"report_{date_str}_{city}.md")

            with open(md_path, "w", encoding="utf-8") as f:
                f.write(item["city_report"])

            saved_files.append(md_path)
            logging.info(f"[저장 성공] City Markdown: {md_path}")

        # raw json 저장
        raw_path = os.path.join(out_dir, f"raw_{date_str}.json")
        raw_data = {
            "date": date_str,
            "cities": cities_data,
            "errors": global_errors,
        }

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)

        saved_files.append(raw_path)
        logging.info(f"[저장 성공] Raw JSON: {raw_path}")
        return saved_files

    except Exception as e:
        logging.exception(f"파일 저장 실패: {e}")
        global_errors.append({
            "stage": "save_results",
            "error": str(e)
        })
        return []


# =============================================
# 메인 실행
# =============================================
def main():
    args     = parse_args()
    date_str = args.date
    global_errors = [] # 📌 발생한 에러를 담을 리스트 추가

    logging.info(f"===== 여행 추천 시작 (날짜: {date_str}) =====")

    if not CONFIG["OPENAI_API_KEY"]:
        logging.error("OPENAI_API_KEY 미설정")
        sys.exit(1)
    if not CONFIG["KAKAO_REST_API_KEY"]:
        logging.error("KAKAO_REST_API_KEY 미설정")
        sys.exit(1)

    # 📌 [캐싱 로직 추가] 동일 날짜의 JSON 파일이 있으면 API 호출 생략
    raw_path = os.path.join(CONFIG["RESULTS_DIR"], f"raw_{date_str}.json")
    
    if os.path.exists(raw_path):
        logging.info("♻️ 이미 검색된 날짜입니다! 캐시된 데이터를 불러옵니다. (API 비용 절감)")
        with open(raw_path, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
        
        cities_data = cached_data.get("cities", [])
        global_errors = cached_data.get("errors", [])
        
    else:
        # 캐시가 없을 때만 정상적으로 1~4단계 API 호출 진행
        # 1단계: 도시 3개 추천 (1곳 대표 + 2곳 숨은 소도시)
        logging.info("1단계: 도시 추천 중...")
        cities_info = recommend_cities(date_str, global_errors)

        # 2단계: 도시별 처리
        cities_data = []
        for city_info in cities_info:
            raw_city = city_info.get("recommended_city", "서울")

            # ---------------------------------------------------------
            # 입력 데이터 정규화 (공백 제거 및 표준화)
            # 예: "서울시 " -> "서울", " 하동군" -> "하동"
            # 내부 글자는 건드리지 않고 끝의 시/군만 제거
            # ---------------------------------------------------------
            city = re.sub(r"(시|군)\s*$", "", raw_city.strip())
            # ---------------------------------------------------------

            theme = city_info.get("theme", "도시")
            logging.info(f"--- [{city}] 처리 시작 ---")

            # 일정 추천
            logging.info(f"2단계: [{city}] 일정 추천 중...")
            schedule = recommend_schedule(city, theme, date_str, global_errors)

            # 맛집 검색
            logging.info(f"3단계: [{city}] 맛집 검색 중...")
            restaurants = search_restaurants(city, global_errors)

            # 도시별 최종 리포트 생성
            logging.info(f"4단계: [{city}] 리포트 생성 중...")
            city_report = build_city_report(date_str, city_info, schedule, restaurants)

            cities_data.append({
                "info": city_info,
                "schedule": schedule,
                "restaurants": restaurants,
                "city_report": city_report,
            })

            time.sleep(0.5)
    
    # 5단계: 전체 요약 리포트 (에러 내역 포함)
    logging.info("5단계: 전체 요약 리포트 생성 중...")
    summary_md = build_summary_report(date_str, cities_data, global_errors)

    # 6단계: 저장할 때 summary_md 와 errors 리스트도 넘겨주기
    logging.info("6단계: 결과 저장 중...")
    saved_files = save_results(date_str, cities_data, summary_md, global_errors)

    if not saved_files:
        logging.error("결과 저장에 실패했습니다.")
    print("\n" + "="*40)
    print("✅ 완료!")
    print("="*40)
    print(f"추천 도시: {', '.join([c['info']['recommended_city'] for c in cities_data])}")
    print("\n📁 저장된 파일:")
    for f in saved_files:
        print(f"   - {f}")
    print("="*40)


if __name__ == "__main__":
    main()