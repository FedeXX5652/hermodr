"""SQLite: notes, stories, editions, summaries and what I do with them."""
import sqlite3
from os import makedirs
from os.path import join

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    section     TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    lead        TEXT NOT NULL DEFAULT '',      -- what the feed brings
    text        TEXT NOT NULL DEFAULT '',      -- full article, when it could be extracted
    image_url   TEXT,
    published   TEXT NOT NULL,                 -- iso, utc
    fetched     TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    frontpage   INTEGER NOT NULL DEFAULT 0,
    copy_of     INTEGER REFERENCES notes(id),  -- wire copy of an earlier note
    story_id    INTEGER REFERENCES stories(id)
);
CREATE INDEX IF NOT EXISTS notes_published ON notes(published);
CREATE INDEX IF NOT EXISTS notes_story ON notes(story_id);

CREATE TABLE IF NOT EXISTS stories (
    id       INTEGER PRIMARY KEY,
    created  TEXT NOT NULL,
    updated  TEXT NOT NULL                     -- published time of its newest note
);

CREATE TABLE IF NOT EXISTS editions (
    id            INTEGER PRIMARY KEY,
    created       TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    model         TEXT NOT NULL,               -- provenance of the summaries, or 'no-model'
    path          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edition_items (
    edition_id  INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
    story_id    INTEGER NOT NULL REFERENCES stories(id),
    section     TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    score       REAL NOT NULL,
    why         TEXT NOT NULL,
    notes_seen  INTEGER NOT NULL,              -- how many notes the story had when shown
    updated     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (edition_id, story_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    key        TEXT PRIMARY KEY,               -- hash of the notes + model + prompt version
    story_id   INTEGER NOT NULL,
    model      TEXT NOT NULL,
    params     TEXT NOT NULL,                  -- json: everything that produced it
    body       TEXT NOT NULL,                  -- json: sentences with their sources
    verified   INTEGER NOT NULL,
    created    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    edition_id  INTEGER NOT NULL,
    story_id    INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('click', 'useful', 'not_interested')),
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_status (
    url        TEXT PRIMARY KEY,
    last_ok    TEXT,
    last_error TEXT,
    error      TEXT
);
"""


def connect(data_dir):
    makedirs(data_dir, exist_ok=True)
    con = sqlite3.connect(join(data_dir, "hermodr.db"))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    return con
