# -*- coding: utf-8 -*-
"""
RKNN NPU backend for hand landmarks (Rockchip RK3576/RK3588 ...).

Runs MediaPipe's own palm-detection + hand-landmark models (converted to .rknn
for RK3576) on the NPU and mimics the `mp.solutions.hands.Hands`
interface: `.process(rgb)` returns an object with `multi_hand_landmarks`
(MediaPipe NormalizedLandmarkList), and `.close()` releases resources.
The gesture logic in gesture_recognizer.py therefore stays unchanged.

Backend selection via env var GESTURE_BACKEND: auto (default) | npu | cpu
"""
import math
import os
import types
import platform
from pathlib import Path

import cv2
import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
PALM_MODEL = os.path.join(MODEL_DIR, "palm_detection_full.rknn")
LANDMARK_MODEL = os.path.join(MODEL_DIR, "hand_landmark_full.rknn")

PALM_SIZE = 192
LANDMARK_SIZE = 224
# Landmarks used by MediaPipe HandLandmarksToRectCalculator
_PARTIAL_IDS = [0, 1, 2, 3, 5, 6, 9, 10, 13, 14, 17, 18]


def _normalize_radians(a):
    return a - 2 * math.pi * math.floor((a + math.pi) / (2 * math.pi))


def _palm_anchors():
    """SsdAnchorsCalculator options of MediaPipe palm_detection (192x192)."""
    strides = [8, 16, 16, 16]
    anchors = []
    layer = 0
    while layer < len(strides):
        repeats = 0
        last = layer
        while last < len(strides) and strides[last] == strides[layer]:
            repeats += 2  # aspect ratio 1.0 + interpolated scale
            last += 1
        fm = int(math.ceil(PALM_SIZE / strides[layer]))
        for y in range(fm):
            for x in range(fm):
                for _ in range(repeats):
                    anchors.append(((x + 0.5) / fm, (y + 0.5) / fm))
        layer = last
    return np.array(anchors, dtype=np.float32)


