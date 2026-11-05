"""The bulk ingest: folders and zips read into the record as notes, safely, in order, and resumably."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import record
from jobhunt.ingest import frontmatter


class Frontmatter(unittest.TestCase):
    def test_lists_and_awkward_scalars_round_trip(self):
        fields = {"title": "Report: phase 2 # draft", "tags": ["a", "b-c"], "about_owner": "true", "empty": [],
                  "period": "2023-09..2024-06"}
        parsed, body = frontmatter.parse(frontmatter.dump(fields) + "# Body\n")
        self.assertEqual(parsed, fields)
        self.assertEqual(body, "# Body\n")
        self.assertEqual(frontmatter.parse("---\ntags: [x, \"y, z\"]\n---\n")[0], {"tags": ["x", "y, z"]})

    def test_the_record_reads_a_list_of_tags_and_a_quoted_title(self):
        note = '---\ntitle: "Report: phase 2"\ntags:\n  - a\n  - b\n---\n# Report\n\n' + "Long enough text. " * 4
        self.assertEqual(record._frontmatter(note)[0]["tags"], ["a", "b"])
        self.assertEqual(record.chunks("x.md", note)[0]["title"], "Report: phase 2")
        self.assertFalse(record.should_index("---\ntype: [index]\n---\n# Start\n"))


if __name__ == "__main__":
    unittest.main()
