# -*- coding: utf-8 -*-
"""
TYM 채널 성과 대시보드 수집기
- 매일 1회 실행 (권장: 23:50 — 일 방문자 수가 그날 마감치에 가깝도록)
- Apify API 호출 2번(인스타 프로필 / 네이버 블로그 프로필)으로 전 지표 갱신
- 결과를 data.json 에 기록 (팔로워·방문자는 날짜별 누적, 나머지는 최신값으로 교체)
- 같은 날 재실행해도 안전 (그날 값만 덮어씀)

준비물: pip install requests
설정: 아래 APIFY_TOKEN 만 채우면 됨 (console.apify.com → Settings → API tokens)
"""
import json, os, re, sys, datetime, urllib.request

# ================== 설정 ==================
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "여기에_APIFY_토큰")
INSTA_USER  = "tymtractors_kr"
BLOG_ID     = "tymsns"
DATA_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
MAX_POSTS   = 12          # 블로그 최근 글 수집 개수 (비용: 글당 $0.002)
# ==========================================

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}&timeout=120"

# 게시물별 정확 지표 액터 (앱 화면과 동일 기준: 답글 포함 댓글 수, 전체 좋아요 수)
DETAIL_ACTOR = "data-slayer~instagram-post-details"

def run_actor(actor_id, payload):
    url = API.format(actor=actor_id, token=APIFY_TOKEN)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())

def today_str():
    return datetime.date.today().isoformat()

def month_prefix():
    return datetime.date.today().strftime("%Y-%m")

def upsert_series(series, d, v):
    """날짜별 시계열에 오늘 값을 추가(이미 있으면 교체)"""
    for p in series:
        if p["d"] == d:
            p["v"] = v
            return series
    series.append({"d": d, "v": v})
    series.sort(key=lambda p: p["d"])
    return series

def fetch_insta_details(post_urls):
    """게시물 URL별 정확한 (좋아요, 댓글) 반환.
    프로필 스크레이퍼의 요약치는 답글 제외·일부 좋아요 누락으로 앱 화면보다 낮게 나옴.
    이 액터는 게시물 내부 데이터를 직접 읽어 앱과 같은 값을 줌.
    실패하면 빈 dict 반환 → 호출부에서 프로필 요약치로 폴백."""
    if not post_urls:
        return {}
    try:
        items = run_actor(DETAIL_ACTOR, {"postUrls": post_urls})
        out = {}
        for it in items:
            m = it.get("metrics") or {}
            if it.get("code") and m.get("like_count") is not None:
                out[it["code"]] = (m.get("like_count") or 0, m.get("comment_count") or 0)
        return out
    except Exception as e:
        print(f"[경고] 게시물 상세 수집 실패 → 프로필 요약치 사용: {e}")
        return {}

# 제품 모델명 형태의 해시태그 (예: T4058N, 5075E, RGO660, T5088)
MODEL_RE = re.compile(r"^(?:[A-Z]{1,4}\d{3,4}[A-Z]{0,2}|\d{4}[A-Z])$")

def insta_title(post):
    """캡션 첫 줄(40자). 게시물 해시태그에 모델명이 있고 첫 줄에 없으면 뒤에 붙임."""
    first = (post.get("caption") or "").split("\n")[0].strip()[:40] or "(무제)"
    base = first.upper().replace("-", "")
    for h in post.get("hashtags") or []:
        hu = str(h).upper().replace("-", "")
        if MODEL_RE.match(hu) and hu not in base:
            return f"{first} · {h}"
    return first

def classify_insta(caption):
    c = (caption or "")
    if "Check Point" in c or "스펙" in c:
        return "제품"
    if "TIP" in c.upper() or "팁" in c or "정보" in c or "체크리스트" in c:
        return "정보"
    return "소식"

def main():
    if "여기에" in APIFY_TOKEN:
        sys.exit("APIFY_TOKEN을 설정하세요 (파일 상단 또는 환경변수)")

    # ---------- 기존 데이터 로드 ----------
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"insta": {"followers": []}, "naver": {"visitors": []}}
    d = today_str()

    # ---------- 1) 인스타그램 ----------
    items = run_actor("apify~instagram-profile-scraper", {"usernames": [INSTA_USER]})
    p = items[0]
    posts = p.get("latestPosts") or []
    insta = data.setdefault("insta", {})
    insta["followers"] = upsert_series(insta.get("followers", []), d, p["followersCount"])

    details = fetch_insta_details([x.get("url") for x in posts if x.get("url")])

    reacted = []
    for post in posts:
        likes = post.get("likesCount") or 0
        com = post.get("commentsCount") or 0
        if post.get("shortCode") in details:      # 정확값으로 교체
            likes, com = details[post["shortCode"]]
        title = insta_title(post)
        reacted.append({
            "d": (post.get("timestamp") or "")[:10],
            "title": title,
            "reactions": likes + com,
            "type": classify_insta(post.get("caption")),
        })
    reacted.sort(key=lambda x: x["d"], reverse=True)
    insta["monthlyPosts"] = sum(1 for x in reacted if x["d"].startswith(month_prefix()))
    insta["recent"] = [{k: x[k] for k in ("d", "title", "reactions")} for x in reacted[:3]]
    insta["best"] = [{"title": x["title"], "reactions": x["reactions"]}
                     for x in sorted(reacted, key=lambda x: -x["reactions"])[:3]]
    type_map = {}
    for x in reacted:
        type_map.setdefault(x["type"], []).append(x["reactions"])
    insta["typeAvg"] = [{"type": t, "avg": round(sum(v) / len(v))}
                        for t, v in type_map.items() if v]
    insta.setdefault("planPosts", 6)

    # ---------- 2) 네이버 블로그 ----------
    items = run_actor("hgservices~naver-blog-profile-scraper",
                      {"blogs": [BLOG_ID], "maxPosts": MAX_POSTS, "includeCategories": False})
    profile = next(x for x in items if x.get("type") == "profile")
    bposts = [x for x in items if x.get("type") == "post"]
    naver = data.setdefault("naver", {})
    naver["visitors"] = upsert_series(naver.get("visitors", []), d, profile["dayVisitorCount"])
    naver["subscribers"] = profile["subscriberCount"]
    naver["monthlyPosts"] = sum(1 for x in bposts if (x.get("addedAt") or "").startswith(month_prefix()))
    naver["recent"] = [{
        "d": (x.get("addedAt") or "")[:10],
        "title": x.get("title") or "(무제)",
        "reactions": (x.get("sympathyCount") or 0) + (x.get("commentCount") or 0),
    } for x in sorted(bposts, key=lambda x: x.get("addedAt") or "", reverse=True)[:3]]
    naver.setdefault("planPosts", 4)

    # ---------- 저장 ----------
    data["updated"] = d
    data["sample"] = False
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[{d}] 갱신 완료 — 팔로워 {p['followersCount']:,} · 일 방문자 {profile['dayVisitorCount']:,}")

if __name__ == "__main__":
    main()
