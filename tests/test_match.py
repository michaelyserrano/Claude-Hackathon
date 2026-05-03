import json
import tempfile
import unittest
from pathlib import Path

from ingest import db
from ingest import match


class MatchPureFunctionTest(unittest.TestCase):
    def test_cosine_handles_normal_zero_and_mismatched_vectors(self):
        self.assertAlmostEqual(match.cosine([1, 0], [1, 0]), 1.0)
        self.assertEqual(match.cosine([0, 0], [1, 0]), 0.0)
        self.assertEqual(match.cosine([1, 0], [1]), 0.0)

    def test_topics_overlap_normalizes_case_and_whitespace(self):
        self.assertTrue(match.topics_overlap([" Public Safety "], ["public safety"]))
        self.assertFalse(match.topics_overlap(["Housing"], ["Transit"]))

    def test_compute_matches_uses_topic_prefilter_and_threshold(self):
        item = match.AgendaItem(
            id="A1",
            topics=("Housing",),
            embedding=(1.0, 0.0),
        )
        housing_signal = match.Signal(
            id="p1",
            signal_type="petition",
            topics=("housing",),
            embedding=(1.0, 0.0),
            weight=100,
        )
        wrong_topic_signal = match.Signal(
            id="r1",
            signal_type="reddit",
            topics=("Transit",),
            embedding=(1.0, 0.0),
            weight=50,
        )
        low_similarity_signal = match.Signal(
            id="p2",
            signal_type="petition",
            topics=("Housing",),
            embedding=(0.0, 1.0),
            weight=100,
        )

        matches = match.compute_matches(
            [item],
            [housing_signal, wrong_topic_signal, low_similarity_signal],
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].signal_id, "p1")
        self.assertEqual(matches[0].signal_type, "petition")

    def test_compute_buzz_weights_counts_top_signal_and_normalizes(self):
        signals = [
            match.Signal(
                id="p1",
                signal_type="petition",
                topics=("Housing",),
                embedding=(1.0, 0.0),
                weight=100,
            ),
            match.Signal(
                id="r1",
                signal_type="reddit",
                topics=("Housing",),
                embedding=(1.0, 0.0),
                weight=10,
            ),
        ]
        matches = [
            match.Match("A1", "petition", "p1", 0.8),
            match.Match("A1", "reddit", "r1", 0.5),
        ]

        scores = match.compute_buzz(["A1", "A2"], matches, signals)

        self.assertAlmostEqual(scores["A1"].raw_score, 105.0)
        self.assertEqual(scores["A1"].petition_count, 1)
        self.assertEqual(scores["A1"].reddit_count, 1)
        self.assertEqual(scores["A1"].top_signal_id, "petition:p1")
        self.assertAlmostEqual(scores["A1"].score, 100.0)
        self.assertEqual(scores["A2"].score, 0.0)
        self.assertIsNone(scores["A2"].top_signal_id)

    def test_compute_buzz_handles_all_zero_raw_scores(self):
        scores = match.compute_buzz(["A1"], [], [])

        self.assertEqual(scores["A1"].score, 0.0)
        self.assertEqual(scores["A1"].petition_count, 0)
        self.assertEqual(scores["A1"].reddit_count, 0)


class MatchDbIntegrationTest(unittest.TestCase):
    def test_main_replaces_stale_rows_and_writes_zero_buzz_items(self):
        original_db_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db.DB_PATH = Path(tmpdir) / "civic.db"
                db.init_schema()
                conn = db.connect()
                try:
                    conn.execute(
                        """
                        INSERT INTO agenda_items (
                            id, meeting_date, title, raw_text, summary, topics,
                            stage, dollar_amount, sponsors, embedding
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "A1",
                            "2026-05-01",
                            "Housing item",
                            "raw",
                            "summary",
                            json.dumps(["Housing"]),
                            "Upcoming",
                            None,
                            json.dumps([]),
                            json.dumps([1.0, 0.0]),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO agenda_items (
                            id, meeting_date, title, raw_text, summary, topics,
                            stage, dollar_amount, sponsors, embedding
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "A2",
                            "2026-05-02",
                            "Unenriched item",
                            "raw",
                            None,
                            None,
                            "Upcoming",
                            None,
                            json.dumps([]),
                            None,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO petitions (
                            id, url, title, description, signature_count,
                            topics, embedding, scraped_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "p1",
                            "https://www.change.org/p/p1",
                            "Petition",
                            "description",
                            100,
                            json.dumps(["Housing"]),
                            json.dumps([1.0, 0.0]),
                            "2026-05-03T00:00:00+00:00",
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO matches
                          (agenda_item_id, signal_type, signal_id, similarity)
                        VALUES (?, ?, ?, ?)
                        """,
                        ("stale", "petition", "old", 1.0),
                    )
                    conn.execute(
                        """
                        INSERT INTO buzz_scores
                          (agenda_item_id, score, petition_count, reddit_count, top_signal_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        ("stale", 99.0, 1, 0, "petition:old"),
                    )
                    conn.commit()
                finally:
                    conn.close()

                match.main()

                conn = db.connect()
                try:
                    matches = conn.execute(
                        "SELECT * FROM matches ORDER BY agenda_item_id, signal_id"
                    ).fetchall()
                    buzz_rows = conn.execute(
                        "SELECT * FROM buzz_scores ORDER BY agenda_item_id"
                    ).fetchall()
                finally:
                    conn.close()

                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["agenda_item_id"], "A1")
                self.assertEqual(matches[0]["signal_id"], "p1")

                self.assertEqual([row["agenda_item_id"] for row in buzz_rows], ["A1", "A2"])
                self.assertAlmostEqual(buzz_rows[0]["score"], 100.0)
                self.assertEqual(buzz_rows[0]["petition_count"], 1)
                self.assertEqual(buzz_rows[0]["reddit_count"], 0)
                self.assertEqual(buzz_rows[0]["top_signal_id"], "petition:p1")
                self.assertEqual(buzz_rows[1]["score"], 0.0)
                self.assertIsNone(buzz_rows[1]["top_signal_id"])
        finally:
            db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
