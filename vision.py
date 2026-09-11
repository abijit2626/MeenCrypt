"""
track_fish_visualize.py — CV visualization + periodic JSON logging every 5 seconds.
Includes fixes for trail accumulation, ID thrashing, and noise blobs.
"""

import os
import time
import math
import itertools
import json
import sys
from collections import deque, defaultdict

import cv2
import numpy as np

# Override via FISHRAND_CAMERA_INDEX so run scripts can point this at a
# droidcam/v4l2loopback device without editing this file each time.
CAMERA_INDEX = int(os.environ.get("FISHRAND_CAMERA_INDEX", "0"))
MIN_CONTOUR_AREA = 150
MAX_FISH = 10
TRAIL_LENGTH = 20
ACTIVITY_HISTORY_LEN = 100
MAX_MATCH_DIST = 150  # Increased to prevent ID thrashing during fast motion
TRACK_TTL = 25        # Increased to retain ID through brief detection gaps
MAX_FISH_FOR_DISTANCE_LINES = 4

# JSON Logging options
JSON_LOG_INTERVAL = 5.0  # seconds
PRINT_JSON_TO_TERMINAL = True
SAVE_JSON_TO_FILE = True
JSON_FILENAME = "fish_log.json"


def find_camera_index(max_check: int = 5):
    for i in range(max_check):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, _ = cap.read()
            print(f"Index {i}: {'OK, frame read' if ok else 'opened but no frame'}")
            cap.release()
        else:
            print(f"Index {i}: not available")


class FishTracker:
    def __init__(self, min_contour_area=MIN_CONTOUR_AREA, max_fish=MAX_FISH, detect_shadows=False):
        self.min_contour_area = min_contour_area
        self.max_fish = max_fish
        self.detect_shadows = detect_shadows
        self._build_bg_subtractor()
        self.tracks = {}
        self.next_id = 0

    def _build_bg_subtractor(self):
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=25, detectShadows=self.detect_shadows
        )

    def toggle_shadows(self):
        self.detect_shadows = not self.detect_shadows
        self._build_bg_subtractor()
        self.tracks = {}
        self.next_id = 0

    def _assign_ids(self, detections):
        track_ids = list(self.tracks.keys())
        candidates = []
        for tid in track_ids:
            tx, ty = self.tracks[tid]["centroid"]
            for di, fd in enumerate(detections):
                dx, dy = fd["centroid"]
                dist = math.hypot(dx - tx, dy - ty)
                if dist <= MAX_MATCH_DIST:
                    candidates.append((dist, tid, di))
        candidates.sort(key=lambda c: c[0])

        matched_tracks = set()
        matched_dets = set()
        assignment = {}
        for dist, tid, di in candidates:
            if tid in matched_tracks or di in matched_dets:
                continue
            assignment[di] = tid
            matched_tracks.add(tid)
            matched_dets.add(di)

        new_tracks = {}
        for di, fd in enumerate(detections):
            if di in assignment:
                tid = assignment[di]
                new_tracks[tid] = {"centroid": fd["centroid"], "missed": 0}
                fd["id"] = tid
            else:
                tid = self.next_id
                self.next_id += 1
                new_tracks[tid] = {"centroid": fd["centroid"], "missed": 0}
                fd["id"] = tid

        for tid in track_ids:
            if tid not in matched_tracks:
                missed = self.tracks[tid]["missed"] + 1
                if missed <= TRACK_TTL:
                    new_tracks[tid] = {"centroid": self.tracks[tid]["centroid"], "missed": missed}

        self.tracks = new_tracks

    def process_frame(self, frame: np.ndarray) -> dict:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        fg_mask = self.bg_subtractor.apply(gray)
        _, thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        # Morphological opening removes stray reflections and speckle noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) > self.min_contour_area]
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[: self.max_fish]

        fish_data = []
        for c in contours:
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            area = cv2.contourArea(c)
            fish_data.append({"centroid": (cx, cy), "area": area, "contour": c})

        prev_positions = {tid: t["centroid"] for tid, t in self.tracks.items()}
        self._assign_ids(fish_data)

        for fd in fish_data:
            cx, cy = fd["centroid"]
            vx, vy, direction, displacement = 0.0, 0.0, 0.0, 0.0
            prev = prev_positions.get(fd["id"])
            if prev is not None:
                dx, dy = cx - prev[0], cy - prev[1]
                displacement = math.hypot(dx, dy)
                vx, vy = dx, dy
                direction = math.atan2(dy, dx)
            fd["displacement"] = displacement
            fd["velocity"] = (vx, vy)
            fd["direction"] = direction

        pairwise_distances = [
            (a["id"], b["id"], math.hypot(a["centroid"][0] - b["centroid"][0], a["centroid"][1] - b["centroid"][1]))
            for a, b in itertools.combinations(fish_data, 2)
        ]

        activity_pct = 100.0 * np.sum(thresh > 0) / thresh.size

        return {
            "fish": fish_data,
            "pairwise_distances": pairwise_distances,
            "activity_pct": activity_pct,
            "mask": thresh,
        }


