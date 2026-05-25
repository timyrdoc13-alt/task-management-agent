"""Research runner: web search -> fetch -> DeepSeek synthesize -> save markdown + docx.

Search: Serper (Google JSON) if SERPER_API_KEY set, else DuckDuckGo HTML scrape.
Budgets: configurable fetches and wall time; sources treated as untrusted text.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaiten_api import ARTIFACTS_DIR, ENV, MSK, slugify  # noqa: E402
from llm import (  # noqa: E402
    extract_research_facts,
    search_queries_for,
    stream_research_tldr,
    synthesize_research,
)

EmitFn = Callable[..., None]


class ResearchCancelledError(Exception):
    """Cooperative cancel from job_store / TG /cancel."""
from report_export import export_report_files, kaiten_card_description  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MAX_BYTES_PER_PAGE = 2 * 1024 * 1024
MAX_CHARS_PER_PAGE = 6000


def _fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept-Language", "ru,en;q=0.8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES_PER_PAGE)
        enc = resp.headers.get_content_charset() or "utf-8"
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")


def _strip_html(s: str) -> str:
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_ddg_href(raw_href: str) -> str | None:
    """DDG HTML returns protocol-relative redirect URLs: //duckduckgo.com/l/?uddg=..."""
    href = html.unescape(raw_href).strip()
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        href = urllib.parse.unquote((q.get("uddg") or [""])[0])
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def serper_search(query: str, limit: int | None = None) -> list[dict]:
    """Google search via Serper API (https://serper.dev). Returns [{url, title}, ...]."""
    api_key = (ENV.get("SERPER_API_KEY") or "").strip()
    if not api_key:
        return []
    limit = limit or int(ENV.get("RESEARCH_RESULTS_PER_QUERY", "4"))
    num = min(max(limit + 2, 6), 10)
    payload = json.dumps({"q": query, "num": num}).encode("utf-8")
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=payload,
        method="POST",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Serper HTTP {e.code}: {err_body}") from e
    except Exception as e:
        raise RuntimeError(f"Serper request failed: {e}") from e

    out: list[dict] = []
    for item in (data.get("organic") or [])[:limit]:
        link = (item.get("link") or "").strip()
        title = (item.get("title") or link or "")[:200]
        if link.startswith("http://") or link.startswith("https://"):
            out.append({"url": link, "title": title})
    return out


def web_search(query: str, limit: int | None = None) -> tuple[list[dict], str]:
    """Serper if key + results, else DuckDuckGo. Returns (results, backend name)."""
    limit = limit or int(ENV.get("RESEARCH_RESULTS_PER_QUERY", "4"))
    if (ENV.get("SERPER_API_KEY") or "").strip():
        try:
            res = serper_search(query, limit)
            if res:
                return res, "serper"
        except RuntimeError:
            pass
    return ddg_search(query, limit), "ddg"


def ddg_search(query: str, limit: int | None = None) -> list[dict]:
    limit = limit or int(ENV.get("RESEARCH_RESULTS_PER_QUERY", "4"))
    """DuckDuckGo HTML search (no API key). Tries html.duckduckgo.com then duckduckgo.com/html."""
    endpoints = (
        "https://html.duckduckgo.com/html/",
        "https://duckduckgo.com/html/",
    )
    results: list[dict] = []
    for base in endpoints:
        url = base + "?" + urllib.parse.urlencode({"q": query})
        try:
            body = _fetch(url, timeout=15)
        except Exception:
            continue
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            body,
            re.S,
        ):
            resolved = _resolve_ddg_href(m.group(1))
            if not resolved or "duckduckgo.com" in resolved:
                continue
            title = _strip_html(m.group(2))[:200]
            results.append({"url": resolved, "title": title})
            if len(results) >= limit:
                return results
        if results:
            return results
        # DDG иногда отдаёт «пустую» страницу без result__a — пауза и второй endpoint
        time.sleep(1.5)
    return results


