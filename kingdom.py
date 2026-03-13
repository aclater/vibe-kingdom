#!/usr/bin/env python3
"""
kingdom.py – LLM-powered LinkedIn post engine

Supports two engines:
  --engine cloud  (default) – OpenAI API
  --engine local             – local OpenAI-compatible endpoint (RamaLama, etc.)

Pipeline:
  fetch-news              Pull RSS feeds into the ideas store
  generate-posts          Use the LLM to draft posts from stored ideas
  list-posts              View drafts / approved / exported posts
  set-status              Move a post between draft → approved → exported
  export-csv              Export approved posts to a CSV for scheduling
  bootstrap-constitution  Generate a personalized constitution from public signals
  import-linkedin         Import a LinkedIn ZIP export into state
"""

import os
import sys
import json
import csv
import argparse
import datetime as dt
import zipfile
import io
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
from dotenv import load_dotenv
from openai import OpenAI

# ── Load .env before reading any env vars ─────────────────────────────────────
load_dotenv()

STATE_FILE = "kingdom_state.json"
RSS_FILE = "rss_feeds.txt"
CONSTITUTION_FILE = "constitution.txt"

# ── LLM config ────────────────────────────────────────────────────────────────
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:8080/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss:20b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local")

DEFAULT_CONSTITUTION = """
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


def load_constitution() -> str:
    """Load constitution from file if present, otherwise return DEFAULT_CONSTITUTION."""
    if os.path.exists(CONSTITUTION_FILE):
        with open(CONSTITUTION_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return content
    return DEFAULT_CONSTITUTION


# ── LLM client factory ────────────────────────────────────────────────────────

def make_client(engine: str) -> tuple[OpenAI, str]:
    """Return (OpenAI client, model name) for the requested engine."""
    if engine == "cloud":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
            sys.exit(1)
        return OpenAI(api_key=api_key), OPENAI_MODEL
    else:  # local
        return OpenAI(base_url=LLM_ENDPOINT, api_key=LLM_API_KEY), LLM_MODEL


def chat_completion(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> str:
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )
    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}", file=sys.stderr)
        sys.exit(1)
    return resp.choices[0].message.content.strip()


# ── State helpers ─────────────────────────────────────────────────────────────

def load_rss_feeds() -> List[str]:
    if not os.path.exists(RSS_FILE):
        print(f"[WARN] RSS file not found: {RSS_FILE}", file=sys.stderr)
        return []
    feeds = []
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
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


# ── Commands ──────────────────────────────────────────────────────────────────

def fetch_news(max_per_feed: int = 5):
    feeds = load_rss_feeds()
    if not feeds:
        print("[ERROR] No RSS feeds found in rss_feeds.txt")
        return

    state = load_state()
    ideas = state.get("ideas", [])
    existing_urls = {i.get("url") for i in ideas if i.get("url")}
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    for feed in feeds:
        print(f"[INFO] Fetching feed: {feed}")
        parsed = feedparser.parse(feed)
        for e in parsed.entries[:max_per_feed]:
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


def generate_posts(engine: str, count: int = 6):
    client, model = make_client(engine)
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

Format — number each post like this:
1) Post text here
2) Post text here

Plain text only, no markdown beyond line breaks.
"""

    constitution = load_constitution()
    print(f"[INFO] Calling {engine} LLM (model: {model})")
    raw = chat_completion(client, model, constitution, prompt)
    posts_text = _parse_numbered_posts(raw, count)

    state_posts = state.get("posts", [])
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    for body in posts_text:
        p = {
            "id": next_id(state_posts),
            "body": body,
            "status": "draft",
            "created_at": now,
            "source": engine,
        }
        state_posts.append(p)
        print(f"\n--- Draft [{p['id']}] ---\n{body}\n")

    state["posts"] = state_posts
    save_state(state)
    print(f"[INFO] Saved {len(posts_text)} posts.")


