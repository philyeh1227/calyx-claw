#!/usr/bin/env python3
"""
hailo_yolov8/detect.py

YOLOv8 Hailo-8L object detection with live frame buffer and capture API.

- Captures frames from Picamera2 (1920x1080 main + model-size lores)
- Runs YOLOv8 detection on Hailo NPU via lores stream
- Keeps latest full-resolution frame in RAM buffer (no disk writes)
- Exposes HTTP API on port 8081 for on-demand photo capture
- Writes detection status to status.txt every 60 seconds
"""

import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import libcamera
from picamera2 import Picamera2
from picamera2.devices import Hailo
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HEF_PATH = "/usr/share/hailo-models/yolov8s_h8l.hef"
STATUS_PATH = "/home/calyxclaw-one/hailo_yolov8/status.txt"
CONFIDENCE_THRESHOLD = 0.4
PERSIST_SECONDS = 3.0       # object must appear continuously for this long
WRITE_INTERVAL = 60         # seconds between status.txt overwrites
CAMERA_ROTATION = 180

CAPTURE_SERVER_HOST = "127.0.0.1"
CAPTURE_SERVER_PORT = 8081
CAPTURE_DIR = "/tmp"
CAPTURE_QUALITY = 85

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
# Frame buffer (RAM only, no disk I/O)
# ---------------------------------------------------------------------------
class FrameBuffer:
    """Thread-safe buffer holding the latest full-resolution camera frame."""

    def __init__(self):
        self._frame = None      # numpy array (RGB888, 1920x1080)
        self._lock = threading.Lock()
        self._timestamp = 0.0

    def update(self, frame: np.ndarray):
        """Store a new frame (caller must pass a copy, not a DMA view)."""
        with self._lock:
            self._frame = frame
            self._timestamp = time.time()

    @property
    def available(self) -> bool:
        with self._lock:
            return self._frame is not None

    def save_jpeg(self, output_path: str, quality: int = CAPTURE_QUALITY) -> dict:
        """Save the buffered frame to a JPEG file.

        Returns metadata dict with path, timestamp, and file size.
        Raises RuntimeError if no frame is buffered yet.
        """
        with self._lock:
            if self._frame is None:
                raise RuntimeError("no frame available yet (camera still starting?)")
            frame = self._frame.copy()
            ts = self._timestamp

        # Picamera2 RGB888 is actually BGR in memory; convert to RGB for PIL
        img = Image.fromarray(frame[:, :, ::-1])
        img.save(output_path, "JPEG", quality=quality)

        return {
            "path": output_path,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
            "size_bytes": os.path.getsize(output_path),
        }


# ---------------------------------------------------------------------------
# Capture HTTP API
# ---------------------------------------------------------------------------
class CaptureHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP handler for on-demand photo capture.

    Endpoints:
        GET  /health   -> {"status": "ok", ...}
        POST /capture  -> save buffered frame to JPEG, return file metadata
            Optional JSON body: {"path": "/custom/path.jpg", "quality": 90}
    """

    frame_buffer: FrameBuffer = None  # set before server starts

    def log_message(self, fmt, *args):
        print("[capture-api] {}".format(fmt % args), file=sys.stderr, flush=True)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "service": "detect-capture",
                "frame_ready": self.frame_buffer.available if self.frame_buffer else False,
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/capture"):
            self._send_json(404, {"error": "not found"})
            return

        if self.frame_buffer is None:
            self._send_json(503, {"error": "frame buffer not initialized"})
            return

        # Parse optional JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, ValueError) as e:
                self._send_json(400, {"error": "invalid JSON: {}".format(e)})
                return

        default_path = os.path.join(
            CAPTURE_DIR, "claw_capture_{}.jpg".format(int(time.time())),
        )
        output_path = body.get("path", default_path)
        quality = body.get("quality", CAPTURE_QUALITY)

        try:
            result = self.frame_buffer.save_jpeg(output_path, quality)
            self._send_json(200, result)
        except RuntimeError as e:
            self._send_json(503, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})


def start_capture_server(frame_buffer: FrameBuffer) -> HTTPServer:
    """Start the capture API server in a daemon thread."""
    CaptureHandler.frame_buffer = frame_buffer
    server = HTTPServer((CAPTURE_SERVER_HOST, CAPTURE_SERVER_PORT), CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(
        "[capture-api] listening on {}:{}".format(
            CAPTURE_SERVER_HOST, CAPTURE_SERVER_PORT,
        ),
        flush=True,
    )
    return server


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
    frame_buffer = FrameBuffer()
    stop_event = threading.Event()

    writer_thread = threading.Thread(
        target=write_status,
        args=(tracker, stop_event),
        daemon=True,
    )
    writer_thread.start()

    capture_server = start_capture_server(frame_buffer)

    with Hailo(HEF_PATH) as hailo:
        model_h, model_w, _ = hailo.get_input_shape()

        with Picamera2() as picam2:
            transform = libcamera.Transform(rotation=CAMERA_ROTATION)
            main_config = picam2.create_preview_configuration(
                main={"size": (1920, 1080), "format": "RGB888"},
                lores={"size": (model_w, model_h), "format": "RGB888"},
                transform=transform,
                buffer_count=4,
            )
            picam2.configure(main_config)
            picam2.start()
            print("Camera started. Model input: {}x{}".format(model_w, model_h))
            print("Rotation: {} degrees".format(CAMERA_ROTATION))
            print("Logging objects present for {}s+ to {}".format(PERSIST_SECONDS, STATUS_PATH))
            print("Frame buffer: enabled (capture API on port {})".format(CAPTURE_SERVER_PORT))
            print("Press Ctrl+C to stop.")

            try:
                while True:
                    # Grab both streams from the same capture request
                    request = picam2.capture_request()
                    try:
                        lores_frame = request.make_array("lores").copy()
                        main_frame = request.make_array("main").copy()
                    finally:
                        request.release()

                    # Update RAM frame buffer (always fresh, no disk I/O)
                    frame_buffer.update(main_frame)

                    # Run detection on lores stream
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
                capture_server.shutdown()
                writer_thread.join(timeout=5)


if __name__ == "__main__":
    main()
