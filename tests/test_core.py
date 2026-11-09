import unittest

from vod_set_splitter.core import (
    ScanProfile,
    collapse_change_times,
    edit_segment_bounds,
    end_previous_at,
    format_time,
    frame_difference,
    parse_time,
    overlapping_segments,
    normalize_default_titles,
    is_default_title,
    remove_segment,
    SetSegment,
    split_segment_at,
    mark_segment_done,
    select_sample_indices,
)
from vod_set_splitter.metadata import SUBPROCESS_FLAGS, clean_ocr_text, normalize_round, parse_scoreboard_text
from vod_set_splitter.thumbnail import find_character_asset, list_characters, list_skins, parse_title


class TimeTests(unittest.TestCase):
    def test_parse_and_format_round_trip(self) -> None:
        self.assertEqual(parse_time("01:02:03"), 3723)
        self.assertEqual(format_time(3723), "01:02:03")

    def test_rejects_invalid_minute(self) -> None:
        with self.assertRaises(ValueError):
            parse_time("1:75")


class MetadataTests(unittest.TestCase):
    def test_ocr_processes_are_hidden_on_windows(self) -> None:
        import os
        import subprocess

        if os.name == "nt":
            self.assertEqual(SUBPROCESS_FLAGS, subprocess.CREATE_NO_WINDOW)

    def test_cleans_scoreboard_name(self) -> None:
        self.assertEqual(clean_ocr_text("  sloth\n"), "SLOTH")

    def test_extracts_round_without_best_of(self) -> None:
        text = "WINNERS ROUND 2\nBEST OF 3"
        self.assertEqual(normalize_round(text), "Winners Round 2")

    def test_extracts_named_bracket_rounds(self) -> None:
        cases = {
            "WINNERS QUARTERS\nBEST OF 3": "Winners Quarters",
            "LOSERS QUARTERFINALS": "Losers Quarterfinals",
            "WINNERS SEMIS": "Winners Semis",
            "LOSERS SEMIFINALS": "Losers Semifinals",
            "WINNERS FINALS": "Winners Finals",
            "LOSERS FINAL": "Losers Final",
            "GRAND FINALS": "Grand Finals",
            "GRAND FINALS RESET": "Grand Finals Reset",
            "TOP 8": "Top 8",
            "POOL A2": "Pool A2",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(normalize_round(text), expected)

    def test_corrects_common_round_ocr_errors(self) -> None:
        self.assertEqual(normalize_round("WINNFRS QUARTFRS"), "Winners Quarters")
        self.assertEqual(normalize_round("LOSFRS SFMIS"), "Losers Semis")
        self.assertEqual(normalize_round("WINNERS BANALS"), "Winners Finals")
        self.assertEqual(normalize_round("GRANDS RESET"), "Grand Finals Reset")

    def test_preserves_custom_top_line_when_not_a_standard_round(self) -> None:
        text = "PLAYER ONE\nPLAYER TWO\nSPECIAL SHOWCASE\nBEST OF 5"
        self.assertEqual(parse_scoreboard_text(text), ("PLAYER ONE", "PLAYER TWO", "Special Showcase"))

    def test_rejects_short_garbage_top_line(self) -> None:
        text = "PLAYER ONE\nPLAYER TWO\nNE COS\nBEST OF 5"
        self.assertEqual(parse_scoreboard_text(text), ("PLAYER ONE", "PLAYER TWO", None))

    def test_parses_thumbnail_title(self) -> None:
        self.assertEqual(
            parse_title("Throni vs Sloth - Winners Round 2"),
            ("Throni", "Sloth", "Winners Round 2"),
        )

    def test_real_render_pack_prefers_hd_default_and_alternate(self) -> None:
        from pathlib import Path

        root = Path(__import__("os").environ.get("VSS_RENDER_PACK", "-"))  # set VSS_RENDER_PACK to a render pack folder to run this
        if root.is_dir():
            default = find_character_asset(root, "Fox", "Default")
            alternate = find_character_asset(root, "Fox", "3")
            self.assertEqual(default.name, "Fox_Standard_1_Render.png")
            self.assertEqual(alternate.name, "Fox_Standard_3_Render.png")
            self.assertNotIn("old", default.parts)

    def test_real_render_pack_populates_dropdowns(self) -> None:
        from pathlib import Path

        root = Path(__import__("os").environ.get("VSS_RENDER_PACK", "-"))
        if root.is_dir():
            self.assertIn("Fox", list_characters(root))
            self.assertEqual(list_skins(root, "Fox"), ["Default", "Standard 2", "Standard 3", "Standard 4"])

    def test_parses_composite_scoreboard_without_best_of(self) -> None:
        text = "THRONI -\nSLOTH\nWINNERS ROUND 2\nBEST OF 3\n"
        self.assertEqual(parse_scoreboard_text(text), ("THRONI", "SLOTH", "Winners Round 2"))


class BoundaryEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [SetSegment(0, 100, "Set 1"), SetSegment(100, 200, "Set 2")]

    def test_allows_gap_without_moving_neighbor(self) -> None:
        updated = edit_segment_bounds(self.segments, 0, 0, 90, adjust_neighbors=False)
        self.assertEqual((updated[0].end, updated[1].start), (90, 100))

    def test_moves_neighbor_when_end_would_overlap(self) -> None:
        updated = edit_segment_bounds(self.segments, 0, 0, 110)
        self.assertEqual((updated[0].end, updated[1].start), (110, 110))
        self.assertEqual(overlapping_segments(updated), [])

    def test_linked_boundary_moves_neighbor_earlier_too(self) -> None:
        updated = edit_segment_bounds(self.segments, 0, 0, 90)
        self.assertEqual((updated[0].end, updated[1].start), (90, 100))

    def test_unchanged_boundary_does_not_close_existing_gap(self) -> None:
        segments = [SetSegment(0, 90, "Set 1"), SetSegment(100, 200, "Set 2")]
        updated = edit_segment_bounds(segments, 0, 0, 90)
        self.assertEqual(updated[1].start, 100)

    def test_done_set_cannot_be_changed(self) -> None:
        segments = [SetSegment(0, 90, "Set 1", locked=True), SetSegment(100, 200, "Set 2")]
        with self.assertRaisesRegex(ValueError, "is done"):
            edit_segment_bounds(segments, 0, 0, 80, adjust_neighbors=False)

    def test_done_neighbor_cannot_be_changed_by_overlap(self) -> None:
        segments = [SetSegment(0, 100, "Set 1"), SetSegment(100, 200, "Set 2", locked=True)]
        with self.assertRaisesRegex(ValueError, "overlaps done set Set 2"):
            edit_segment_bounds(segments, 0, 0, 110)

    def test_independent_edit_preserves_dead_time_gap(self) -> None:
        updated = edit_segment_bounds(self.segments, 0, 0, 90, adjust_neighbors=False)
        self.assertEqual((updated[0].end, updated[1].start), (90, 100))

    def test_end_previous_resolves_overlap_at_playhead(self) -> None:
        segments = [SetSegment(0, 130, "Set 1"), SetSegment(100, 200, "Set 2")]
        updated = end_previous_at(segments, 1, 120)
        self.assertEqual((updated[0].end, updated[1].start), (120, 120))

    def test_end_previous_preserves_existing_dead_time(self) -> None:
        segments = [SetSegment(0, 90, "Set 1"), SetSegment(120, 200, "Set 2")]
        updated = end_previous_at(segments, 1, 80)
        self.assertEqual((updated[0].end, updated[1].start), (80, 120))

    def test_end_previous_appends_uncovered_video_after_last_set(self) -> None:
        segments = [SetSegment(0, 80, "Set 1"), SetSegment(80, 150, "Set 2")]
        updated = end_previous_at(segments, 1, 100, video_duration=200)
        self.assertEqual(
            [(item.start, item.end, item.title) for item in updated],
            [(0, 100, "Set 1"), (100, 150, "Set 2"), (150, 200, "Set 00")],
        )

    def test_end_previous_does_not_append_when_last_set_reaches_video_end(self) -> None:
        segments = [SetSegment(0, 80, "Set 1"), SetSegment(80, 200, "Set 2")]
        updated = end_previous_at(segments, 1, 100, video_duration=200)
        self.assertEqual(len(updated), 2)

    def test_delete_preserves_neighbor_timestamps(self) -> None:
        segments = [
            SetSegment(0, 90, "Set 1"),
            SetSegment(90, 120, "Empty"),
            SetSegment(140, 200, "Set 2"),
        ]
        updated = remove_segment(segments, 1)
        self.assertEqual([(item.start, item.end) for item in updated], [(0, 90), (140, 200)])
        self.assertEqual([item.title for item in updated], ["Set 01", "Set 02"])

    def test_delete_preserves_custom_titles(self) -> None:
        segments = [SetSegment(0, 90, "Mango vs Zain"), SetSegment(90, 120, "Empty")]
        updated = remove_segment(segments, 1)
        self.assertEqual(updated[0].title, "Mango vs Zain")

    def test_project_load_numbering_repair_preserves_custom_names(self) -> None:
        segments = [SetSegment(0, 90, "Set 01"), SetSegment(100, 200, "Set 06"), SetSegment(210, 300, "Finals")]
        updated = normalize_default_titles(segments)
        self.assertEqual([item.title for item in updated], ["Set 01", "Set 02", "Finals"])

    def test_identifies_only_automatic_titles(self) -> None:
        self.assertTrue(is_default_title("Set 06"))
        self.assertFalse(is_default_title("Throne vs Sloth - Winners Round 2"))

    def test_done_set_cannot_be_deleted(self) -> None:
        with self.assertRaisesRegex(ValueError, "is done"):
            remove_segment([SetSegment(0, 90, "Set 1", locked=True)], 0)

    def test_split_preserves_other_set_metadata(self) -> None:
        segments = [
            SetSegment(0, 100, "Custom", character_one="Fox", skin_one="Standard 3"),
            SetSegment(120, 200, "Finals", locked=True, character_two="Marth"),
        ]
        updated, index = split_segment_at(segments, 50)
        self.assertEqual(index, 1)
        self.assertEqual((updated[0].start, updated[0].end, updated[0].title), (0, 50, "Custom"))
        self.assertEqual((updated[1].start, updated[1].end, updated[1].title), (50, 100, "Set 02"))
        self.assertEqual((updated[2].start, updated[2].end, updated[2].title), (120, 200, "Finals"))
        self.assertTrue(updated[2].locked)
        self.assertEqual(updated[2].character_two, "Marth")

    def test_done_advances_next_set_by_one_second(self) -> None:
        segments = [SetSegment(0, 90, "Set 1"), SetSegment(80, 200, "Set 2")]
        updated = mark_segment_done(segments, 0)
        self.assertTrue(updated[0].locked)
        self.assertEqual(updated[1].start, 91)

    def test_done_does_not_move_locked_next_set(self) -> None:
        segments = [SetSegment(0, 90, "Set 1"), SetSegment(100, 200, "Set 2", locked=True)]
        updated = mark_segment_done(segments, 0)
        self.assertEqual(updated[1].start, 100)

    def test_rejects_overlap_when_neighbor_adjustment_is_off(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlaps Set 2"):
            edit_segment_bounds(self.segments, 0, 0, 110, adjust_neighbors=False)


class DetectionTests(unittest.TestCase):
    def test_default_threshold_separates_calibrated_changes(self) -> None:
        profile = ScanProfile()
        self.assertGreater(0.12, profile.change_threshold)
        self.assertGreater(profile.change_threshold, 0.016)

    def test_selects_keyframes_at_requested_interval(self) -> None:
        timestamps = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
        self.assertEqual(select_sample_indices(timestamps, 5.0), {0, 3, 6})

    def test_frame_difference_is_changed_pixel_ratio(self) -> None:
        self.assertEqual(frame_difference(bytes([0, 0, 0, 0]), bytes([0, 24, 23, 255])), 0.5)

    def test_change_bursts_become_spaced_boundaries(self) -> None:
        changes = [100, 105, 110, 500, 505, 650, 900]
        self.assertEqual(collapse_change_times(changes, 30, 300), [105, 505, 900])


if __name__ == "__main__":
    unittest.main()