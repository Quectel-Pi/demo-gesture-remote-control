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

        # Component initialization
        # MediaPipeGestureRecognizer will be created once and reused
        self.gesture = MediaPipeGestureRecognizer()

        # FPS calculation
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()
        self.last_command = None
        self.last_command_time = 0.0
        self.command_repeat_interval = 0.35
        self.command_repeat_intervals = {
            'seek_forward': 0.12,
            'seek_back': 0.12,
            'toggle': 0.30,
            'vol_up': 0.18,
            'vol_down': 0.18,
        }

        # Processing config: 限制处理分辨率 & 检测频率（可在 UI 配置）
        self.proc_width = 640  # 将输入缩放到宽度 640（可调：480/640/960）
        self.detection_fps = 15  # 手势检测的目标频率（FPS）
        self._last_detect_time = 0.0  # time.time() 单位秒

        self.frame_remain = 0
        self.command_remain = ''

    def find_available_camera(self):
        """自动检测可用摄像头设备，返回设备 id 或 None。"""
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
        error("No available camera device found")
        return None

    def start_capture(self, camera_id=None):
        """
        打开摄像头并启动抓帧线程。
        如果 camera_id 为 None，则会自动查找可用摄像头。
        """
        if camera_id is None:
            camera_id = self.find_available_camera()
            if camera_id is None:
                raise Exception("No available camera device found")

        debug(f"Starting camera capture on device ID: {camera_id}")

        # Release existing capture if any
        #self._safe_release_capture()

        with self._lock:
            self.cap = cv2.VideoCapture(camera_id)
            if not (self.cap and self.cap.isOpened()):
                # 尝试释放并报错
                try:
                    if self.cap is not None:
                        self.cap.release()
                except Exception:
                    pass
                self.cap = None
                raise Exception(f"Cannot open camera device {camera_id}")

            # 尝试设置合适的摄像头参数（视驱动支持情况）
            try:
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                self.cap.set(cv2.CAP_PROP_FPS, 30)
            except Exception:
                pass

            self._closed = False

        with self._lock:
            self.running = True
            self.exiting = False
            self.frame_count = 0
            self.fps = 0
            self.last_fps_time = time.time()
            self._last_detect_time = 0.0

        # 启动线程（如果尚未运行）
        try:
            if not self.isRunning():
                self.start()
        except RuntimeError:
            # 如果线程无法启动（例如已结束），记录错误
            error("Failed to start capture thread")

    def stop_capture(self):
        debug("Stopping camera capture...")
        with self._lock:
            self.exiting = True
            self.running = False

        # 等待线程结束（最多 2 秒）
        try:
            if self.isRunning():
                self.wait(2000)
        except Exception:
            pass

        # 释放资源
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
            # 确保释放 MediaPipe 资源
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

    def cmd_hud(self, cmd):
        gesture_cmd = cmd
        if cmd is None:
            if self.frame_remain >= 0:
                self.frame_remain -= 1
                gesture_cmd = self.command_remain
            else:
                return None
        else:
            self.frame_remain = 5
            self.command_remain = cmd
        return gesture_cmd

    def run(self):
        self._closed = False
        self.running = True
        self._last_detect_time = 0.0
        try:
            while True:
                with self._lock:
                    should_continue = (not self.exiting) and self.running and (self.cap is not None) and (not self._closed)

                if not should_continue:
                    break

                try:
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
                        # 如果读帧失败，稍等并重试
                        time.sleep(0.01)
                        continue

                    # 可选：按比例缩放以减少后续计算量（保持纵横比）
                    h, w = frame.shape[:2]
                    if w > self.proc_width:
                        scale = self.proc_width / float(w)
                        new_h = max(1, int(h * scale))
                        try:
                            frame = cv2.resize(frame, (self.proc_width, new_h), interpolation=cv2.INTER_LINEAR)
                        except Exception:
                            # 如果缩放失败，使用原始帧
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

                    processed_frame = frame.copy()

                    # 如果检测被启用，则按 detection_fps 做节流
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
                            # 支持两种 process_frame 签名：
                            # - 返回 detection_result dict（旧签名）
                            # - 返回 (detection_result, res) tuple（新签名，res 用于绘制）
                            result = None
                            try:
                                result = self.gesture.process_frame(processed_frame)
                            except TypeError:
                                # 如果手势处理函数需要不同的参数或抛错，捕获并将 result 设为 None
                                result = None

                            if isinstance(result, tuple) and len(result) >= 1:
                                detection_result = result[0] or {}
                                res = result[1] if len(result) > 1 else None
                            elif isinstance(result, dict):
                                detection_result = result or {}
                            else:
                                detection_result = {}

                            try:
                                self.detection_status.emit(detection_result or {})
                            except Exception:
                                pass

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
                                # 无命令时清空，避免后续同类手势被长期吞掉
                                with self._lock:
                                    self.last_command = None
                        except Exception as e:
                            error(f"Gesture detection error: {e}")
                            try:
                                self.detection_status.emit({})
                            except Exception:
                                pass
                    else:
                        # 不做检测时仍发出空状态，保证 UI 能收到 frame
                        try:
                            self.detection_status.emit({})
                        except Exception:
                            pass

                    # 绘制 landmarks：不要每帧创建新的 mp.solutions.hands.Hands()
                    show_landmarks = False
                    with self._lock:
                        show_landmarks = self.show_landmarks
                    if show_landmarks:
                        if res is not None:
                            try:
                                self.gesture.draw_landmarks(processed_frame, res)
                            except Exception as e:
                                error(f"draw landmarks err: {e}")

                    remain_cmd = self.cmd_hud(command)
                    if remain_cmd:
                        cv2.putText(processed_frame, f"cmd: {remain_cmd}", (10, 28 + 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 200), 2)
                    # 将处理后的帧发回 UI（QLabel 显示等）
                    try:
                        self.frame_ready.emit(processed_frame)
                    except Exception:
                        pass

                    # 稍微睡眠以让出 CPU（避免 tight loop）
                    time.sleep(0.001)
                except Exception as e:
                    error(f"Error in camera capture loop: {e}")
                    # 在出现严重错误时退出循环，以便释放资源
                    break
        finally:
            # 线程退出时确保资源释放
            self._safe_release_capture()
            try:
                self.finished.emit()
            except Exception:
                pass

    def __del__(self):
        # 确保释放
        try:
            self.stop_capture()
        except Exception:
            pass