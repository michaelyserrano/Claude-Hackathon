import tempfile
import unittest
from pathlib import Path

from ingest import db
from ingest.scrape_reddit import (
    mentions_cambridge,
    qualifies,
    submission_to_post,
    top_comments_text,
)


class FakeComment:
    def __init__(self, body, score=0, stickied=False):
        self.body = body
        self.score = score
        self.stickied = stickied


class FakeComments(list):
    def replace_more(self, limit=0):
        return None

    def list(self):
        return self


class FakeSubmission:
    def __init__(
        self,
        *,
        post_id="abc123",
        title="Cambridge bike lane changes",
        selftext="A local discussion",
        score=10,
        num_comments=5,
        comments=None,
        created_utc=1_700_000_000,
    ):
        self.id = post_id
        self.title = title
        self.selftext = selftext
        self.score = score
        self.num_comments = num_comments
        self.comments = FakeComments(comments or [])
        self.created_utc = created_utc
        self.permalink = f"/r/Cambridge/comments/{post_id}/slug/"
        self.subreddit = "Cambridge"


class RedditScraperTest(unittest.TestCase):
    def test_mentions_cambridge_uses_title_and_body(self):
        self.assertTrue(mentions_cambridge(FakeSubmission(title="Cambridge traffic")))
        self.assertTrue(
            mentions_cambridge(
                FakeSubmission(title="Question", selftext="Does Cambridge, MA allow this?")
            )
        )
        self.assertFalse(
            mentions_cambridge(
                FakeSubmission(title="Somerville traffic", selftext="Davis Square")
            )
        )

    def test_qualifies_by_score_or_comment_count(self):
        self.assertTrue(qualifies(FakeSubmission(score=10, num_comments=0)))
        self.assertTrue(qualifies(FakeSubmission(score=0, num_comments=5)))
        self.assertFalse(qualifies(FakeSubmission(score=9, num_comments=4)))

    def test_top_comments_text_sorts_and_skips_removed_content(self):
        submission = FakeSubmission(
            comments=[
                FakeComment("low score", score=1),
                FakeComment("[deleted]", score=99),
                FakeComment("top comment", score=10),
                FakeComment("stickied", score=50, stickied=True),
                FakeComment("second comment", score=5),
            ]
        )

        self.assertEqual(
            top_comments_text(submission, n=2),
            "top comment\n---\nsecond comment",
        )

    def test_submission_to_post_normalizes_contract(self):
        submission = FakeSubmission(
            post_id="xyz",
            selftext="[removed]",
            comments=[FakeComment("useful comment", score=3)],
        )

        post = submission_to_post(submission)

        self.assertEqual(post["id"], "xyz")
        self.assertEqual(post["url"], "https://www.reddit.com/r/Cambridge/comments/xyz/slug/")
        self.assertEqual(post["body"], "useful comment")
        self.assertEqual(post["created_at"], "2023-11-14T22:13:20+00:00")


class RedditDbTest(unittest.TestCase):
    def test_upsert_reddit_post_preserves_enrichment_fields(self):
        original_db_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db.DB_PATH = Path(tmpdir) / "civic.db"
                db.init_schema()

                first_post = {
                    "id": "abc123",
                    "url": "https://www.reddit.com/r/Cambridge/comments/abc123/slug/",
                    "subreddit": "Cambridge",
                    "title": "Original title",
                    "body": "Original body",
                    "score": 10,
                    "comment_count": 5,
                    "created_at": "2026-05-01T00:00:00+00:00",
                }
                updated_post = {
                    **first_post,
                    "title": "Updated title",
                    "body": "Updated body",
                    "score": 20,
                    "comment_count": 10,
                }

                conn = db.connect()
                try:
                    db.upsert_reddit_post(conn, first_post)
                    conn.execute(
                        """
                        UPDATE reddit_posts
                        SET topics = ?, embedding = ?
                        WHERE id = ?
                        """,
                        ('["Transit"]', "[0.1, 0.2]", "abc123"),
                    )
                    db.upsert_reddit_post(conn, updated_post)
                    row = conn.execute(
                        "SELECT * FROM reddit_posts WHERE id = ?", ("abc123",)
                    ).fetchone()
                finally:
                    conn.close()

                self.assertEqual(row["title"], "Updated title")
                self.assertEqual(row["body"], "Updated body")
                self.assertEqual(row["score"], 20)
                self.assertEqual(row["comment_count"], 10)
                self.assertEqual(row["topics"], '["Transit"]')
                self.assertEqual(row["embedding"], "[0.1, 0.2]")
        finally:
            db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