def _parse_numbered_posts(raw: str, expected: int) -> List[str]:
    """Split a numbered-list LLM response into individual post strings."""
    posts = []
    current: List[str] = []

    for line in raw.splitlines():
        s = line.strip()
        # Match lines like "1)", "2)", "10)", etc.
        parts = s.split(")", 1)
        if len(parts) == 2 and parts[0].isdigit():
            if current:
                posts.append("\n".join(current).strip())
                current = []
            current.append(parts[1].strip())
        else:
            current.append(line)

    if current:
        posts.append("\n".join(current).strip())

    return [p for p in posts if p]


def list_posts(status: str = None):
    state = load_state()
    posts = state.get("posts", [])
    if status:
        posts = [p for p in posts if p["status"] == status]
    if not posts:
        print("[INFO] No posts found.")
        return
    for p in posts:
        print(f"\n[{p['id']}] ({p['status']}) {p['created_at']}")
        print(p["body"])
        print("-" * 40)


def set_status(pid: int, status: str):
    state = load_state()
    for p in state["posts"]:
        if p["id"] == pid:
            p["status"] = status
            save_state(state)
            print(f"[INFO] Set [{pid}] -> {status}")
            return
    print("[ERROR] Post not found")


def export_csv(outfile: str, hour: int = 9):
    state = load_state()
    posts = [p for p in state["posts"] if p["status"] == "approved"]
    if not posts:
        print("[INFO] No approved posts.")
        return

    cur = dt.date.today() + dt.timedelta(days=1)
    rows = []
    for p in posts:
        while cur.weekday() >= 5:  # skip weekends
            cur += dt.timedelta(days=1)
        scheduled = dt.datetime(
            cur.year, cur.month, cur.day, hour, 0, 0,
            tzinfo=dt.timezone.utc
        )
        rows.append({"scheduled_at_iso": scheduled.isoformat(), "body": p["body"]})
        cur += dt.timedelta(days=1)

    with open(outfile, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["scheduled_at_iso", "body"])
        w.writeheader()
        w.writerows(rows)

    for p in state["posts"]:
        if p["status"] == "approved":
            p["status"] = "exported"
    save_state(state)
    print(f"[INFO] Exported {len(rows)} posts -> {outfile}")


# ── Bootstrap constitution helpers ────────────────────────────────────────────

def _fetch_tavily(name: str, employer: str) -> Optional[List[Dict[str, str]]]:
    """Fetch web search results from Tavily. Returns list of results or None on failure."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("[WARN] TAVILY_API_KEY not set — skipping Tavily web search.", file=sys.stderr)
        return None
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)

        results = {}

        # First search: general name + employer
        try:
            resp = client.search(
                f'"{name}" "{employer}"',
                max_results=10,
                search_depth="advanced"
            )
            for r in resp.get("results", []):
                url = r.get("url", "")
                if url and url not in results:
                    results[url] = {
                        "title": r.get("title", ""),
                        "url": url,
                        "content": (r.get("content") or "")[:300],
                    }
        except Exception as e:
            print(f"[WARN] Tavily first search failed: {e}", file=sys.stderr)

        # Second search: articles, talks, keynotes
        try:
            resp2 = client.search(
                f'"{name}" "{employer}" articles OR talks OR keynote OR interview',
                max_results=10,
                search_depth="advanced"
            )
            for r in resp2.get("results", []):
                url = r.get("url", "")
                if url and url not in results:
                    results[url] = {
                        "title": r.get("title", ""),
                        "url": url,
                        "content": (r.get("content") or "")[:300],
                    }
        except Exception as e:
            print(f"[WARN] Tavily second search failed: {e}", file=sys.stderr)

        return list(results.values()) if results else None
    except ImportError:
        print("[WARN] tavily-python not installed — skipping Tavily web search.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[WARN] Tavily search failed: {e}", file=sys.stderr)
        return None


def _fetch_youtube(name: str, employer: str) -> Optional[List[Dict[str, str]]]:
    """Fetch YouTube video transcripts. Returns list of video info or None on failure."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("[WARN] YOUTUBE_API_KEY not set — skipping YouTube search.", file=sys.stderr)
        return None
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=api_key)
        resp = youtube.search().list(
            q=f"{name} {employer}",
            part="snippet",
            type="video",
            maxResults=5
        ).execute()
        items = resp.get("items", [])
    except ImportError:
        print("[WARN] google-api-python-client not installed — skipping YouTube search.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[WARN] YouTube search failed: {e}", file=sys.stderr)
        return None

    results = []
    for item in items:
        video_id = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title", "")
        if not video_id:
            continue
        transcript_text = None
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            raw_text = " ".join(t["text"] for t in transcript)
            transcript_text = raw_text[:1500]
        except Exception:
            pass  # Many videos have no transcript — skip silently
        results.append({
            "title": title,
            "video_id": video_id,
            "transcript": transcript_text,
        })

    return results if results else None


