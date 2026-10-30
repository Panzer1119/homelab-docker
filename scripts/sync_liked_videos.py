import os
import sqlite3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
DATABASE_FILE = "youtube.db"

PLAYLIST_ID = "LL"


def authenticate():
    credentials = None

    if os.path.exists(TOKEN_FILE) and os.path.getsize(TOKEN_FILE) > 0:
        credentials = Credentials.from_authorized_user_file(
            TOKEN_FILE, SCOPES
        )

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(credentials.to_json())

    return build("youtube", "v3", credentials=credentials)


def create_database():
    connection = sqlite3.connect(DATABASE_FILE)

    connection.execute("""
                       CREATE TABLE IF NOT EXISTS video
                       (
                           video_id                  TEXT PRIMARY KEY,
                           published_at              TEXT,
                           liked_at                  TEXT,
                           title                     TEXT,
                           description               TEXT,
                           video_owner_channel_id    TEXT,
                           video_owner_channel_title TEXT,
                           privacy_status            TEXT,
                           playlist_item_id          TEXT
                       )
                       """)

    connection.commit()
    return connection


def video_exists(connection, video_id):
    return connection.execute(
        "SELECT 1 FROM video WHERE video_id = ?",
        (video_id,)
    ).fetchone() is not None


def playlist_item_exists(connection, playlist_item_id):
    return connection.execute(
        "SELECT 1 FROM video WHERE playlist_item_id = ?",
        (playlist_item_id,)
    ).fetchone() is not None


def save_videos(connection, items):
    videos = []

    for item in items:
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        content_details = item.get("contentDetails", {})

        video_id = content_details.get("videoId")
        published_at = content_details.get("videoPublishedAt")
        liked_at = snippet.get("publishedAt")

        if not video_id:
            continue

        videos.append((
            video_id,
            published_at,
            liked_at,
            snippet.get("title"),
            snippet.get("description"),
            snippet.get("videoOwnerChannelId"),
            snippet.get("videoOwnerChannelTitle"),
            status.get("privacyStatus"),
            item.get("id"),
        ))

    connection.executemany("""
                           INSERT INTO video (video_id,
                                              published_at,
                                              liked_at,
                                              title,
                                              description,
                                              video_owner_channel_id,
                                              video_owner_channel_title,
                                              privacy_status,
                                              playlist_item_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(video_id) DO UPDATE SET published_at              = COALESCE(NULLIF(excluded.published_at, ''), published_at),
                                                               liked_at                  = COALESCE(NULLIF(excluded.liked_at, ''), liked_at),
                                                               title                     = COALESCE(NULLIF(excluded.title, ''), title),
                                                               description               = COALESCE(NULLIF(excluded.description, ''), description),
                                                               video_owner_channel_id    = COALESCE(
                                                                       NULLIF(excluded.video_owner_channel_id, ''),
                                                                       video_owner_channel_id),
                                                               video_owner_channel_title = COALESCE(
                                                                       NULLIF(excluded.video_owner_channel_title, ''),
                                                                       video_owner_channel_title),
                                                               privacy_status            = excluded.privacy_status,
                                                               playlist_item_id          = COALESCE(NULLIF(excluded.playlist_item_id, ''), playlist_item_id)
                           """, videos)

    connection.commit()

    return len(videos)


def sync_liked_videos(youtube, connection):
    page_token = None
    total = 0

    while True:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails,status",
            playlistId=PLAYLIST_ID,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        items = response.get("items", [])
        new_items = []

        for item in items:
            video_id = item.get("contentDetails", {}).get("videoId")
            playlist_item_id = item.get("id")

            #if video_id and video_exists(connection, video_id):
            if playlist_item_exists(connection, playlist_item_id):
                print(f"Reached existing video: {video_id}")
                break

            new_items.append(item)

        count = save_videos(connection, new_items)
        total += count

        print(f"Saved {total} new videos")

        # Stop if we encountered an existing video
        if len(new_items) < len(items):
            break

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    print(f"\nFinished! Added {total} new videos.")


def main():
    youtube = authenticate()
    connection = create_database()

    try:
        sync_liked_videos(youtube, connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
