import asyncio
import logging
from datetime import datetime, timezone
from time import struct_time
from typing import Any, List, Optional

import feedparser
import httpx
from databases import Database
from newspaper import Article
from sqlalchemy import insert, select, update

from .models import articles

logger = logging.getLogger(__name__)


async def fetch_full_content(db: Database, article_id: int, url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.exception("Failed to fetch article page %s: %s", url, e)
        return None

    try:
        news_article = Article(url)
        news_article.set_html(resp.text)
        news_article.parse()
        full_text = news_article.text
    except Exception as e:
        logger.exception("Failed to parse article %s: %s", url, e)
        return None

    try:
        query = (
            update(articles)
            .where(articles.c.id == article_id)
            .values(content=full_text, fetched_at=datetime.now(tz=timezone.utc))
        )
        await db.execute(query)
        logger.info("Full content saved for article %s", article_id)
        return full_text
    except Exception as e:
        logger.exception(
            "Failed to update full content for article %s: %s", article_id, e
        )
        return None


async def fetch_rss(
    db: Database,
    rss_url: str,
    max_articles: int = 20,
    user_agent: str = "RSSFetcher/1.0",
) -> List[str]:
    headers = {"User-Agent": user_agent}
    new_articles: List[str] = []

    try:
        async with httpx.AsyncClient(headers=headers, timeout=15) as client:
            resp = await client.get(rss_url)
            resp.raise_for_status()
    except Exception as e:
        logger.exception("Failed to fetch RSS from %s: %s", rss_url, e)
        return new_articles

    feed = feedparser.parse(resp.text)
    if feed.bozo:
        logger.warning("RSS parse error for %s (bozo=%s)", rss_url, feed.bozo_exception)
        return new_articles

    tasks = []

    for entry in feed.entries[:max_articles]:
        link: Optional[str] = getattr(entry, "link", None)
        title: str = getattr(entry, "title", "Untitled")
        if not link:
            logger.debug("Skipping entry without link: %s", entry)
            continue

        # Check for duplicates
        query = select(articles.c.id).where(articles.c.link == link)
        exists = await db.fetch_one(query)
        if exists:
            continue

        # Publication date
        pub_date: datetime
        published_parsed: Optional[Any] = getattr(
            entry, "published_parsed", None
        ) or getattr(entry, "updated_parsed", None)
        if isinstance(published_parsed, struct_time):
            pub_date = datetime(
                published_parsed.tm_year,
                published_parsed.tm_mon,
                published_parsed.tm_mday,
                published_parsed.tm_hour,
                published_parsed.tm_min,
                published_parsed.tm_sec,
            )
        else:
            pub_date = datetime.now(tz=timezone.utc)

        # Image URL
        image_url: Optional[str] = None
        enclosures = getattr(entry, "enclosures", None)
        if isinstance(enclosures, list) and len(enclosures) > 0:
            first = enclosures[0]
            if isinstance(first, dict):
                image_url = first.get("href")

        # Summary / description
        summary: str = getattr(entry, "summary", "") or getattr(
            entry, "description", ""
        )

        # Insert into database
        ins = insert(articles).values(
            title=title,
            link=link,
            pub_date=pub_date,
            summary=summary,
            content="",
            image_url=image_url,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        try:
            article_id = await db.execute(ins)
            new_articles.append(title)
            logger.info("Added article: %s", title)

            tasks.append(fetch_full_content(db, article_id, link))
        except Exception as e:
            logger.exception("Failed to insert article '%s': %s", title, e)

    if tasks:
        await asyncio.gather(*tasks)

    logger.info("Total new articles added: %s", len(new_articles))
    return new_articles
