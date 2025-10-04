import asyncio
import logging
import random
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

import sqlalchemy
import uvicorn
from databases import Database
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .fetcher import fetch_rss
from .models import articles, metadata

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@lru_cache
def get_settings() -> config.Settings:
    return config.Settings()  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db = Database(settings.DB_URL)
    app.state.db = db

    await db.connect()
    engine = sqlalchemy.create_engine(settings.DB_URL)
    metadata.create_all(engine)

    await fetch_rss(
        db=db,
        rss_url=settings.DONOR_RSS_URL,
        max_articles=settings.MAX_ARTICLES,
        user_agent=settings.USER_AGENT,
    )
    asyncio.create_task(schedule_fetch(app))

    yield

    await db.disconnect()


async def get_db(request: Request) -> Database:
    return request.app.state.db


async def schedule_fetch(app: FastAPI):
    db: Database = app.state.db
    settings = get_settings()

    base_interval = settings.FETCH_INTERVAL_MIN * 60
    backoff = 1
    max_backoff = 32

    while True:
        try:
            logger.info("Fetching RSS from %s", settings.DONOR_RSS_URL)
            await fetch_rss(
                db=db,
                rss_url=settings.DONOR_RSS_URL,
                max_articles=10,
                user_agent=settings.USER_AGENT,
            )
            logger.info("RSS fetch completed successfully")
            backoff = 1
        except Exception as e:
            logger.exception("Error while fetching RSS: %s", e)
            backoff = min(backoff * 2, max_backoff)
            logger.warning("Retrying with backoff factor %s", backoff)

        delay = base_interval * backoff
        jitter = random.uniform(0.8, 1.2)
        delay *= jitter

        logger.info(
            "Sleeping for %.1f seconds (backoff=%s, jitter=%.2f)",
            delay,
            backoff,
            jitter,
        )

        await asyncio.sleep(delay)


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index(
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[config.Settings, Depends(get_settings)],
    page: int = 1,
):
    count_query = sqlalchemy.select(sqlalchemy.func.count()).select_from(articles)
    total_articles = await db.fetch_val(count_query)

    offset = (page - 1) * settings.ARTICLES_PER_PAGE

    query = (
        articles.select()
        .order_by(articles.c.pub_date.desc())
        .limit(settings.ARTICLES_PER_PAGE)
        .offset(offset)
    )
    rows = await db.fetch_all(query)

    total_pages = (
        total_articles + settings.ARTICLES_PER_PAGE - 1
    ) // settings.ARTICLES_PER_PAGE

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "articles": rows,
            "ga_measurement_id": settings.GA_MEASUREMENT_ID,
            "current_page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/news/{article_id}")
async def news_detail(
    article_id: int,
    request: Request,
    db: Annotated[Database, Depends(get_db)],
):
    query = articles.select().where(articles.c.id == article_id)
    article = await db.fetch_one(query)
    if not article:
        return {"error": "Article not found"}

    return templates.TemplateResponse(
        "news_detail.html", {"request": request, "article": article}
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
