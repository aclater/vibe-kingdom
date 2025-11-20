#!/usr/bin/env python3
"""
kingdom_local.py – Local LLM-powered LinkedIn post engine

Uses a local OpenAI-compatible endpoint (e.g. RamaLama) to:
- fetch RSS -> ideas
- generate LinkedIn-style draft posts
- store state in kingdom_state.json
- export approved posts to CSV for scheduling
"""

import os
import sys
import json
import csv
import argparse
import datetime as dt
from typing import List, Dict, Any

import feedparser
import requests

STATE_FILE = "kingdom_state.json"
RSS_FILE = "rss_feeds.txt"

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss:20b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

CONSTITUTION = """
You are a LinkedIn ghostwriter for a senior public-sector architect working on:
- Public-sector IT modernization
- Open source (Linux, Red Hat, OpenShift, Ansible)
- AI/ML in government (governance, risk, practical use)
- Software supply chain security (SBOM, SLSA, FedRAMP, NIST)
- Practical architecture patterns (DevSecOps, hybrid cloud, automation)

Hard constraints:
- No partisan politics or commentary on candidates/elections.
- Do not reveal or speculate about non-public details of agencies, customers, or deals.
- Do not speak on behalf of specific agencies.
- Avoid internal company gossip or HR topics.
- Avoid emojis and hashtags unless explicitly requested.
- Tone: concise, pragmatic, slightly wry, "seen-it-before" senior architect.
- Max 220 words per post.
"""

def load_rss_feeds() -> List[str]:
    if not os.path.exists(RSS_FILE):
        print(f"[WARN] RSS file not found: {RSS_FILE}", file=sys.stderr)
        return []
    feeds = []
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if line and not line.startswith("#"):
                feeds.append(line)
    return feeds

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"ideas": [], "posts": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def next_id(items: List[Dict[str, Any]]) -> int:
    return 1 if not items else max(i["id"] for i in items) + 1

def fetch_news(max_per_feed: int = 5):
    feeds = load_rss_feeds()
    if not feeds:
        print("[ERROR] No RSS feeds found in rss_feeds.txt")
        return

    state = load_state()
    ideas = state.get("ideas", [])
    existing_urls = {i.get("url") for i in ideas if i.get("url")}
    now = dt.datetime.now(dt.UTC).isoformat()

    for feed in feeds:
        print(f"[INFO] Fetching feed: {feed}")
        parsed = feedparser.parse(feed)
        entries = parsed.entries[:max_per_feed]
        for e in entries:
            url = e.get("link")
            if not url or url in existing_urls:
                continue
            idea = {
                "id": next_id(ideas),
                "title": (e.get("title") or "").strip(),
                "summary": (e.get("summary") or "").strip(),
                "url": url,
                "source_feed": feed,
                "created_at": now,
                "published_raw": e.get("published", ""),
                "topic_guess": None,
            }
            ideas.append(idea)
            existing_urls.add(url)
            print(f"  + [{idea['id']}] {idea['title']}")

    state["ideas"] = ideas
    save_state(state)
    print("[INFO] Done fetching.")

