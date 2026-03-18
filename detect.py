#!/usr/bin/env python3
"""
hailo_yolov8/detect.py

Extends the rpicam YOLOv8 Hailo-8L example.
- Captures detections in-memory
- Filters objects that persist for 3+ seconds
- Overwrites status.txt every 60 seconds (SD-card-friendly)
"""

import time
import threading

import numpy as np
import libcamera
from picamera2 import Picamera2
from picamera2.devices import Hailo

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HEF_PATH = "/usr/share/hailo-models/yolov8s_h8l.hef"
STATUS_PATH = "/home/calyxclaw-one/hailo_yolov8/status.txt"
CONFIDENCE_THRESHOLD = 0.4
PERSIST_SECONDS = 3.0       # object must appear continuously for this long
WRITE_INTERVAL = 60         # seconds between status.txt overwrites
CAMERA_ROTATION = 180

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


# ---------------------------------------------------------------------------
# Detection tracker
# ---------------------------------------------------------------------------
class DetectionTracker:
    """Tracks per-class presence using in-memory state only."""

    def __init__(self, persist_seconds: float):
        self.persist_seconds = persist_seconds
        # {class_name: {"first_seen": float, "last_seen": float, "confidence": float}}
        self._active = {}
        self._lock = threading.Lock()

    def update(self, detections):
        """
        Update tracker with current-frame detections.

        Args:
            detections: list of (class_name, confidence) for this frame
        """
        now = time.time()
        seen_now = {cls: conf for cls, conf in detections}

        with self._lock:
            for cls, conf in seen_now.items():
                if cls in self._active:
                    entry = self._active[cls]
                    self._active[cls] = {
                        "first_seen": entry["first_seen"],
                        "last_seen": now,
                        "confidence": max(entry["confidence"], conf),
                    }
                else:
                    self._active[cls] = {
                        "first_seen": now,
                        "last_seen": now,
                        "confidence": conf,
                    }

            # Remove classes not seen recently
            stale = [
                cls for cls, entry in self._active.items()
                if now - entry["last_seen"] > self.persist_seconds
            ]
            for cls in stale:
                del self._active[cls]

    def persistent_detections(self):
        """
        Return objects that have been present for >= persist_seconds.

        Returns:
            list of (class_name, confidence, duration_seconds)
        """
        now = time.time()
        results = []
        with self._lock:
            for cls, entry in self._active.items():
                duration = entry["last_seen"] - entry["first_seen"]
                if duration >= self.persist_seconds:
                    results.append((cls, entry["confidence"], duration))
        results.sort(key=lambda x: x[2], reverse=True)
        return results


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------
def write_status(tracker, stop_event):
    """Background thread: overwrites status.txt every WRITE_INTERVAL seconds."""
    while not stop_event.wait(WRITE_INTERVAL):
        persistent = tracker.persistent_detections()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines = ["# Updated: {}\n".format(timestamp)]
        if persistent:
            for cls, conf, duration in persistent:
                lines.append("{} | {:<20} | {:5.1f}% | {:.1f}s\n".format(
                    timestamp, cls, conf * 100, duration))
        else:
            lines.append("(no persistent detections)\n")

        with open(STATUS_PATH, "w") as f:
            f.writelines(lines)
        print("[{}] status.txt updated -- {} persistent object(s)".format(
            timestamp, len(persistent)))


# ---------------------------------------------------------------------------
# YOLOv8 output parser
# ---------------------------------------------------------------------------
def parse_detections(raw_output):
    """
    Parse Hailo YOLOv8 NMS postprocess output.

    raw_output: list of 80 numpy arrays, one per COCO class.
      Each array shape: (N, 5) where N = number of detections for that class.
      Columns: [x1, y1, x2, y2, confidence] (normalised 0-1)

    Returns list of (class_name, confidence) for detections above threshold.
    """
    detections = []

    for class_idx, class_detections in enumerate(raw_output):
        if class_detections is None or len(class_detections) == 0:
            continue

        # class_detections shape: (N, 5)
        confidences = class_detections[:, 4]
        valid_mask = confidences >= CONFIDENCE_THRESHOLD
        if not np.any(valid_mask):
            continue

        best_conf = float(np.max(confidences[valid_mask]))
        class_name = COCO_CLASSES[class_idx] if class_idx < len(COCO_CLASSES) else "class_{}".format(class_idx)
        detections.append((class_name, best_conf))

    return detections


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    tracker = DetectionTracker(persist_seconds=PERSIST_SECONDS)
    stop_event = threading.Event()

    writer_thread = threading.Thread(
        target=write_status,
        args=(tracker, stop_event),
        daemon=True,
    )
    writer_thread.start()

    with Hailo(HEF_PATH) as hailo:
        model_h, model_w, _ = hailo.get_input_shape()

        with Picamera2() as picam2:
            transform = libcamera.Transform(rotation=CAMERA_ROTATION)
            main_config = picam2.create_preview_configuration(
                main={"size": (1920, 1080)},
                lores={"size": (model_w, model_h), "format": "RGB888"},
                transform=transform,
                buffer_count=4,
            )
            picam2.configure(main_config)
            picam2.start()
            print("Camera started. Model input: {}x{}".format(model_w, model_h))
            print("Rotation: {} degrees".format(CAMERA_ROTATION))
            print("Logging objects present for {}s+ to {}".format(PERSIST_SECONDS, STATUS_PATH))
            print("Press Ctrl+C to stop.")

            try:
                while True:
                    lores_frame = picam2.capture_array("lores")
                    raw_output = hailo.run(lores_frame)
                    detections = parse_detections(raw_output)
                    tracker.update(detections)

                    if detections:
                        names = ", ".join(
                            "{}({:.0%})".format(c, conf) for c, conf in detections
                        )
                        print("\r[{}] {:<80}".format(
                            time.strftime("%H:%M:%S"), names), end="", flush=True)

            except KeyboardInterrupt:
                print("\nStopping...")
            finally:
                stop_event.set()
                writer_thread.join(timeout=5)


if __name__ == "__main__":
    main()