def fetch_and_extract(url: str) -> dict:
    try:
        body = _fetch(url, timeout=20)
        text = _strip_html(body)
        # try to extract <title>
        mt = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
        title = _strip_html(mt.group(1)) if mt else url
        return {"url": url, "title": title[:200], "text": text[:MAX_CHARS_PER_PAGE], "ok": True}
    except Exception as e:
        return {"url": url, "title": url, "text": "", "ok": False, "error": str(e)[:200]}


def _emit(emit: EmitFn | None, phase: str, **data) -> None:
    if emit:
        try:
            emit(phase, **data)
        except Exception:
            pass


def run_research(
    topic: str,
    max_fetches: int | None = None,
    wall_time_s: int | None = None,
    emit: EmitFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    global MAX_CHARS_PER_PAGE
    MAX_CHARS_PER_PAGE = int(ENV.get("RESEARCH_CHARS_PER_PAGE", "6000"))
    max_fetches = max_fetches or int(ENV.get("AUTO_RESEARCH_MAX_FETCHES", "12"))
    wall_time_s = wall_time_s or int(ENV.get("AUTO_RESEARCH_WALL_TIME_SEC", "480"))
    fetch_workers = int(ENV.get("RESEARCH_FETCH_WORKERS", "4"))
    stream_tldr = ENV.get("RESEARCH_STREAM_TLDR", "true").lower() in {"1", "true", "yes"}
    t0 = time.time()

    def _cancelled() -> bool:
        try:
            return bool(should_cancel and should_cancel())
        except Exception:
            return False

    if _cancelled():
        raise ResearchCancelledError("cancelled before start")

    _emit(emit, "status", text=f"🔍 Планирую поиск по теме…")
    queries = search_queries_for(topic)
    # Запасной английский запрос, если LLM дал только русские (DDG хуже их парсит)
    if not any(re.search(r"[a-zA-Z]{4,}", q) for q in queries):
        queries.append(
            "Open WebUI local LLM Qwen GPT OSS enterprise deployment"
            if "webui" in topic.lower() or "qwen" in topic.lower()
            else " ".join(w for w in re.findall(r"[A-Za-z0-9]{3,}", topic)[:6]) or topic[:80]
        )
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    search_backend = "none"
    for q in queries:
        if _cancelled():
            raise ResearchCancelledError("cancelled during search")
        if time.time() - t0 > wall_time_s * 0.4:
            break
        results, backend = web_search(q)
        if search_backend == "none":
            search_backend = backend
        elif backend != search_backend and search_backend != "mixed":
            search_backend = "mixed"
        for r in results:
            if r["url"] in seen_urls:
                continue
            seen_urls.add(r["url"])
            candidates.append(r)
        time.sleep(0.35 if backend == "serper" else 0.5)

    _emit(
        emit,
        "status",
        text=f"📎 Найдено {len(candidates)} ссылок ({search_backend}). Читаю страницы…",
    )

    fetched: list[dict] = []
    fetch_errors: list[str] = []
    to_fetch = candidates[: max_fetches * 2]
    done_n = 0
    with ThreadPoolExecutor(max_workers=max(1, fetch_workers)) as pool:
        futures = {pool.submit(fetch_and_extract, c["url"]): c for c in to_fetch}
        for fut in as_completed(futures):
            if _cancelled():
                raise ResearchCancelledError("cancelled during fetch")
            if len(fetched) >= max_fetches:
                break
            if time.time() - t0 > wall_time_s * 0.85:
                break
            done_n += 1
            c = futures[fut]
            try:
                page = fut.result()
            except Exception as e:
                fetch_errors.append(f"{c.get('url', '?')}: {e}")
                continue
            if page["ok"] and len(page["text"]) > 200:
                fetched.append(page)
            else:
                err = page.get("error") or f"мало текста ({len(page.get('text', ''))} симв.)"
                fetch_errors.append(f"{c.get('url', '?')}: {err}")
            if done_n % 2 == 0:
                _emit(
                    emit,
                    "status",
                    text=(
                        f"📥 Прочитано страниц: {len(fetched)}/{max_fetches} "
                        f"(попыток {done_n}/{len(to_fetch)})"
                    ),
                )

    facts = None
    two_pass = ENV.get("RESEARCH_TWO_PASS", "true").lower() in {"1", "true", "yes"}

    if not fetched:
        if not candidates:
            reason = (
                "Поиск не вернул ссылок (Serper: проверь SERPER_API_KEY и лимит; "
                "DuckDuckGo: смена вёрстки или блокировка)."
            )
        else:
            reason = (
                f"Найдено {len(candidates)} ссылок, но ни одна страница не отдалась для разбора "
                f"(таймаут, CAPTCHA, paywall или пустая разметка)."
            )
        markdown = (
            f"# {topic}\n\n"
            "## Результат\n\n"
            f"{reason}\n\n"
            "## Поисковые запросы\n" + "\n".join(f"- {q}" for q in queries)
        )
        if candidates:
            markdown += "\n\n## Найденные ссылки (не удалось прочитать)\n" + "\n".join(
                f"- [{c.get('title', c['url'])}]({c['url']})" for c in candidates[:10]
            )
        if fetch_errors:
            markdown += "\n\n## Ошибки загрузки\n" + "\n".join(f"- {e}" for e in fetch_errors[:8])
    else:
        if _cancelled():
            raise ResearchCancelledError("cancelled before synthesis")
        if two_pass:
            _emit(emit, "status", text=f"🧠 Извлекаю факты из {len(fetched)} источников…")
            facts = extract_research_facts(topic, fetched)
        if stream_tldr and emit:
            _emit(emit, "status", text=f"✍️ Краткое резюме (стрим)… источников: {len(fetched)}")
            try:
                for chunk in stream_research_tldr(topic, fetched, facts):
                    _emit(emit, "tldr_delta", text=chunk)
            except Exception:
                pass
        _emit(emit, "status", text="📄 Собираю полный отчёт (DOCX)…")
        markdown = synthesize_research(topic, fetched, time.time() - t0, facts=facts)

    date = datetime.now(MSK).strftime("%Y-%m-%d")
    dest_dir = ARTIFACTS_DIR / f"{date}-{slugify(topic)}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    report = dest_dir / "report.md"
    report.write_text(markdown, encoding="utf-8")
    if fetched and two_pass and facts:
        (dest_dir / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2))
    exported = export_report_files(report)
    kaiten_desc = kaiten_card_description(markdown, topic)
    sources_path = dest_dir / "sources.json"
    sources_path.write_text(json.dumps(
        [{"url": p["url"], "title": p["title"]} for p in fetched], ensure_ascii=False, indent=2
    ))
    meta = {
        "topic": topic,
        "created_at": datetime.now(MSK).isoformat(timespec="seconds"),
        "queries": queries,
        "candidates": len(candidates),
        "fetched": len(fetched),
        "wall_time_s": round(time.time() - t0, 1),
        "report": str(report),
        "report_docx": exported["docx"],
        "kaiten_description": kaiten_desc,
        "search_backend": search_backend,
        "empty_reason": "no_candidates" if not candidates else ("no_fetched" if not fetched else None),
    }
    (dest_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    _emit(emit, "done")

    attach_fmt = ENV.get("RESEARCH_ATTACH_FORMAT", "docx").lower()
    attach_path = exported["docx"] if attach_fmt != "md" else str(report)

    return {
        "status": "success",
        "report_path": attach_path,
        "report_md_path": str(report),
        "report_docx_path": exported["docx"],
        "kaiten_description": kaiten_desc,
        "sources_path": str(sources_path),
        "meta": meta,
    }


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "best practices monorepo Next.js 15"
    print(f"Researching: {topic}")
    out = run_research(topic)
    print(json.dumps(out, ensure_ascii=False, indent=2))
