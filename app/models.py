from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()

articles = Table(
    "articles",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(255)),
    Column("link", String(500), unique=True),
    Column("pub_date", DateTime),
    Column("summary", Text),
    Column("content", Text),
    Column("image_url", String(500)),
    Column("fetched_at", DateTime),
)
