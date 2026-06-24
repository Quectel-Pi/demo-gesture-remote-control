# -*- coding: utf-8 -*-
"""
MediaPipe-based gesture recognizer (用于主程序)
- 基于 gesture_demo.txt 的算法实现（多点光流 + 门控 + 下滑路径积分 + 张开手掌等）
- 重用 MediaPipe Hands 实例，提供显式 close() 以释放资源
- process_frame(frame) -> (detection_result:dict, mp_result) :
    detection_result 包含键：'hand_present','num_hands','gesture','cmd','primary_center','fps'
- draw_landmarks(frame, mp_result) : 在传入的 BGR frame 上绘制 landmark（供 UI 可视化）
"""
import time
import cv2
import numpy as np
from collections import deque

try:
    import mediapipe as mp
except ImportError as e:
    raise RuntimeError("Please install mediapipe: pip install mediapipe") from e


class MediaPipeGestureRecognizer:
    def __init__(self):
        # MediaPipe Hands（只初始化一次，避免频繁创建导致内存/资源问题）
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6
        )
        self.drawer = mp.solutions.drawing_utils
        self.drawer_style = mp.solutions.drawing_styles

        # 光流与门控状态（参照 gesture_demo）
        self.prev_gray = None
        self.prev_points = None  # (N,1,2)
        self.flow_window_dx = deque(maxlen=4)
        self.flow_window_dy = deque(maxlen=4)

        # 参数
        self.flow_thresh_ratio = 0.040
        self.flow_static_ratio = 0.010
        self.flow_static_ratio_open_palm = 0.014
        self.swipe_consistent_min = 4

        self.ema_alpha = 0.25
        self.dy_ema = 0.0
        self.dx_ema = 0.0
        self.vel_thresh_norm_vertical = 1.20
        self.vel_thresh_norm_horizontal = 1.20
        self._dy_gate_high = False
        self._dx_gate_high = False
        self.last_frame_ms = 0

        # 角度门
        self.vertical_angle_gate_ratio_up = 2.2
        self.vertical_angle_gate_ratio_down = 1.8
        self.horizontal_angle_gate_ratio = 2.2

        # 下滑偏置
        self.down_bias = 0.90

        # 下滑路径积分窗
        self.dy_hist_norm = deque(maxlen=24)
        self.dy_hist_t = deque(maxlen=24)
        self.dy_path_window_ms = 250
        self.down_path_thresh = 1.80

        # 张开手掌
        self.open_palm_min_spread_ratio = 1.22
        self.open_palm_max_spread_ratio = 2.01
        self.open_palm_ms = 180
        self.open_palm_cooldown_ms = 200
        self._open_palm_start_ms = 0
        self._open_palm_release_cnt = 0
        self._last_motion_cmd_ms = 0
        self._last_spread = 0.0
        self.open_palm_armed = True
        self.open_palm_latched = False
        self.open_palm_release_frames = 3

        # 轨迹 & 主手选择
        self.tracks = {}
        self.next_track_id = 1
        self.primary_track_id = None
        self.primary_lock_ms = 700
        self.last_primary_set_ms = 0
        self.primary_last_center = None
        self.num_hands = 0

        # 节流（外部也可能有节流）
        self.last_cmd_ms = 0
        self.cmd_throttle_ms = 180

        # FPS 统计
        self.frame_count = 0
        self.start_time = time.time()
        self.fps = 0.0

    def close(self):
        """显式释放 MediaPipe Hands 使用的资源（必须在程序退出时调用）"""
        try:
            if self.hands is not None:
                try:
                    self.hands.close()
                except Exception:
                    pass
        finally:
            self.hands = None

    # ---------- Utils ----------
    @staticmethod
    def _is_finger_up(pts, tip, pip, delta=10):
        return pts[tip][1] < pts[pip][1] - delta

    @staticmethod
    def _palm_center(pts):
        # 使用 0,5,17 点的均值作为掌心（与 demo 保持一致）
        xs = [pts[0][0], pts[5][0], pts[17][0]]
        ys = [pts[0][1], pts[5][1], pts[17][1]]
        return int(np.mean(xs)), int(np.mean(ys))

    @staticmethod
    def _hand_width(pts):
        return abs(pts[17][0] - pts[5][0]) + 1e-6

    def _palm_spread(self, pts, cx, cy):
        tips = [8, 12, 16, 20]
        dists = [np.hypot(pts[t][0] - cx, pts[t][1] - cy) for t in tips]
        avg = float(np.mean(dists)) if dists else 0.0
        spread = avg / self._hand_width(pts)
        self._last_spread = spread
        return spread

    def _update_prev(self, gray, points):
        # points: list of (x,y)
        if not points:
            self.prev_gray = gray.copy()
            self.prev_points = None
            return
        self.prev_gray = gray.copy()
        self.prev_points = np.array([[p] for p in points], dtype=np.float32)

    @staticmethod
    def _robust_median(values):
        if not values:
            return 0.0
        arr = np.array(values, dtype=np.float32)
        med = np.median(arr)
        q1, q3 = np.percentile(arr, [25, 75])
        iqr = max(1e-6, q3 - q1)
        keep = arr[np.abs(arr - med) <= 1.5 * iqr]
        return float(np.median(keep)) if keep.size else float(med)

    def _hand_flow(self, gray, pts, cxcy):
        """
        多点金字塔光流（稳健中位数）
        anchors: palm center + MCPs + fingertips
        """
        anchor_idxs = [0, 5, 9, 13, 17, 8, 12, 16, 20]
        anchors = [cxcy] + [pts[i] for i in anchor_idxs]
        if self.prev_gray is None or self.prev_points is None:
            self._update_prev(gray, anchors)
            return 0.0, 0.0

        # 使用更保守、更快的参数，减少计算量并提高稳定性
        try:
            new_points, st, err = cv2.calcOpticalFlowPyrLK(
                self.prev_gray, gray, self.prev_points, None,
                winSize=(15, 15), maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)
            )
        except Exception:
            # 如果光流计算失败，重置 prev 并返回 0
            self._update_prev(gray, anchors)
            return 0.0, 0.0

        dxs, dys = [], []
        if new_points is not None and st is not None:
            prev_pts = self.prev_points.reshape(-1, 2)
            new_pts = new_points.reshape(-1, 2)
            st_flat = st.reshape(-1)
            for i in range(len(prev_pts)):
                if st_flat[i] == 1:
                    old = prev_pts[i]
                    new = new_pts[i]
                    dxs.append(float(new[0] - old[0]))
                    dys.append(float(new[1] - old[1]))

        # 更新 prev（放在这里保证每次基于最新帧）
        self._update_prev(gray, anchors)

        dx = self._robust_median(dxs)
        dy = self._robust_median(dys)
        self.flow_window_dx.append(dx)
        self.flow_window_dy.append(dy)
        return dx, dy

    @staticmethod
    def _consistent_sign(values, min_count=3):
        if not values:
            return False
        pos = sum(1 for v in values if v > 0)
        neg = sum(1 for v in values if v < 0)
        return (pos >= min_count) or (neg >= min_count)

    def _throttle(self):
        now = int(time.time() * 1000)
        if now - self.last_cmd_ms >= self.cmd_throttle_ms:
            self.last_cmd_ms = now
            return True
        return False

    # tracks + primary selection（用于在多手场景下选择主手）
    def _update_tracks(self, centers, now_ms):
        assign = {}
        unmatched_tracks = set(self.tracks.keys())
        for det_idx, c in enumerate(centers):
            best_id, best_dist = None, 1e9
            for tid in list(unmatched_tracks):
                pc = self.tracks[tid]["center"]
                d = np.hypot(c[0] - pc[0], c[1] - pc[1])
                if d < best_dist:
                    best_id, best_dist = tid, d
            if best_id is not None and best_dist <= 120:
                assign[det_idx] = best_id
                unmatched_tracks.remove(best_id)
                self.tracks[best_id]["center"] = c
                self.tracks[best_id]["last_seen_ms"] = now_ms
            else:
                tid = self.next_track_id
                self.next_track_id += 1
                self.tracks[tid] = {"center": c, "last_seen_ms": now_ms, "armed": True}
                assign[det_idx] = tid
        # 对超时 track 做复位/arming
        for tid in list(unmatched_tracks):
            last_seen = self.tracks[tid]["last_seen_ms"]
            if (now_ms - last_seen) >= 400:
                self.tracks[tid]["armed"] = True
        return assign

    def _select_primary(self, hands_pts2d, assign_map, w, h, now_ms):
        if not hands_pts2d:
            return None, {}
        # 如果在锁定窗口内且主手仍然存在，则保持主手
        if self.primary_track_id is not None and (now_ms - self.last_primary_set_ms) < self.primary_lock_ms:
            for det_idx, tid in assign_map.items():
                if tid == self.primary_track_id:
                    pts = hands_pts2d[det_idx]
                    cx, cy = self._palm_center(pts)
                    self.primary_last_center = (cx, cy)
                    return det_idx, {"reason": "lock_window"}
        # 否则选择面积最大的手
        best_det_idx, best_area, best_info = None, -1, {}
        for det_idx, pts in enumerate(hands_pts2d):
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            area = max(1, (max(xs) - min(xs)) * (max(ys) - min(ys)))
            if area > best_area:
                best_area = area
                cx, cy = self._palm_center(pts)
                best_det_idx = det_idx
                best_info = {"reason": "largest_area", "cx": cx, "cy": cy}
        tid = assign_map.get(best_det_idx)
        if tid is not None:
            self.primary_track_id = tid
            self.last_primary_set_ms = now_ms
            self.primary_last_center = (best_info["cx"], best_info["cy"])
        return best_det_idx, best_info

    # ----------------------------- Core infer (基于 demo 的判断) -----------------------------
    def _infer(self, pts, w, h, gray):
        scale = max(w, h)
        flow_static_px = max(8, int(scale * self.flow_static_ratio_open_palm))

        # 四指张开（不含拇指）
        four = 0
        four += 1 if self._is_finger_up(pts, 8, 6) else 0
        four += 1 if self._is_finger_up(pts, 12, 10) else 0
        four += 1 if self._is_finger_up(pts, 16, 14) else 0
        four += 1 if self._is_finger_up(pts, 20, 18) else 0

        # 光流
        cx, cy = self._palm_center(pts)
        dx_med, dy_med = 0.0, 0.0
        dx_flow, dy_flow = self._hand_flow(gray, pts, (cx, cy))
        dx_med = float(np.median(self.flow_window_dx)) if self.flow_window_dx else 0.0
        dy_med = float(np.median(self.flow_window_dy)) if self.flow_window_dy else 0.0

        now_ms = int(time.time() * 1000)
        dt_ms = max(16, now_ms - self.last_frame_ms) if self.last_frame_ms else 33
        self.last_frame_ms = now_ms

        # 归一化速度 + EMA
        hand_w = self._hand_width(pts)
        dy_speed = (dy_med * 1000.0) / (dt_ms + 1e-6)
        dx_speed = (dx_med * 1000.0) / (dt_ms + 1e-6)
        dy_norm = dy_speed / (hand_w + 1e-6)
        dx_norm = dx_speed / (hand_w + 1e-6)
        self.dy_ema = (1 - self.ema_alpha) * self.dy_ema + self.ema_alpha * dy_norm
        self.dx_ema = (1 - self.ema_alpha) * self.dx_ema + self.ema_alpha * dx_norm

        # ---- accumulate down path within window ----
        dy_norm_inst = dy_norm
        self.dy_hist_norm.append(dy_norm_inst)
        self.dy_hist_t.append(now_ms)
        down_path_sum = 0.0
        for v, t in zip(reversed(self.dy_hist_norm), reversed(self.dy_hist_t)):
            if (now_ms - t) > self.dy_path_window_ms:
                break
            if v > 0:
                down_path_sum += v

        # hysteresis reset
        if abs(self.dy_ema) <= self.vel_thresh_norm_vertical * 0.8:
            self._dy_gate_high = False
        if abs(self.dx_ema) <= self.vel_thresh_norm_horizontal * 0.8:
            self._dx_gate_high = False

        # Vertical swipe (volume)
        is_down = dy_med > 0
        v_gate = self.vertical_angle_gate_ratio_down if is_down else self.vertical_angle_gate_ratio_up

        # margin bias (接近底边放宽阈值)
        margin_px = max(0.0, h - cy)
        margin_scale = 1.0
        if margin_px < 120.0:
            margin_scale = 0.85 + 0.15 * (margin_px / 120.0)
        v_thr_base = self.vel_thresh_norm_vertical * (self.down_bias if is_down else 1.0)
        v_thr = v_thr_base * (margin_scale if is_down else 1.0)

        is_vertical = abs(dy_med) > v_gate * abs(dx_med)
        vertical_consistent = self._consistent_sign(self.flow_window_dy, self.swipe_consistent_min)

        speed_pass = (abs(self.dy_ema) > v_thr)
        path_pass = (is_down and down_path_sum > self.down_path_thresh)

        if four >= 1 and is_vertical and (speed_pass or path_pass) and not self._dy_gate_high and vertical_consistent:
            self._dy_gate_high = True
            self._last_motion_cmd_ms = now_ms
            gesture = "swipe_up" if self.dy_ema < 0 else "swipe_down"
            cmd = "vol_up" if self.dy_ema < 0 else "vol_down"
            return gesture, cmd

        # Horizontal swipe (seek)
        is_horizontal = abs(dx_med) > self.horizontal_angle_gate_ratio * abs(dy_med)
        if four >= 1 and is_horizontal and abs(self.dx_ema) > self.vel_thresh_norm_horizontal and not self._dx_gate_high \
           and self._consistent_sign(self.flow_window_dx, self.swipe_consistent_min):
            self._dx_gate_high = True
            self._last_motion_cmd_ms = now_ms
            gesture = "swipe_left" if self.dx_ema > 0 else "swipe_right"
            cmd = "seek_back" if self.dx_ema > 0 else "seek_forward"
            return gesture, cmd

        # Open palm (toggle)
        cooling = (now_ms - self._last_motion_cmd_ms) < self.open_palm_cooldown_ms
        is_static = (abs(dx_med) < flow_static_px) and (abs(dy_med) < flow_static_px)
        spread = self._palm_spread(pts, cx, cy)
        spread_ok = (four >= 4) and (self.open_palm_min_spread_ratio <= spread <= self.open_palm_max_spread_ratio)
        open_candidate = (not cooling) and is_static and spread_ok

        if not open_candidate:
            self._open_palm_start_ms = 0
            self._open_palm_release_cnt = min(self.open_palm_release_frames, self._open_palm_release_cnt + 1)
            if self._open_palm_release_cnt >= self.open_palm_release_frames:
                self.open_palm_latched = False
                self.open_palm_armed = True
        else:
            self._open_palm_release_cnt = 0
            if self.open_palm_armed and not self.open_palm_latched:
                if self._open_palm_start_ms == 0:
                    self._open_palm_start_ms = now_ms
                elif (now_ms - self._open_palm_start_ms) >= self.open_palm_ms:
                    self._open_palm_start_ms = 0
                    self.open_palm_latched = True
                    self.open_palm_armed = False
                    gesture = "open_palm"
                    cmd = "toggle"
                    return gesture, cmd

        return None, None

    # ----------------------------- process_frame & draw -----------------------------
    def process_frame(self, frame_bgr):
        """
        处理一帧 BGR 图像并返回 (detection_result, mp_result)
        - detection_result: dict
        - mp_result: mediapipe hands process 返回的结果（用于绘制）
        """
        # 避免在每帧都新建 MediaPipe 实例，复用 self.hands
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        h, w = frame_bgr.shape[:2]
        # FPS 统计（较低频率更新）
        self.frame_count += 1
        if self.frame_count % 30 == 0:
            elapsed = time.time() - self.start_time
            if elapsed > 0:
                self.fps = self.frame_count / elapsed
            self.start_time = time.time()
            self.frame_count = 0

        res = None
        try:
            if self.hands is not None:
                res = self.hands.process(rgb)
        except Exception:
            res = None

        hands_pts2d = []
        if res and getattr(res, "multi_hand_landmarks", None):
            for lm in res.multi_hand_landmarks:
                hands_pts2d.append([(int(p.x * w), int(p.y * h)) for p in lm.landmark])

        now_ms = int(time.time() * 1000)
        self.num_hands = len(hands_pts2d)
        centers = [self._palm_center(pts) for pts in hands_pts2d]

        # Track assignment and primary selection
        assign_map = self._update_tracks(centers, now_ms) if centers else {}
        primary_det_idx, sel_info = self._select_primary(hands_pts2d, assign_map, w, h, now_ms) if centers else (None, {})

        gesture = None
        cmd = None
        primary_center = None

        if primary_det_idx is not None:
            pts = hands_pts2d[primary_det_idx]
            primary_center = self._palm_center(pts)
            # 保证在切换主手时清理历史以避免残留
            tid = assign_map.get(primary_det_idx)
            if tid is not None:
                self.open_palm_armed = self.tracks.get(tid, {}).get("armed", True)

            # 如果是新主手，清空光流历史与下滑积分
            # 通过检测 primary_lock_ms 实现短期锁定（在 _select_primary 中设置）
            gesture, cmd = self._infer(pts, w, h, gray)

            # 在识别 open_palm 后更新 track armed 状态
            if gesture == "open_palm" and tid in self.tracks:
                self.tracks[tid]["armed"] = False
        else:
            # 没有主手时重置状态，回到画面后可快速触发
            self.open_palm_armed = True
            self.open_palm_latched = False
            self._open_palm_start_ms = 0
            self._open_palm_release_cnt = self.open_palm_release_frames

        detection_result = {
            'hand_present': self.num_hands > 0,
            'num_hands': self.num_hands,
            'gesture': gesture,
            'cmd': cmd,
            'primary_center': primary_center,
            'fps': float(self.fps)
        }
        return detection_result, res

    def draw_landmarks(self, frame_bgr, mp_result):
        """在 BGR 图像上绘制 MediaPipe 的 landmarks（安全调用，不抛异常）"""
        try:
            if mp_result and getattr(mp_result, "multi_hand_landmarks", None):
                for hand_landmarks in mp_result.multi_hand_landmarks:
                    try:
                        self.drawer.draw_landmarks(
                            frame_bgr,
                            hand_landmarks,
                            self.mp_hands.HAND_CONNECTIONS,
                            self.drawer_style.get_default_hand_landmarks_style(),
                            self.drawer_style.get_default_hand_connections_style()
                        )
                    except Exception:
                        # 单个手绘制出错时继续其他手的绘制
                        pass
        except Exception:
            pass