def _sample_rss_for_bootstrap() -> List[Dict[str, str]]:
    """Sample RSS feeds for bootstrap — returns titles and short summaries."""
    feeds = load_rss_feeds()
    results = []
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:3]:
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "").strip()[:150]
                if title:
                    results.append({
                        "feed": feed_url,
                        "title": title,
                        "summary": summary,
                    })
        except Exception as e:
            print(f"[WARN] Failed to parse feed {feed_url}: {e}", file=sys.stderr)
    return results


def _parse_linkedin_zip(zip_path: str) -> Dict[str, Any]:
    """Parse a LinkedIn ZIP export and return structured data."""
    data: Dict[str, Any] = {
        "profile": {},
        "positions": [],
        "skills": [],
        "posts": [],
    }

    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"LinkedIn ZIP not found: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Build case-insensitive name map
        name_map = {name.lower(): name for name in zf.namelist()}

        def read_csv_from_zip(filename_lower: str) -> Optional[List[Dict[str, str]]]:
            """Read a CSV from the ZIP by case-insensitive filename."""
            actual = name_map.get(filename_lower)
            if not actual:
                return None
            with zf.open(actual) as raw:
                content = raw.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(content))
            return list(reader)

        # Profile.csv
        profile_rows = read_csv_from_zip("profile.csv")
        if profile_rows:
            row = profile_rows[0]
            data["profile"] = {
                "first_name": row.get("First Name", ""),
                "last_name": row.get("Last Name", ""),
                "headline": row.get("Headline", ""),
                "summary": row.get("Summary", ""),
                "industry": row.get("Industry", ""),
            }

        # Positions.csv
        positions_rows = read_csv_from_zip("positions.csv")
        if positions_rows:
            for row in positions_rows[:5]:
                data["positions"].append({
                    "company": row.get("Company Name", ""),
                    "title": row.get("Title", ""),
                    "description": row.get("Description", ""),
                })

        # Skills.csv
        skills_rows = read_csv_from_zip("skills.csv")
        if skills_rows:
            for row in skills_rows:
                name = row.get("Name", "").strip()
                if name:
                    data["skills"].append(name)

        # Shares.csv or Posts.csv
        posts_rows = read_csv_from_zip("shares.csv")
        commentary_col = "ShareCommentary"
        date_col = "Date"
        if posts_rows is None:
            posts_rows = read_csv_from_zip("posts.csv")
            commentary_col = "Commentary"

        if posts_rows:
            count = 0
            for row in posts_rows:
                text = (row.get(commentary_col) or "").strip()
                if not text:
                    continue
                data["posts"].append({
                    "text": text[:400],
                    "date": row.get(date_col, ""),
                })
                count += 1
                if count >= 30:
                    break

    return data


