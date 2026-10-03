import struct
import unittest

from tiktok_lossless_patch import build_box, fix_chunk_offsets


class ChunkOffsetTests(unittest.TestCase):
    def test_offsets_inside_media_container(self):
        for box_type, fmt in ((b'stco', '>I'), (b'co64', '>Q')):
            for delta in (128, -128):
                with self.subTest(box_type=box_type, delta=delta):
                    offsets = (0, 1024, 4096)
                    table = build_box(
                        box_type,
                        struct.pack('>II', 0, len(offsets))
                        + b''.join(struct.pack(fmt, value) for value in offsets),
                    )
                    data = table
                    for container in (b'stbl', b'minf', b'mdia', b'trak', b'moov'):
                        data = build_box(container, data)
                    data = bytearray(data)
                    expected = bytearray(data)
                    entries_start = data.index(box_type) + 12
                    for index, value in enumerate(offsets):
                        struct.pack_into(
                            fmt, expected,
                            entries_start + index * struct.calcsize(fmt),
                            value + delta if value else 0,
                        )

                    fix_chunk_offsets(data, delta)

                    self.assertEqual(data, expected)


if __name__ == '__main__':
    unittest.main()
