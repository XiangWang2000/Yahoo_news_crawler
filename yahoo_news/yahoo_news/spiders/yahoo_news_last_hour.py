import csv
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

import scrapy


class YahooListServiceLastHourSpider(scrapy.Spider):
    name = "yahoo_news_last_hour"
    allowed_domains = ["yahoo.com"] # 允許 Yahoo 子網域，避免文章頁 redirect 被 OffsiteMiddleware 擋掉

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS": 8,
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "*/*",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://tw.news.yahoo.com/",
            "X-Requested-With": "XMLHttpRequest",
        },
    }

    BASE = "https://tw.news.yahoo.com/_td-news/api/resource/ListService;api=archive;ncpParams="

    def __init__(self, mode="start", buffer_minutes=20, hours=1, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.mode = (mode or "start").strip().lower() #決定程式從哪個時間點往前抓
        # mode: start(以程式啟動時間為窗口上界) / finish(以結束時間) / newest(以抓到的最新文章時間)
        if self.mode not in {"start", "finish", "newest"}:
            self.mode = "start"

        self.tz = ZoneInfo("Asia/Taipei")
        hours = max(1, float(hours))
        self.window = timedelta(hours=hours) #預設近1小時

        try:
            buffer_m = int(buffer_minutes)
        except Exception:
            buffer_m = 20
        buffer_m = max(0, min(buffer_m, 120))
        self.buffer = timedelta(minutes=buffer_m) #多抓一些時間，後續再進行裁切

        self.start_time = datetime.now(self.tz)
        self.crawl_cutoff = (self.start_time - self.window) - self.buffer # 爬取門檻：先多抓(buffer)，最後在 closed() 再裁切成精準窗口

        self.count = 20
        self.max_pages = 160

        self.stop_pages_required = 3
        self.old_pages_streak = 0
        self.refreshed_first_page = False # 列表可能有快照延遲/刷新後才出現的新文章：停爬前 refresh 一次 start=0 補齊最新端缺口

        self.results = []
        self.seen = set() # 列表可能重複推同篇文章；用 link 去重避免重抓/重寫

    def start_requests(self):
        yield scrapy.Request(self._make_url(0), callback=self.parse_list, meta={"page": 0}, dont_filter=True) # 由程式自行控管去重(seen)；並允許 refresh 首頁

    def parse_list(self, response):
        try:
            data = response.json()
        except Exception:
            self.logger.warning("Non-JSON response (first 200): %s", response.text[:200])
            return

        items = self._find_story_items(data)
        if not items:
            self.logger.info("No items found.")
            return

        page_has_recent = False

        for it in items:
            rel_url = (it.get("url") or "").strip()
            ts = it.get("published_at")
            if not rel_url or not ts:
                continue

            pub_dt = self._parse_epoch_seconds(ts)
            if not pub_dt or pub_dt < self.crawl_cutoff:
                continue

            page_has_recent = True
            full_url = response.urljoin(rel_url)
            if full_url in self.seen:
                continue
            self.seen.add(full_url)

            yield response.follow(
                full_url,
                callback=self.parse_article,
                meta={
                    "link": full_url,
                    "title": (it.get("title") or "").strip(),
                    "date": pub_dt,
                    "provider_name": (it.get("provider_name") or "").strip(),
                },
                dont_filter=True,
            )

        self.old_pages_streak = 0 if page_has_recent else self.old_pages_streak + 1

        page = response.meta["page"]
        
        if self.old_pages_streak >= self.stop_pages_required and not self.refreshed_first_page:
            self.refreshed_first_page = True
            self.logger.info("Refreshing first page once before stopping...")
            yield scrapy.Request(self._make_url(0), callback=self.parse_list, meta={"page": 0}, dont_filter=True)
            return

        if self.old_pages_streak < self.stop_pages_required and (page + 1 < self.max_pages):
            yield scrapy.Request(
                self._make_url((page + 1) * self.count),
                callback=self.parse_list,
                meta={"page": page + 1},
                dont_filter=True,
            )

    def parse_article(self, response):
        author = self._extract_author_from_article(response) or response.meta["provider_name"]
        self.results.append({
            "link": response.meta["link"],
            "title": response.meta["title"],
            "author": author,
            "date": response.meta["date"],
        })

    def closed(self, reason):
        if not self.results:
            self.logger.info("No results.")
            return

        self.results.sort(key=lambda x: x["date"], reverse=True)
        newest = self.results[0]["date"]
        oldest = self.results[-1]["date"]

        self.logger.info(
            "RAW Newest=%s Oldest=%s Span=%.2f minutes (mode=%s start_time=%s crawl_cutoff=%s buffer=%s)",
            newest.isoformat(),
            oldest.isoformat(),
            (newest - oldest).total_seconds() / 60.0,
            self.mode,
            self.start_time.isoformat(),
            self.crawl_cutoff.isoformat(),
            self.buffer,
        )

        if self.mode == "start":
            window_end = self.start_time
        elif self.mode == "finish":
            window_end = datetime.now(self.tz)
        else:  # newest news
            window_end = newest

        window_start = window_end - self.window
        # 進行資料裁切
        filtered = [r for r in self.results if window_start <= r["date"] <= window_end]
        filtered.sort(key=lambda x: x["date"], reverse=True)

        # 用 window_end 當檔名時間戳（與本次窗口對齊）
        ts = window_end.strftime("%Y-%m-%d_%H%M%S")
        hours_str = int(self.window.total_seconds() // 3600)
        out_file = f"yahoo_last_{hours_str}h_{ts}.csv"

        with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["link", "title", "author", "date"])
            w.writeheader()
            for r in filtered:
                w.writerow({**r, "date": r["date"].isoformat()})

        if filtered:
            self.logger.info(
                "FINAL Saved=%d file=%s window_end=%s window_start=%s FINAL Span=%.2f minutes",
                len(filtered),
                out_file,
                window_end.isoformat(),
                window_start.isoformat(),
                (filtered[0]["date"] - filtered[-1]["date"]).total_seconds() / 60.0,
            )
        else:
            self.logger.info("FINAL Saved=0 window_end=%s window_start=%s", out_file, window_end.isoformat(), window_start.isoformat())

    # ---------------- helpers ----------------

    def _make_url(self, start: int) -> str:
        ncp_params = {
            "query": {
                "count": self.count,
                "imageSizes": "220x128",
                "documentType": "article,video,monetization",
                "start": start,
                "tag": None,
            }
        }
        ncp_encoded = quote(json.dumps(ncp_params, separators=(",", ":"), ensure_ascii=False), safe="")
        qs = "device=desktop&ecma=modern&intl=tw&lang=zh-Hant-TW&partner=none&region=TW&site=news&tz=Asia%2FTaipei&returnMeta=true"
        return f"{self.BASE}{ncp_encoded}?{qs}"

    def _parse_epoch_seconds(self, ts):
        try:
            ts = float(ts)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=self.tz)
        except Exception:
            return None

    def _find_story_items(self, data):
        def looks_like_item(x):
            return isinstance(x, dict) and "url" in x and "published_at" in x and "title" in x

        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                if cur and any(looks_like_item(x) for x in cur):
                    return [x for x in cur if looks_like_item(x)]
                stack.extend(cur)
            elif isinstance(cur, dict):
                stack.extend(cur.values())
        return []

    def _extract_author_from_article(self, response) -> str:
        for txt in response.xpath("//script[@type='application/ld+json']/text()").getall():
            txt = (txt or "").strip()
            if not txt:
                continue
            try:
                data = json.loads(txt)
            except Exception:
                continue

            for obj in (data if isinstance(data, list) else [data]):
                if not isinstance(obj, dict):
                    continue
                name = self._author_name(obj.get("author"))
                if name:
                    return name
                graph = obj.get("@graph")
                if isinstance(graph, list):
                    for node in graph:
                        if isinstance(node, dict):
                            name = self._author_name(node.get("author"))
                            if name:
                                return name

        meta_author = response.xpath(
            "normalize-space(//meta[@name='author']/@content | //meta[@property='article:author']/@content)"
        ).get()
        return (meta_author or "").strip()

    def _author_name(self, author):
        if not author:
            return None
        if isinstance(author, str):
            return author.strip() or None
        if isinstance(author, dict):
            n = author.get("name")
            return n.strip() if isinstance(n, str) and n.strip() else None
        if isinstance(author, list):
            for a in author:
                n = self._author_name(a)
                if n:
                    return n
        return None