def bootstrap_constitution(
    name: str,
    employer: str,
    linkedin_zip: Optional[str],
    out_file: str,
    engine: str,
):
    """Generate a personalized constitution from multiple data sources."""
    print(f"[INFO] Bootstrapping constitution for {name} at {employer}")

    state = load_state()

    # Load cached LinkedIn data from state if available
    linkedin_data = state.get("linkedin")

    # Parse LinkedIn ZIP if provided
    if linkedin_zip:
        print(f"[INFO] Parsing LinkedIn export: {linkedin_zip}")
        try:
            linkedin_data = _parse_linkedin_zip(linkedin_zip)
            state["linkedin"] = linkedin_data
            save_state(state)
            print("[INFO] LinkedIn data saved to state.")
        except Exception as e:
            print(f"[WARN] Failed to parse LinkedIn ZIP: {e}", file=sys.stderr)

    # Gather all sources in parallel
    print("[INFO] Gathering signals from sources (parallel)...")
    tavily_results = None
    youtube_results = None
    rss_results = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_fetch_tavily, name, employer): "tavily",
            executor.submit(_fetch_youtube, name, employer): "youtube",
            executor.submit(_sample_rss_for_bootstrap): "rss",
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
                if source == "tavily":
                    tavily_results = result
                elif source == "youtube":
                    youtube_results = result
                elif source == "rss":
                    rss_results = result or []
            except Exception as e:
                print(f"[WARN] Source '{source}' failed: {e}", file=sys.stderr)

    # Build prompt sections
    profile_info = "Not available"
    if linkedin_data and linkedin_data.get("profile"):
        p = linkedin_data["profile"]
        profile_info = (
            f"Name: {p.get('first_name', '')} {p.get('last_name', '')}\n"
            f"Headline: {p.get('headline', '')}\n"
            f"Industry: {p.get('industry', '')}\n"
            f"Summary: {p.get('summary', '')[:500]}\n"
        )
        if linkedin_data.get("positions"):
            profile_info += "\nPositions:\n"
            for pos in linkedin_data["positions"]:
                profile_info += f"  - {pos.get('title', '')} at {pos.get('company', '')}\n"
                desc = pos.get("description", "")
                if desc:
                    profile_info += f"    {desc[:200]}\n"
        if linkedin_data.get("skills"):
            profile_info += f"\nSkills: {', '.join(linkedin_data['skills'][:20])}\n"

    tavily_text = "Not available"
    if tavily_results:
        lines = []
        for r in tavily_results[:10]:
            lines.append(f"- [{r['title']}]({r['url']})\n  {r['content']}")
        tavily_text = "\n".join(lines)

    youtube_text = "Not available"
    if youtube_results:
        lines = []
        for v in youtube_results:
            lines.append(f"- {v['title']}")
            if v.get("transcript"):
                lines.append(f"  Transcript excerpt: {v['transcript'][:500]}")
            else:
                lines.append("  (no transcript available)")
        youtube_text = "\n".join(lines)

    rss_text = "Not available"
    if rss_results:
        lines = []
        for r in rss_results:
            lines.append(f"- {r['title']}: {r['summary']}")
        rss_text = "\n".join(lines[:30])

    linkedin_posts_text = "Not available"
    if linkedin_data and linkedin_data.get("posts"):
        lines = []
        for post in linkedin_data["posts"][:15]:
            lines.append(f"[{post.get('date', '')}] {post['text']}")
        linkedin_posts_text = "\n\n".join(lines)

    synthesis_prompt = f"""You are creating a personalized CONSTITUTION — a system prompt for an AI LinkedIn ghostwriter.

Analyze the following signals about {name} at {employer} and write a CONSTITUTION in their voice.

## Professional Profile
{profile_info}

## Public Articles and Press Mentions
{tavily_text}

## Conference Talks and Videos
{youtube_text}

## Topics from Followed RSS Feeds
{rss_text}

## Sample LinkedIn Posts
{linkedin_posts_text}

---

Write a CONSTITUTION using EXACTLY this format (no extra sections, no markdown):

You are a LinkedIn ghostwriter for [1-2 sentence description of this specific person and their domain].

Topics:
- [specific topic — be concrete, not generic]
- [...]

Hard constraints:
- [constraint inferred from their role and public positioning]
- [...]

Tone: [description inferred from their actual writing/speaking style]
Max [N] words per post.

Be specific. Generic constitutions produce generic posts. Infer everything you can from the signals provided."""

    print("[INFO] Calling LLM to synthesize constitution...")
    client, model = make_client(engine)
    constitution_text = chat_completion(
        client, model,
        "You are a helpful assistant that creates detailed, personalized writing guidelines.",
        synthesis_prompt,
    )

    print("\n" + "=" * 60)
    print("Generated Constitution:")
    print("=" * 60)
    print(constitution_text)
    print("=" * 60 + "\n")

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(constitution_text + "\n")

    print(f"[INFO] Constitution saved to {out_file}")


