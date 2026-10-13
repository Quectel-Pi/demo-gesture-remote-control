import cv2
import time
import threading
from PySide6.QtCore import QThread, Signal
from gesture_recognizer import MediaPipeGestureRecognizer
from log import debug, error


class VideoCaptureThread(QThread):
    frame_ready = Signal(object)
    detection_status = Signal(dict)
    fps_updated = Signal(float)
    command_detected = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.cap = None
        self.running = False
        self.detecting = True
        self.show_landmarks = True

        # Exit and resource flags
        self.exiting = False
        self._closed = True
        self._lock = threading.RLock()
        self.camera_id = None
        self.reconnect_interval = 2.0
        self._last_reconnect_attempt = 0.0

        # Component initialization
        # MediaPipeGestureRecognizer will be created once and reused
        self.gesture = MediaPipeGestureRecognizer()

        # FPS calculation
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()
        self.last_command = None
        self.last_command_time = 0.0
        self.command_repeat_interval = 0.20
        self.command_repeat_intervals = {
            'seek_forward': 0.18,
            'seek_back': 0.18,
            'play': 0.26,
            'pause': 0.26,
            'toggle': 0.26,
            'vol_up': 0.20,
            'vol_down': 0.20,
        }

        # Processing config: limit processing resolution and detection rate (UI configurable)
        self.proc_width = 480  # Scale input to 480px wide (adjustable: 480/640/960)
        self.detection_fps = 15  # Target gesture detection rate (FPS)
        self._last_detect_time = 0.0  # time.time() timestamp in seconds
        self.mirror_preview = True  # Mirror preview only; gesture recognition direction is unchanged
        self.preview_emit_fps = 12  # Upper bound for UI preview refresh rate to reduce main-thread rendering pressure
        self.status_emit_fps = 10  # Upper bound for detection status emission to avoid signal storms
        self._last_preview_emit_time = 0.0
        self._last_status_emit_time = 0.0
        self.landmarks_hold_sec = 0.20  # Briefly reuse landmarks to avoid visual flicker
        self._last_landmarks_res = None
        self._last_landmarks_time = 0.0

        # Short status hold to avoid UI jitter when non-detection frames return empty
        self.status_hold_sec = 0.18
        self._last_nonempty_status = {}
        self._last_nonempty_status_time = 0.0

        # Lightweight adaptive preview FPS: keep detection at 15 FPS and tune preview within [min, max] based on load
        self.adaptive_preview_fps = True
        self.preview_emit_fps_min = 10
        self.preview_emit_fps_max = 14
        self._loop_ema_ms = 0.0
        self._last_preview_tune_time = 0.0

        self.frame_remain = 0
        self.command_remain = ''

    def find_available_camera(self):
        """Auto-detect an available camera device and return its id or None."""
        debug("Searching for available camera devices...")
        for i in range(10):
            temp_cap = None
            try:
                temp_cap = cv2.VideoCapture(i)
                if temp_cap is None:
                    continue
                if temp_cap.isOpened():
                    ret, frame = temp_cap.read()
                    if ret and frame is not None:
                        temp_cap.release()
                        debug(f"Found available camera at device ID: {i}")
                        return i
            except Exception as e:
                error(f"Error checking camera {i}: {e}")
            finally:
                if temp_cap is not None:
                    try:
                        if temp_cap.isOpened():
                            temp_cap.release()
                    except Exception:
                        pass
        # UVC fallback scan: on RK3576 (Quectel Pi M2) rkisp/rkcif occupy
        # /dev/video0-9 so a USB UVC camera can land on a high index (e.g. 73).
        # Scan sysfs for uvcvideo nodes when the 0-9 probe finds nothing.
        try:
            import glob, os as _os
            v4l_dirs = sorted(glob.glob('/sys/class/video4linux/video*'),
                              key=lambda x: int(x.rsplit('video', 1)[1]))
            for d in v4l_dirs:
                try:
                    drv = _os.path.realpath(_os.path.join(d, 'device', 'driver'))
                    if 'uvcvideo' not in drv:
                        continue
                    idx = int(d.rsplit('video', 1)[1])
                except Exception:
                    continue
                temp_cap = None
                try:
                    temp_cap = cv2.VideoCapture(idx)
                    if temp_cap.isOpened():
                        ret, frame = temp_cap.read()
                        if ret and frame is not None:
                            temp_cap.release()
                            debug("Found UVC camera at device ID: %d" % idx)
                            return idx
                except Exception as e:
                    error("Error checking camera %d: %s" % (idx, e))
                finally:
                    if temp_cap is not None:
                        try:
                            if temp_cap.isOpened():
                                temp_cap.release()
                        except Exception:
                            pass
        except Exception as e:
            error("UVC scan failed: %s" % e)
        error("No available camera device found")
        return None

    def start_capture(self, camera_id=None):
        """
        Open the camera and start the capture thread.
        If camera_id is None, an available camera will be searched automatically.
        """
        if camera_id is None:
            camera_id = self.find_available_camera()
            if camera_id is None:
                error("No available camera device found, waiting for camera reconnect")

        debug(f"Starting camera capture on device ID: {camera_id}")

        # Release existing capture if any
        #self._safe_release_capture()

        with self._lock:
            self.camera_id = camera_id
            if self.gesture is None or getattr(self.gesture, "hands", None) is None:
                self.gesture = MediaPipeGestureRecognizer()

        if camera_id is not None:
            self._open_capture(camera_id)

        with self._lock:
            self.running = True
            self.exiting = False
            self.frame_count = 0
            self.fps = 0
            self.last_fps_time = time.time()
            self._last_detect_time = 0.0
            self._last_preview_emit_time = 0.0
            self._last_status_emit_time = 0.0
            self._last_landmarks_res = None
            self._last_landmarks_time = 0.0
            self._last_nonempty_status = {}
            self._last_nonempty_status_time = 0.0
            self._loop_ema_ms = 0.0
            self._last_preview_tune_time = 0.0

        # Start the thread if it is not already running
        try:
            if not self.isRunning():
                self.start()
        except RuntimeError:
            # Log an error if the thread cannot be started (for example, after it has already ended)
            error("Failed to start capture thread")

    def _open_capture(self, camera_id):
        cap = cv2.VideoCapture(camera_id)
        if not (cap and cap.isOpened()):
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            return False

        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 24)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        with self._lock:
            self._release_capture_only()
            self.cap = cap
            self._closed = False
            self.camera_id = camera_id
        debug(f"Camera connected on device ID: {camera_id}")
        return True

    def _release_capture_only(self):
        with self._lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception as e:
                    error(f"Error releasing camera capture: {e}")
                finally:
                    self.cap = None
                    self._closed = True
            else:
                self._closed = True

    def stop_capture(self):
        debug("Stopping camera capture...")
        with self._lock:
            self.exiting = True
            self.running = False

        # Wait for the thread to finish (up to 2 seconds)
        try:
            if self.isRunning():
                self.wait(2000)
        except Exception:
            pass

        # Release resources
        self._safe_release_capture()

    def _safe_release_capture(self):
        """Safely release camera and release gesture resources."""
        try:
            with self._lock:
                if self.cap is not None:
                    try:
                        if not self._closed:
                            debug("Releasing camera capture")
                            self.cap.release()
                    except Exception as e:
                        error(f"Error releasing camera capture: {e}")
                    finally:
                        self.cap = None
                        self._closed = True
                else:
                    self._closed = True
        except Exception as e:
            error(f"Error in _safe_release_capture: {e}")
        finally:
            # Ensure MediaPipe resources are released
            try:
                if self.gesture is not None:
                    try:
                        debug("Releasing gesture detect")
                        self.gesture.close()
                    except Exception:
                        pass
            except Exception:
                pass
            with self._lock:
                self.cap = None
                self._closed = True

    def toggle_detection(self, detecting):
        with self._lock:
            self.detecting = detecting

    def toggle_landmarks(self, show):
        with self._lock:
            self.show_landmarks = show

    def command_display_text(self, cmd):
        labels = {
            'play': 'Play',
            'pause': 'Pause',
            'toggle': 'Play/Pause',
            'seek_forward': 'Seek +5s',
            'seek_back': 'Seek -5s',
            'vol_up': 'Volume +5%',
            'vol_down': 'Volume -5%',
        }
        return labels.get(cmd, 'Waiting')

    def cmd_hud(self, cmd):
        control_state = 'Engaged'
        gesture_cmd = cmd
        if cmd is None:
            if self.frame_remain >= 0:
                self.frame_remain -= 1
                gesture_cmd = self.command_remain
            else:
                control_state = 'Disengaged'
                gesture_cmd = None
        else:
            self.frame_remain = 10
            self.command_remain = cmd
        return f"{control_state} | {self.command_display_text(gesture_cmd)}"

    def run(self):
        self._closed = False
        self.running = True
        self._last_detect_time = 0.0
        read_failures = 0
        try:
            while True:
                with self._lock:
                    should_continue = (not self.exiting) and self.running
                    cap_ready = (self.cap is not None) and (not self._closed)
                    camera_id = self.camera_id

                if not should_continue:
                    break

                if not cap_ready:
                    now = time.time()
                    if now - self._last_reconnect_attempt >= self.reconnect_interval:
                        self._last_reconnect_attempt = now
                        next_camera_id = camera_id
                        if next_camera_id is None:
                            next_camera_id = self.find_available_camera()
                        if next_camera_id is not None:
                            self._open_capture(next_camera_id)
                    time.sleep(0.1)
                    continue

                try:
                    loop_start = time.time()
                    ret, frame = False, None
                    cap_valid = False
                    with self._lock:
                        cap_valid = self.cap is not None and not self._closed

                    if cap_valid:
                        try:
                            ret, frame = self.cap.read()
                        except Exception as e:
                            error(f"Error reading frame: {e}")
                            ret = False

                    if not ret or frame is None:
                        read_failures += 1
                        if read_failures >= 30:
                            error("Cannot read frame from camera, waiting for reconnect")
                            self._release_capture_only()
                            read_failures = 0
                        time.sleep(0.01)
                        continue
                    read_failures = 0

                    # Optional: resize proportionally to reduce downstream processing cost while preserving aspect ratio
                    h, w = frame.shape[:2]
                    if w > self.proc_width:
                        scale = self.proc_width / float(w)
                        new_h = max(1, int(h * scale))
                        try:
                            frame = cv2.resize(frame, (self.proc_width, new_h), interpolation=cv2.INTER_LINEAR)
                        except Exception:
                            # Fall back to the original frame if resizing fails
                            pass

                    # Update and emit FPS occasionally
                    with self._lock:
                        self.frame_count += 1
                        now = time.time()
                        if (now - self.last_fps_time) >= 1.0:
                            try:
                                self.fps = self.frame_count / (now - self.last_fps_time)
                            except Exception:
                                self.fps = 0
                            self.frame_count = 0
                            self.last_fps_time = now
                            try:
                                self.fps_updated.emit(self.fps)
                            except Exception:
                                pass

                    processed_frame = frame

                    # Throttle detection to detection_fps when detection is enabled
                    run_detection = False
                    with self._lock:
                        detecting_enabled = self.detecting
                    if detecting_enabled:
                        now_sec = time.time()
                        if (now_sec - self._last_detect_time) >= (1.0 / max(1.0, self.detection_fps)):
                            run_detection = True
                            self._last_detect_time = now_sec

                    detection_result = {}
                    res = None
                    command = None
                    if run_detection:
                        try:
                            result = None
                            try:
                                result = self.gesture.process_frame(processed_frame)
                            except TypeError:
                                result = None

                            if isinstance(result, tuple) and len(result) >= 1:
                                detection_result = result[0] or {}
                                res = result[1] if len(result) > 1 else None
                            elif isinstance(result, dict):
                                detection_result = result or {}
                            else:
                                detection_result = {}

                            now_status = time.time()
                            if detection_result:
                                self._last_nonempty_status = detection_result
                                self._last_nonempty_status_time = now_status
                            if res is not None:
                                self._last_landmarks_res = res
                                self._last_landmarks_time = now_status

                            now_emit = time.time()
                            if (now_emit - self._last_status_emit_time) >= (1.0 / max(1.0, self.status_emit_fps)):
                                status_to_emit = detection_result or {}
                                if not status_to_emit:
                                    if (now_emit - self._last_nonempty_status_time) <= self.status_hold_sec:
                                        status_to_emit = self._last_nonempty_status
                                try:
                                    self.detection_status.emit(status_to_emit)
                                except Exception:
                                    pass
                                self._last_status_emit_time = now_emit

                            command = detection_result.get('cmd', None)
                            now_cmd_time = time.time()
                            repeat_interval = self.command_repeat_intervals.get(command, self.command_repeat_interval)
                            should_emit = (
                                command and (
                                    command != self.last_command or
                                    (now_cmd_time - self.last_command_time) >= repeat_interval
                                )
                            )
                            if should_emit:
                                debug(f"Command detected: {command}")
                                try:
                                    self.command_detected.emit(command)
                                except Exception:
                                    pass
                                with self._lock:
                                    self.last_command = command
                                    self.last_command_time = now_cmd_time
                            elif not command:
                                # Clear on no command to avoid suppressing later gestures of the same type
                                with self._lock:
                                    self.last_command = None
                        except Exception as e:
                            error(f"Gesture detection error: {e}")
                            now_emit = time.time()
                            if (now_emit - self._last_status_emit_time) >= (1.0 / max(1.0, self.status_emit_fps)):
                                status_to_emit = {}
                                if (now_emit - self._last_nonempty_status_time) <= self.status_hold_sec:
                                    status_to_emit = self._last_nonempty_status
                                try:
                                    self.detection_status.emit(status_to_emit)
                                except Exception:
                                    pass
                                self._last_status_emit_time = now_emit
                    else:
                        now_emit = time.time()
                        if (now_emit - self._last_status_emit_time) >= (1.0 / max(1.0, self.status_emit_fps)):
                            status_to_emit = {}
                            if (now_emit - self._last_nonempty_status_time) <= self.status_hold_sec:
                                status_to_emit = self._last_nonempty_status
                            try:
                                self.detection_status.emit(status_to_emit)
                            except Exception:
                                pass
                            self._last_status_emit_time = now_emit

                    now_preview = time.time()
                    should_emit_preview = (now_preview - self._last_preview_emit_time) >= (1.0 / max(1.0, self.preview_emit_fps))
                    if should_emit_preview:
                        # Draw the latest landmarks when emitting preview frames to reduce flicker and control cost
                        show_landmarks = False
                        with self._lock:
                            show_landmarks = self.show_landmarks
                        if show_landmarks:
                            if self._last_landmarks_res is not None and (now_preview - self._last_landmarks_time) <= self.landmarks_hold_sec:
                                try:
                                    self.gesture.draw_landmarks(processed_frame, self._last_landmarks_res)
                                except Exception as e:
                                    error(f"draw landmarks err: {e}")

                        # Use mirrored preview so the user does not perceive left/right reversal.
                        display_frame = cv2.flip(processed_frame, 1) if self.mirror_preview else processed_frame

                        remain_cmd = self.cmd_hud(command)
                        hud_text = f" {remain_cmd}"
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        font_scale = 0.8
                        thickness = 2
                        text_x, text_y = 10, 38
                        (text_w, text_h), baseline = cv2.getTextSize(hud_text, font, font_scale, thickness)
                        pad_x, pad_y = 8, 8
                        box_x1 = max(0, text_x - pad_x)
                        box_y1 = max(0, text_y - text_h - pad_y)
                        box_x2 = min(display_frame.shape[1] - 1, text_x + text_w + pad_x)
                        box_y2 = min(display_frame.shape[0] - 1, text_y + baseline + pad_y)

                        cv2.rectangle(display_frame, (box_x1, box_y1), (box_x2, box_y2), (60, 60, 60), -1)
                        cv2.putText(display_frame, hud_text, (text_x, text_y),
                            font, font_scale, (0, 200, 200), thickness)
                        try:
                            self.frame_ready.emit(display_frame)
                        except Exception:
                            pass
                        self._last_preview_emit_time = now_preview

                    # Estimate capture-loop load and fine-tune preview FPS (at most 1 FPS adjustment per second)
                    loop_ms = (time.time() - loop_start) * 1000.0
                    if self._loop_ema_ms <= 0.0:
                        self._loop_ema_ms = loop_ms
                    else:
                        self._loop_ema_ms = self._loop_ema_ms * 0.9 + loop_ms * 0.1

                    if self.adaptive_preview_fps:
                        now_tune = time.time()
                        if (now_tune - self._last_preview_tune_time) >= 1.0:
                            # Empirical thresholds: >36 ms means load is high; <24 ms means there is headroom
                            if self._loop_ema_ms > 36.0 and self.preview_emit_fps > self.preview_emit_fps_min:
                                self.preview_emit_fps -= 1
                            elif self._loop_ema_ms < 24.0 and self.preview_emit_fps < self.preview_emit_fps_max:
                                self.preview_emit_fps += 1
                            self._last_preview_tune_time = now_tune

                    # Sleep briefly to yield CPU time and avoid a tight loop
                    time.sleep(0.001)
                except Exception as e:
                    error(f"Error in camera capture loop: {e}")
                    # Exit the loop on serious errors so resources can be released
                    break
        finally:
            # Ensure resources are released when the thread exits
            self._safe_release_capture()
            try:
                self.finished.emit()
            except Exception:
                pass

    def __del__(self):
        # Ensure release
        try:
            self.stop_capture()
        except Exception:
            pass