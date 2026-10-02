#!/usr/bin/env python3
"""Share (0..1) of sampled pixels that differ between two PPM screenshots in
the left third of the screen, where the system menu panel is drawn.

    screendiff.py a.ppm b.ppm --more 0.5     exit 0 if the share is above 0.5
    screendiff.py a.ppm b.ppm --less 0.1     exit 0 if it is below 0.1
"""
import sys


def pixels(path):
    with open(path, "rb") as f:
        if f.readline().strip() != b"P6":
            raise SystemExit(f"{path}: not a binary PPM")
        width, height = map(int, f.readline().split())
        f.readline()
        return width, height, f.read()


def main(a_path, b_path, op, limit):
    w, h, a = pixels(a_path)
    w2, h2, b = pixels(b_path)
    if (w, h) != (w2, h2):
        raise SystemExit("screenshots differ in size")
    points = [(y * w + x) * 3 for y in range(0, h, 8) for x in range(0, w // 3, 8)]
    share = sum(a[p:p + 3] != b[p:p + 3] for p in points) / len(points)
    print(f"{share:.2f}")
    return 0 if (share > float(limit) if op == "--more" else share < float(limit)) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