def import_linkedin(zip_path: str):
    """Import a LinkedIn ZIP export into state."""
    print(f"[INFO] Importing LinkedIn export: {zip_path}")
    try:
        data = _parse_linkedin_zip(zip_path)
    except Exception as e:
        print(f"[ERROR] Failed to parse LinkedIn ZIP: {e}", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    state["linkedin"] = data
    save_state(state)

    print("[INFO] LinkedIn import complete.")
    print(f"  Profile: {data['profile'].get('first_name', '')} {data['profile'].get('last_name', '')} — {data['profile'].get('headline', '')}")
    print(f"  Positions: {len(data['positions'])} found")
    print(f"  Skills: {len(data['skills'])} found")
    print(f"  Posts: {len(data['posts'])} found")
    if data["skills"]:
        print(f"  Top skills: {', '.join(data['skills'][:10])}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="LLM-powered LinkedIn post engine")
    ap.add_argument(
        "--engine", choices=["cloud", "local"], default="cloud",
        help="LLM engine: 'cloud' (OpenAI API) or 'local' (RamaLama / OpenAI-compatible endpoint)"
    )
    sub = ap.add_subparsers(dest="cmd")

    f = sub.add_parser("fetch-news", help="Fetch RSS feeds into the ideas store")
    f.add_argument("--max-per-feed", type=int, default=5)

    g = sub.add_parser("generate-posts", help="Generate draft posts from stored ideas")
    g.add_argument("--count", type=int, default=6)

    l = sub.add_parser("list-posts", help="List posts, optionally filtered by status")
    l.add_argument("--status", choices=["draft", "approved", "exported"])

    s = sub.add_parser("set-status", help="Change the status of a post")
    s.add_argument("id", type=int)
    s.add_argument("status", choices=["draft", "approved", "exported"])

    e = sub.add_parser("export-csv", help="Export approved posts to a scheduling CSV")
    e.add_argument("--outfile", default="posts_for_scheduler.csv")
    e.add_argument("--hour", type=int, default=9)

    bc = sub.add_parser(
        "bootstrap-constitution",
        help="Generate a personalized constitution from public signals and LinkedIn data"
    )
    bc.add_argument("--name", required=True, help="Full name of the person")
    bc.add_argument("--employer", required=True, help="Employer/company name")
    bc.add_argument("--linkedin-zip", default=None, help="Path to LinkedIn ZIP export")
    bc.add_argument("--out", default="constitution.txt", help="Output file (default: constitution.txt)")

    il = sub.add_parser("import-linkedin", help="Import a LinkedIn ZIP export into state")
    il.add_argument("zip_path", help="Path to LinkedIn ZIP export file")

    a = ap.parse_args()

    if a.cmd == "fetch-news":
        fetch_news(a.max_per_feed)
    elif a.cmd == "generate-posts":
        generate_posts(a.engine, a.count)
    elif a.cmd == "list-posts":
        list_posts(a.status)
    elif a.cmd == "set-status":
        set_status(a.id, a.status)
    elif a.cmd == "export-csv":
        export_csv(a.outfile, a.hour)
    elif a.cmd == "bootstrap-constitution":
        bootstrap_constitution(a.name, a.employer, a.linkedin_zip, a.out, a.engine)
    elif a.cmd == "import-linkedin":
        import_linkedin(a.zip_path)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
