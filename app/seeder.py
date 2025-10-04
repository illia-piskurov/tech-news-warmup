import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List

import httpx
from databases import Database
from newspaper import Article
from sqlalchemy import create_engine, select

from . import config
from .models import articles, metadata

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def parse_sitemap_urls(settings: config.Settings) -> List[str]:
    urls: List[str] = []

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.USER_AGENT}, timeout=30
        ) as client:
            logger.info("Fetching sitemap from %s", settings.DONOR_SITEMAP_URL)
            resp = await client.get(settings.DONOR_SITEMAP_URL)
            resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch sitemap %s: %s", settings.DONOR_SITEMAP_URL, e)
        return urls

    try:
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        root = ET.fromstring(resp.text)

        if not root.tag.endswith("urlset"):
            logger.warning("File is not a standard <urlset> sitemap. Skipping parsing.")
            return urls

        for url_element in root.findall("s:url/s:loc", namespace):
            url = url_element.text

            if url and url.startswith(settings.TARGET_PATH_PREFIX):

                suffix = url[len(settings.TARGET_PATH_PREFIX) :].strip()

                if suffix:
                    urls.append(url)

                    if len(urls) >= settings.MAX_ARTICLES_TO_SEED:
                        logger.info(
                            "Reached maximum article limit (%d). Stopping sitemap parsing.",
                            settings.MAX_ARTICLES_TO_SEED,
                        )
                        return urls

    except ET.ParseError as e:
        logger.error("Failed to parse sitemap XML: %s", e)
    except Exception as e:
        logger.error("Error during sitemap parsing: %s", e)

    return urls


async def seed_articles(db: Database, settings: config.Settings):
    article_links = await parse_sitemap_urls(settings)

    if not article_links:
        logger.warning("No article links found in sitemap. Seeding stopped.")
        return

    logger.info("Processing %d articles for seeding.", len(article_links))

    for link in article_links:
        query = select(articles.c.id).where(articles.c.link == link)
        exists = await db.fetch_one(query)
        if exists:
            logger.debug("Skipping existing article: %s", link)
            continue

        try:
            news_article = Article(link)
            news_article.download()
            news_article.parse()

            title = news_article.title if news_article.title else "Untitled"
            full_content = news_article.text if news_article.text else ""
            summary = news_article.summary if news_article.summary else ""

            if not summary and full_content:
                summary = full_content[:200].strip()

            if len(full_content) > 200:
                summary += "..."

            image_url = news_article.top_img if news_article.top_img else None
            pub_date = (
                news_article.publish_date
                if news_article.publish_date
                else datetime.now(tz=timezone.utc)
            )

            ins = articles.insert().values(
                title=title,
                link=link,
                pub_date=pub_date,
                summary=summary,
                content=full_content,
                image_url=image_url,
                fetched_at=datetime.now(tz=timezone.utc),
            )
            await db.execute(ins)
            logger.info("Seeded article: %s", title)

        except Exception as e:
            logger.warning("Failed to process and seed article %s: %s", link, e)

    logger.info("Seeding process finished.")


async def main():
    from functools import lru_cache

    @lru_cache
    def get_settings():
        return config.Settings()  # type: ignore

    settings = get_settings()

    db = Database(settings.DB_URL)
    await db.connect()

    engine = create_engine(settings.DB_URL)
    metadata.create_all(engine)

    try:
        await seed_articles(db, settings)
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