def call_local_llm(system_prompt: str, user_prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7
    }

    try:
        r = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=300)
    except Exception as e:
        print(f"[ERROR] Cannot reach LLM endpoint {LLM_ENDPOINT}: {e}", file=sys.stderr)
        sys.exit(1)

    if r.status_code != 200:
        print(f"[ERROR] LLM returned {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)

    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[ERROR] Bad LLM response: {e}\n{data}")
        sys.exit(1)

def generate_posts(count: int = 6):
    state = load_state()
    ideas = sorted(state.get("ideas", []), key=lambda x: x["created_at"], reverse=True)
    if not ideas:
        print("[ERROR] No ideas yet. Run fetch-news first.")
        return

    ctx = "\n".join(
        f"- [{i['id']}] {i['title']}\n  {i['summary'][:200]}..."
        for i in ideas[:20]
    )

    prompt = f"""
Use these inputs to generate {count} LinkedIn posts in my voice:

{ctx}

Format:
1) Post text
2) Post text
...
Plain text only, no markdown beyond line breaks.
"""

    print(f"[INFO] Calling local LLM: {LLM_ENDPOINT}")
    raw = call_local_llm(CONSTITUTION, prompt)

    posts_text=[]
    current=[]
    prefixes={f"{i})" for i in range(1,10)}

    for line in raw.splitlines():
        s=line.strip()
        if len(s)>=2 and s[:2] in prefixes:
            if current:
                posts_text.append("\n".join(current).strip())
                current=[]
            current.append(s.split(")",1)[1].strip())
        else:
            current.append(line)
    if current:
        posts_text.append("\n".join(current).strip())

    state_posts=state.get("posts",[])
    now=dt.datetime.now(dt.UTC).isoformat()

    for body in posts_text:
        p={
            "id": next_id(state_posts),
            "body": body,
            "status": "draft",
            "created_at": now,
            "source":"local_llm"
        }
        state_posts.append(p)
        print(f"\n--- Draft [{p['id']}] ---\n{body}\n")

    state["posts"]=state_posts
    save_state(state)
    print(f"[INFO] Saved {len(posts_text)} posts.")

def list_posts(status=None):
    state=load_state()
    posts=state.get("posts",[])
    if status:
        posts=[p for p in posts if p["status"]==status]
    for p in posts:
        print(f"\n[{p['id']}] ({p['status']}) {p['created_at']}")
        print(p["body"])
        print("-"*40)

def set_status(pid:int, status:str):
    state=load_state()
    for p in state["posts"]:
        if p["id"]==pid:
            p["status"]=status
            save_state(state)
            print(f"[INFO] Set [{pid}] -> {status}")
            return
    print("[ERROR] Post not found")

def export_csv(outfile:str, hour:int=9):
    state=load_state()
    posts=[p for p in state["posts"] if p["status"]=="approved"]
    if not posts:
        print("[INFO] No approved posts.")
        return

    tomorrow = dt.date.today() + dt.timedelta(days=1)
    cur = tomorrow
    rows=[]
    for p in posts:
        while cur.weekday()>=5:
            cur += dt.timedelta(days=1)
        dt_full = dt.datetime.combine(
            cur, dt.time(hour=hour, tzinfo=dt.UTC)
        )
        rows.append({
            "scheduled_at_iso": dt_full.isoformat(),
            "body": p["body"]
        })
        cur += dt.timedelta(days=1)

    with open(outfile,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["scheduled_at_iso","body"])
        w.writeheader()
        w.writerows(rows)

    for p in state["posts"]:
        if p["status"]=="approved":
            p["status"]="exported"
    save_state(state)
    print(f"[INFO] Exported {len(rows)} posts -> {outfile}")

def main():
    ap=argparse.ArgumentParser(description="Local LLM LinkedIn post engine")
    sub=ap.add_subparsers(dest="cmd")

    f=sub.add_parser("fetch-news"); f.add_argument("--max-per-feed",type=int,default=5)
    g=sub.add_parser("generate-posts"); g.add_argument("--count",type=int,default=6)
    l=sub.add_parser("list-posts"); l.add_argument("--status")
    s=sub.add_parser("set-status"); s.add_argument("id",type=int); s.add_argument("status",choices=["draft","approved","exported"])
    e=sub.add_parser("export-csv"); e.add_argument("--outfile",default="posts_for_scheduler.csv"); e.add_argument("--hour",type=int,default=9)

    a=ap.parse_args()

    if a.cmd=="fetch-news": fetch_news(a.max_per_feed)
    elif a.cmd=="generate-posts": generate_posts(a.count)
    elif a.cmd=="list-posts": list_posts(a.status)
    elif a.cmd=="set-status": set_status(a.id,a.status)
    elif a.cmd=="export-csv": export_csv(a.outfile,a.hour)
    else: ap.print_help()

if __name__=="__main__":
    main()
