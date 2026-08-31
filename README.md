# 🗺️ 국내 여행 추천 프로그램

> OpenAI(gpt-4o-mini) + Kakao Local API를 활용한 AI 기반 국내 여행 추천 자동화 시스템으로, 여행 날짜에 최적화된 국내 대표 명소 및 숨은 소도시를 추천하고 일정별 맛집 동선까지 계산해 리포트를 자동 생성하는 파이프라인입니다.

---


## 📌 프로그램 개요 및 특징

날짜를 입력하면 AI가 자동으로:

* **차별화된 도시 구성**: 1곳의 대표 인기 관광지와 2곳의 한적한 숨은 소도시/군 단위 지역을 조합하여 폭넓은 여행 선택지를 제공합니다.
* **추천 다양성 고도화**: `temperature(0.95)` 및 `presence_penalty(0.6)`를 적용하여 날짜별·실행별 고정된 도시 중복 추천을 최소화합니다.
* **장소-맛집 근접 동선 연계**: 도시별 '1일 여행 일정(오전/오후/저녁)' 생성과 장소와 카카오 API로 수집된 맛집 간의 직선 거리를 계산하고 최단 거리 맛집을 자동 매칭합니다.
* **정밀 위치 데이터 규격화**: 위도/경도 좌표를 소수점 5자리(약 1.1m 정밀도)로 반올림 처리하여 데이터 가독성을 높였습니다.
* **상세 리포트** + **전체 요약 리포트** 자동 생성 및 저장 


---

## 🔄 전체 흐름도

```mermaid
flowchart TD
    %% 커스텀 색상 및 스타일 정의
    classDef inputNode fill:#E1F5FE,stroke:#0288D1,stroke-width:2px,color:#01579B,font-weight:bold
    classDef aiNode fill:#F3E5F5,stroke:#8E24AA,stroke-width:2px,color:#4A148C
    classDef apiNode fill:#FFF8E1,stroke:#FFA000,stroke-width:2px,color:#FF6F00
    classDef docNode fill:#E8F5E9,stroke:#388E3C,stroke-width:2px,color:#1B5E20
    classDef saveNode fill:#ECEFF1,stroke:#546E7A,stroke-width:2px,color:#263238
    classDef endNode fill:#4CAF50,stroke:#388E3C,stroke-width:2px,color:#FFFFFF,font-weight:bold

    %% 노드 정의
    A([👤 사용자 입력<br/>--date YYYY-MM-DD])
    
    B["🤖 1단계: 도시 추천<br/>(대표 1곳 + 소도시 2곳)"]
    C["🤖 2단계: 1일 일정 생성<br/>(오전/오후/저녁)"]
    
    D["🗺️ 3단계: 맛집 검색<br/>(Kakao Local API)"]
    E["🧭 동선 매칭<br/>(일정 장소 ↔ 최단거리 맛집)"]
    
    F["📄 4단계: 도시별 상세 리포트<br/>(개별 MD 문서)"]
    G["📊 5단계: 통합 요약 리포트<br/>(비교 표 + 링크)"]
    
    H[/"💾 6단계: 결과물 저장<br/>(.md & .json)"/]
    I([✅ 추천 프로세스 완료])

    %% 스타일 적용
    class A inputNode
    class B,C aiNode
    class D,E apiNode
    class F,G docNode
    class H saveNode
    class I endNode

    %% 연결선 (흐름)
    A ==>|파이썬 실행| B
    B ==>|3개 도시 루프| C
    C ==> D
    D -. "위도/경도 데이터" .-> E
    E ==> F
    F ==> G
    G ==> H
    H ==> I
```

### 📄 travel_planner.py 함수 구성

```
travel_planner.py
│
├── CONFIG                      환경변수 로드 (API 키, 경로 설정)
├── parse_args()                CLI 인자 파싱 (--date)
│
├── recommend_cities()          1단계: OpenAI로 도시 2~3개 추천
│   └── OpenAI Chat API 호출
│       └── JSON 파싱 (도시명/행정구역/테마/날씨/행사/추천이유)
│
├── recommend_schedule()        2단계: OpenAI로 1일 일정 생성
│   └── OpenAI Chat API 호출
│       └── JSON 파싱 (오전/오후/저녁 시간/활동/장소/팁)
│
├── search_restaurants()        3단계: Kakao API로 맛집 검색
│   └── Kakao 로컬 API 호출
│       └── 위도/경도/주소/카테고리/URL 파싱
│
├── build_city_report()         4단계: 도시별 Markdown 리포트 생성
│   ├── 추천 지역 & 이유 (표)
│   ├── 날씨 요약
│   ├── 행사/축제 목록
│   ├── 맛집 리스트 (표)
│   └── 1일 일정 (오전/오후/저녁)
│
├── build_summary_report()      5단계: 전체 요약 리포트 생성
│   ├── 도시 비교 테이블
│   └── 도시별 요약 + 상세 리포트 링크
│
├── save_results()              6단계: 파일 저장
│   ├── report_날짜_도시.md (도시별)
│   ├── summary_날짜.md (전체 요약)
│   └── raw_날짜.json (원본 데이터)
│
└── main()                      전체 흐름 제어
```

