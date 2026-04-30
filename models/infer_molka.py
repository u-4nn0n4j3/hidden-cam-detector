import argparse
from pathlib import Path

from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YOLOv8n molka inference on a single image."
    )
    parser.add_argument("image", type=str, help="Input image path")
    parser.add_argument(
        "--model",
        type=str,
        default="best.pt",
        help="Path to YOLO model (default: best.pt in current folder)",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.5,
        help="Confidence threshold (default: 0.5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output image path (default: <input>_detected.jpg)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    model_path = Path(args.model)

    if not image_path.exists():
        raise FileNotFoundError(f"Input image not found: {image_path}")
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    model = YOLO(str(model_path))
    results = model.predict(source=str(image_path), conf=args.conf, verbose=False)
    result = results[0]

    molka_cls_id = None
    for cls_id, name in result.names.items():
        if name == "molka":
            molka_cls_id = int(cls_id)
            break

    molka_count = 0
    molka_scores = []

    if result.boxes is not None and len(result.boxes) > 0 and molka_cls_id is not None:
        for box in result.boxes:
            cls_id = int(box.cls.item())
            conf = float(box.conf.item())
            if cls_id == molka_cls_id:
                molka_count += 1
                molka_scores.append(conf)

    if molka_count > 0:
        print(f"Detected molka: {molka_count}")
        print(
            "Confidence scores: "
            + ", ".join(f"{score:.4f}" for score in molka_scores)
        )
    else:
        print("No molka detected.")

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = image_path.with_name(f"{image_path.stem}_detected.jpg")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(filename=str(output_path))
    print(f"Saved result image: {output_path}")


if __name__ == "__main__":
    main()