def _weighted_nms(boxes, scores, kps, iou_thresh=0.3):
    """MediaPipe WEIGHTED non-max suppression. boxes: (N,4) xmin,ymin,xmax,ymax."""
    order = np.argsort(-scores)
    out = []
    while order.size > 0:
        i = order[0]
        xx1 = np.maximum(boxes[i, 0], boxes[order, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = (boxes[order, 2] - boxes[order, 0]) * (boxes[order, 3] - boxes[order, 1])
        iou = inter / (area[0] + area - inter + 1e-9)
        group = order[iou > iou_thresh]
        w = scores[group][:, None]
        box = (boxes[group] * w).sum(0) / w.sum()
        kp = (kps[group] * w[:, :, None]).sum(0) / w.sum()
        out.append((box, kp, float(scores[i])))
        order = order[iou <= iou_thresh]
    return out


class _RKNNModel:
    def __init__(self, path, core_mask):
        from rknnlite.api import RKNNLite
        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(path) != 0:
            raise RuntimeError(f"load_rknn failed: {path}")
        if self.rknn.init_runtime(core_mask=core_mask) != 0:
            raise RuntimeError(f"init_runtime failed: {path}")

    def run(self, img_rgb_u8):
        return self.rknn.inference(inputs=[img_rgb_u8[None]], data_format="nhwc")

    def release(self):
        try:
            self.rknn.release()
        except Exception:
            pass


class RKNNHands:
    """Drop-in replacement for mp.solutions.hands.Hands running on the RKNN NPU."""

    def __init__(self, max_num_hands=2, min_detection_confidence=0.6, min_tracking_confidence=0.6):
        from rknnlite.api import RKNNLite
        from mediapipe.framework.formats import landmark_pb2
        self._landmark_pb2 = landmark_pb2
        self.max_num_hands = max_num_hands
        self.min_det = min_detection_confidence
        self.min_track = min_tracking_confidence
        self.anchors = _palm_anchors()
        self.palm = _RKNNModel(PALM_MODEL, RKNNLite.NPU_CORE_0)
        try:
            self.landmark = _RKNNModel(LANDMARK_MODEL, RKNNLite.NPU_CORE_1)
        except Exception:
            self.palm.release()
            raise
        self._tracked = []  # rects (cx, cy, size, rot) in pixels from last frame

    # ---------------- palm detection ----------------
    def _detect_palms(self, rgb):
        h, w = rgb.shape[:2]
        side = max(w, h)
        pad_x, pad_y = (side - w) / 2.0, (side - h) / 2.0
        square = cv2.copyMakeBorder(rgb, int(pad_y), side - h - int(pad_y), int(pad_x),
                                    side - w - int(pad_x), cv2.BORDER_CONSTANT, value=0)
        inp = cv2.resize(square, (PALM_SIZE, PALM_SIZE), interpolation=cv2.INTER_LINEAR)
        outs = self.palm.run(inp)
        reg = next(o for o in outs if o.shape[-1] == 18).reshape(-1, 18)
        cls = next(o for o in outs if o.shape[-1] == 1).reshape(-1)
        scores = 1.0 / (1.0 + np.exp(-np.clip(cls, -100, 100)))
        keep = scores >= self.min_det
        if not np.any(keep):
            return []
        reg, scores, anc = reg[keep], scores[keep], self.anchors[keep]
        cx = reg[:, 0] / PALM_SIZE + anc[:, 0]
        cy = reg[:, 1] / PALM_SIZE + anc[:, 1]
        bw = reg[:, 2] / PALM_SIZE
        bh = reg[:, 3] / PALM_SIZE
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        kps = reg[:, 4:].reshape(-1, 7, 2) / PALM_SIZE + anc[:, None, :]

        rects = []
        for box, kp, _ in _weighted_nms(boxes, scores, kps)[: self.max_num_hands]:
            # normalized-square -> image pixels
            bx = box * side - np.array([pad_x, pad_y, pad_x, pad_y])
            kp = kp * side - np.array([pad_x, pad_y])
            # DetectionsToRects: rotation from wrist(0) to middle MCP(2), target 90deg
            rot = _normalize_radians(math.pi / 2 - math.atan2(-(kp[2, 1] - kp[0, 1]), kp[2, 0] - kp[0, 0]))
            rects.append(self._rect_transform((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2,
                                              bx[2] - bx[0], bx[3] - bx[1], rot, 3.0, -0.4))
        return rects

    @staticmethod
    def _rect_transform(cx, cy, rw, rh, rot, scale, shift_y):
        """RectTransformationCalculator in pixel space with square_long=True."""
        cx += -rh * shift_y * math.sin(rot)
        cy += rh * shift_y * math.cos(rot)
        size = max(rw, rh) * scale
        return cx, cy, size, rot

    # ---------------- landmarks ----------------
    @staticmethod
    def _rect_to_image_affine(rect):
        """Affine mapping the LANDMARK_SIZE crop back to image pixels."""
        cx, cy, size, rot = rect
        c, s = math.cos(rot), math.sin(rot)
        k = size / LANDMARK_SIZE
        return np.array([[c * k, -s * k, cx - (c - s) * size / 2],
                         [s * k, c * k, cy - (s + c) * size / 2]], dtype=np.float32)

    def _landmarks(self, rgb, rect):
        to_img = self._rect_to_image_affine(rect)
        to_crop = cv2.invertAffineTransform(to_img)
        crop = cv2.warpAffine(rgb, to_crop, (LANDMARK_SIZE, LANDMARK_SIZE),
                              flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        outs = self.landmark.run(crop)
        lms = next(o for o in outs if o.size == 63).reshape(21, 3).astype(np.float32)
        presence = float(np.asarray(outs[1]).reshape(-1)[0])
        xy = lms[:, :2] @ to_img[:, :2].T + to_img[:, 2]
        z = lms[:, 2] * rect[2] / LANDMARK_SIZE
        return np.column_stack([xy, z]), presence

    @staticmethod
    def _landmarks_to_rect(pts):
        """HandLandmarksToRectCalculator + RectTransformation(scale 2.0, shift_y -0.1)."""
        x0, y0 = pts[0, 0], pts[0, 1]
        x1 = ((pts[5, 0] + pts[13, 0]) / 2 + pts[9, 0]) / 2
        y1 = ((pts[5, 1] + pts[13, 1]) / 2 + pts[9, 1]) / 2
        rot = _normalize_radians(math.pi / 2 - math.atan2(-(y1 - y0), x1 - x0))
        p = pts[_PARTIAL_IDS, :2]
        axis_c = (p.min(0) + p.max(0)) / 2
        c, s = math.cos(-rot), math.sin(-rot)
        d = p - axis_c
        proj = np.column_stack([d[:, 0] * c - d[:, 1] * s, d[:, 0] * s + d[:, 1] * c])
        pmin, pmax = proj.min(0), proj.max(0)
        pc = (pmin + pmax) / 2
        c, s = math.cos(rot), math.sin(rot)
        cx = pc[0] * c - pc[1] * s + axis_c[0]
        cy = pc[0] * s + pc[1] * c + axis_c[1]
        rw, rh = pmax - pmin
        return RKNNHands._rect_transform(cx, cy, rw, rh, rot, 2.0, -0.1)

    @staticmethod
    def _overlaps(a, b, thresh=0.5):
        ax0, ay0, ax1, ay1 = a[0] - a[2] / 2, a[1] - a[2] / 2, a[0] + a[2] / 2, a[1] + a[2] / 2
        bx0, by0, bx1, by1 = b[0] - b[2] / 2, b[1] - b[2] / 2, b[0] + b[2] / 2, b[1] + b[2] / 2
        inter = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
        return inter / (a[2] ** 2 + b[2] ** 2 - inter + 1e-9) > thresh

    def process(self, rgb):
        h, w = rgb.shape[:2]
        rects = list(self._tracked)
        # Like MediaPipe: only run palm detection when fewer hands than max are tracked
        if len(rects) < self.max_num_hands:
            for r in self._detect_palms(rgb):
                if len(rects) >= self.max_num_hands:
                    break
                if not any(self._overlaps(r, t) for t in rects):
                    rects.append(r)

        hands, tracked = [], []
        for rect in rects:
            pts, presence = self._landmarks(rgb, rect)
            if presence < self.min_track:
                continue
            nxt = self._landmarks_to_rect(pts)
            if any(self._overlaps(nxt, t) for t in tracked):
                continue
            tracked.append(nxt)
            lm_list = self._landmark_pb2.NormalizedLandmarkList()
            for x, y, z in pts:
                lm_list.landmark.add(x=float(x / w), y=float(y / h), z=float(z / w))
            hands.append(lm_list)
        self._tracked = tracked
        return types.SimpleNamespace(multi_hand_landmarks=hands or None)

    def close(self):
        for m in (getattr(self, "palm", None), getattr(self, "landmark", None)):
            if m is not None:
                m.release()
        self.palm = self.landmark = None


def is_rk3576():
    """Only the supplied RK3576 models are eligible for automatic NPU loading."""
    if platform.system() != "Linux" or platform.machine().lower() not in ("aarch64", "arm64"):
        return False
    try:
        compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    except FileNotFoundError:
        return False
    return b"rockchip,rk3576" in compatible


def create_hands(max_num_hands=2, min_detection_confidence=0.6, min_tracking_confidence=0.6):
    """Return an RKNNHands instance, or None to let the caller fall back to MediaPipe CPU."""
    backend = os.environ.get("GESTURE_BACKEND", "auto").lower()
    if backend not in ("auto", "npu", "cpu"):
        raise ValueError(f"Invalid GESTURE_BACKEND: {backend!r}; expected auto, npu or cpu")
    if backend == "cpu":
        return None
    if not is_rk3576():
        if backend == "npu":
            raise RuntimeError("The supplied NPU models require an ARM64 RK3576 board")
        print("[gesture] Non-RK3576 platform: using MediaPipe CPU")
        return None
    try:
        if not (os.path.exists(PALM_MODEL) and os.path.exists(LANDMARK_MODEL)):
            raise FileNotFoundError(f"RKNN models not found in {MODEL_DIR}")
        hands = RKNNHands(max_num_hands, min_detection_confidence, min_tracking_confidence)
        print("[gesture] Hand landmark backend: RKNN NPU")
        return hands
    except Exception as e:
        if backend == "npu":
            raise
        print(f"[gesture] NPU backend unavailable ({e}), falling back to MediaPipe CPU")
        return None