---

## 📁 결과 파일 구성

### 1. `summary_YYYY-MM-DD.md` - 전체 요약 리포트

```
# 🗺️ YYYY-MM-DD 국내 여행 추천 요약

## 📍 추천 3개 도시 메타데이터 비교 테이블
| 도시 | 행정구역 | 테마 | 날씨 | 맛집 수 |
|------|----------|------|------|---------|
| 제주도 | 제주특별자치도 | 자연 | 맑음 25도 | 10곳 |
| 경주  | 경상북도       | 역사 | 맑음 28도 | 8곳  |
| 부산  | 부산광역시     | 해양 | 맑음 27도 | 9곳  |

## 도시별 핵심 요약 및 개별 리포트 파일 링크
```

### 2. `report_YYYY-MM-DD_도시명.md` - 도시별 상세 리포트

```
# 최종 여행 리포트: 도시명

1️⃣ 추천 지역 & 추천 이유   ← 도시/행정구역/테마/추천이유 표
2️⃣ 날씨 요약               ← 날씨 한 줄 요약
3️⃣ 행사/축제 목록           ← 해당 날짜 주변 행사
4️⃣ 맛집 리스트              ← 상호명/카테고리/별점/주소/좌표(위도&경도)/링크
5️⃣ 1일 여행 일정 및 알정 정소별 최단 거리 추천 맛집 매팅 표기   ← 오전/오후/저녁 활동/장소/팁
```

### 3. `raw_YYYY-MM-DD.json` - 원본 JSON 데이터
* 추천 정보, 일정, 맛집 전체 파싱 원천 데이터

```json
{
  "date": "2026-08-31",
  "cities": [
    {
      "info": {
        "city": "제주도",
        "region": "제주특별자치도",
        "theme": "자연",
        "weather": "온화하고 맑음, 평균 기온 25도",
        "events": ["제주 바다의 날"],
        "reason": "추천 이유..."
      },
      "schedule": {
        "morning":   { "time": "09:00", "activity": "...", "place": "...", "tip": "..." },
        "afternoon": { "time": "13:00", "activity": "...", "place": "...", "tip": "..." },
        "evening":   { "time": "18:00", "activity": "...", "place": "...", "tip": "..." }
      },
      "restaurants": [
        {
          "place_name": "흑돼지 맛집",
          "category_name": "음식점 > 한식",
          "address_name": "제주시 ...",
          "latitude": 33.123,
          "longitude": 126.456,
          "place_url": "https://place.map.kakao.com/..."
        }
      ]
    }
  ]
}
```

---

## ⚙️ 설치 및 실행

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. API 키 설정 (`.env`)

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
KAKAO_REST_API_KEY=xxxxxxxxxxxxxxxx
```

### 3. 실행

```bash
# 날짜 지정 실행
python travel_planner.py --date "2026-08-31"

# 오늘 날짜로 실행 (기본값)
python travel_planner.py
```

---

## 📦 requirements.txt

```
openai
requests
python-dotenv
```

---

## 🔑 API 키 발급

| API | 발급 경로 | 용도 |
|-----|-----------|------|
| OpenAI | https://platform.openai.com | 도시 추천, 일정 생성 |
| Kakao | https://developers.kakao.com | 맛집 검색 (로컬 API) |

---

## 📊 실행 예시

```bash
$ python travel_planner.py --date "2026-08-31"

========================================
✅ 완료!
========================================
추천 도시: 제주도, 경주, 부산

📁 저장된 파일:
   - results/report_2026-08-31_제주도.md
   - results/report_2026-08-31_경주.md
   - results/report_2026-08-31_부산.md
   - results/summary_2026-08-31.md
   - results/raw_2026-08-31.json
========================================
```

---

## ⚠️ 주의사항

- `.env` 파일은 절대 GitHub에 올리지 마세요 (`.gitignore` 등록 필수)
- OpenAI API는 사용량에 따라 비용이 발생합니다
- Kakao API는 일일 호출 한도가 있습니다

---

## 📝 .gitignore 권장 설정

```
.env
logs/
results/
__pycache__/
*.pyc
```