class Visualizer:
    COLORS = [
        (0, 255, 0), (255, 128, 0), (0, 200, 255), (255, 0, 200),
        (0, 255, 255), (200, 0, 255), (128, 255, 0), (0, 128, 255),
    ]

    def __init__(self):
        self.trails = defaultdict(lambda: deque(maxlen=TRAIL_LENGTH))
        self.activity_history = deque(maxlen=ACTIVITY_HISTORY_LEN)
        self.show_trails = True
        self.show_distance_lines = True

    def _color(self, fish_id):
        return self.COLORS[fish_id % len(self.COLORS)]

    def draw(self, frame, result, min_area, shadows_on, active_track_ids):
        h, w = frame.shape[:2]
        fish = result["fish"]

        # Purge stale trails for dropped IDs
        dead_ids = [tid for tid in list(self.trails.keys()) if tid not in active_track_ids]
        for tid in dead_ids:
            del self.trails[tid]

        if self.show_trails:
            for fd in fish:
                self.trails[fd["id"]].append(fd["centroid"])
            for tid, trail in list(self.trails.items()):
                pts = list(trail)
                color_base = self._color(tid)
                for k in range(1, len(pts)):
                    alpha = k / len(pts)
                    color = tuple(int(c * alpha) for c in color_base)
                    p1 = tuple(map(int, pts[k - 1]))
                    p2 = tuple(map(int, pts[k]))
                    cv2.line(frame, p1, p2, color, 2)

        if self.show_distance_lines and len(fish) <= MAX_FISH_FOR_DISTANCE_LINES:
            id_to_centroid = {fd["id"]: fd["centroid"] for fd in fish}
            for (i, j, dist) in result["pairwise_distances"]:
                p1 = tuple(map(int, id_to_centroid[i]))
                p2 = tuple(map(int, id_to_centroid[j]))
                cv2.line(frame, p1, p2, (60, 60, 60), 1, cv2.LINE_AA)
                mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
                cv2.putText(frame, f"{dist:.0f}", mid, cv2.FONT_HERSHEY_SIMPLEX, 0.35, (150, 150, 150), 1)

        for fd in fish:
            fid = fd["id"]
            cx, cy = map(int, fd["centroid"])
            color = self._color(fid)

            cv2.circle(frame, (cx, cy), 7, color, 2)
            cv2.putText(frame, f"#{fid}", (cx + 9, cy - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

            vx, vy = fd["velocity"]
            speed = math.hypot(vx, vy)
            if speed > 0.5:
                scale = 4
                end = (int(cx + vx * scale), int(cy + vy * scale))
                cv2.arrowedLine(frame, (cx, cy), end, color, 2, tipLength=0.4)

            cv2.putText(frame, f"v={speed:.1f}", (cx + 9, cy + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

        cv2.rectangle(frame, (0, 0), (w, 50), (10, 10, 10), -1)
        cv2.putText(frame,
                    f"Fish: {len(fish)}  Activity: {result['activity_pct']:.1f}%  "
                    f"MinArea: {min_area}  Trails: {'ON' if self.show_trails else 'OFF'}  "
                    f"Shadows: {'ON' if shadows_on else 'OFF'}  "
                    f"Dist: {'ON' if self.show_distance_lines else 'OFF'}",
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        cv2.putText(frame, "[q]uit  [+/-] threshold  [t]rails  [m]ask  [s]hadows  [d]istance",
                    (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)

        self.activity_history.append(result["activity_pct"])
        spark_h = 40
        spark_top = h - spark_h
        cv2.rectangle(frame, (0, spark_top), (w, h), (10, 10, 10), -1)
        pts = list(self.activity_history)
        if len(pts) > 1:
            max_val = max(max(pts), 5.0)
            step_x = w / ACTIVITY_HISTORY_LEN
            for k in range(1, len(pts)):
                x1 = int((k - 1) * step_x)
                x2 = int(k * step_x)
                y1 = int(h - (pts[k - 1] / max_val) * (spark_h - 4))
                y2 = int(h - (pts[k] / max_val) * (spark_h - 4))
                cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 120), 2)

        return frame


def output_json_payload(result: dict):
    """Formats tracking metrics into clean JSON and outputs to terminal/file."""
    payload = {
        "timestamp": time.time(),
        "fish_count": len(result["fish"]),
        "activity_pct": round(result["activity_pct"], 2),
        "fish": [
            {
                "id": f["id"],
                "centroid": [round(f["centroid"][0], 1), round(f["centroid"][1], 1)],
                "area": round(f["area"], 1),
                "speed": round(math.hypot(f["velocity"][0], f["velocity"][1]), 2),
                "direction_rad": round(f["direction"], 2)
            }
            for f in result["fish"]
        ]
    }
    json_str = json.dumps(payload)

    if PRINT_JSON_TO_TERMINAL:
        print(f"\n[JSON LOG] {json_str}")
        sys.stdout.flush()

    if SAVE_JSON_TO_FILE:
        with open(JSON_FILENAME, "a") as f:
            f.write(json_str + "\n")


def main():
    global MIN_CONTOUR_AREA

    print("Scanning camera indices...")
    find_camera_index()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"Could not open camera index {CAMERA_INDEX}.")
        return

    for _ in range(10):
        cap.read()
        time.sleep(0.03)

    tracker = FishTracker(min_contour_area=MIN_CONTOUR_AREA)
    viz = Visualizer()
    show_mask = False
    last_json_time = time.time()

    print("Running. Focus the preview window and use q/+/-/t/m/s/d.")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Frame read failed.")
            break

        tracker.min_contour_area = MIN_CONTOUR_AREA
        result = tracker.process_frame(frame)
        annotated = viz.draw(frame.copy(), result, MIN_CONTOUR_AREA, tracker.detect_shadows, tracker.tracks.keys())

        # Non-blocking 5-second interval check
        current_time = time.time()
        if current_time - last_json_time >= JSON_LOG_INTERVAL:
            output_json_payload(result)
            last_json_time = current_time

        cv2.imshow("FISHRAND — visualization", annotated)
        if show_mask:
            cv2.imshow("mask", result["mask"])

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key in (ord("+"), ord("=")):
            MIN_CONTOUR_AREA = min(2000, MIN_CONTOUR_AREA + 10)
        elif key == ord("-"):
            MIN_CONTOUR_AREA = max(5, MIN_CONTOUR_AREA - 10)
        elif key == ord("t"):
            viz.show_trails = not viz.show_trails
            viz.trails.clear()
        elif key == ord("m"):
            show_mask = not show_mask
            if not show_mask:
                cv2.destroyWindow("mask")
        elif key == ord("s"):
            tracker.toggle_shadows()
            viz.trails.clear()
        elif key == ord("d"):
            viz.show_distance_lines = not viz.show_distance_lines

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()