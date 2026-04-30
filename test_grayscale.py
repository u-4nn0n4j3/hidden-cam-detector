import argparse

import cv2

from detection_engine import DetectionEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input image path")
    parser.add_argument("--output", required=True, help="Output image path")
    args = parser.parse_args()

    img = cv2.imread(args.input)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {args.input}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    engine = DetectionEngine(model_path=None)
    lenses = engine.detect_lenses(img)

    vis = img.copy()
    for cx, cy, r in lenses:
        cv2.circle(vis, (cx, cy), r, (0, 255, 0), 2)
        cv2.circle(vis, (cx, cy), 3, (0, 0, 255), -1)

    ok = cv2.imwrite(args.output, vis, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError(f"Failed to save output: {args.output}")

    print(f"{args.input} -> {args.output}: points={len(lenses)}")


if __name__ == "__main__":
    main()
