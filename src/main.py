import os
import sys
import cv2
import time
os.environ["QT_LOGGING_RULES"] = "qt.pointer.dispatch=false"

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, 
    QHBoxLayout, QFileDialog, QMessageBox, QGroupBox, QCheckBox, QFrame,
    QSplitter, QGridLayout, QSlider,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap

from gesture_recognizer import MediaPipeGestureRecognizer
from video_capture import VideoCaptureThread
from video_player import VideoPlayerThread
from fullscreen_player_mode import FullScreenPlayer
from log  import error

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.video_player_thread = VideoPlayerThread()
        self.video_thread = VideoCaptureThread()
        self.current_video_file = ""
        self.video_loaded = False
        self.camera_active = False
        self.is_fullscreen = False
        self.video_duration = 0
        self.video_position = 0
        self.is_slider_pressed = False
        self.last_control_command = None
        self.last_gesture_display_until_ms = 0
        self.gesture_display_hold_ms = 2000
        self.gesture_command_locked_until_ms = 0
        
         # Fullscreen player window
        self.fullscreen_player = None
        self.is_in_fullscreen_mode = False
        
        # Connect signals
        self.video_thread.frame_ready.connect(self.update_camera_frame)
        self.video_thread.command_detected.connect(self.handle_command)
        self.video_thread.detection_status.connect(self.update_detection_status)
        self.video_thread.fps_updated.connect(self.update_fps_display)
        self.video_thread.finished.connect(self.on_video_stopped)
        
        self.video_player_thread.frame_ready.connect(self.update_video_frame)
        self.video_player_thread.playback_finished.connect(self.on_playback_finished)
        self.video_player_thread.video_info_ready.connect(self.update_video_info)

        # Setup styles
        self.setup_styles()
        
        self.init_ui()
        self.auto_start_camera()
        
        # Start video player thread
        self.video_player_thread.start()
        
        # Video progress update timer
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress)
        self.progress_timer.start(100)
        
    def setup_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e2e;
            }
            QLabel {
                color: #cdd6f4;
            }
            QGroupBox {
                color: #89b4fa;
                font-weight: bold;
                border: 2px solid #585b70;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #313244;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #585b70;
                color: #cdd6f4;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6c7086;
            }
            QPushButton:pressed {
                background-color: #45475a;
            }
            QPushButton:disabled {
                background-color: #313244;
                color: #7f849c;
            }
            QCheckBox {
                color: #cdd6f4;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #585b70;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa;
                border-color: #89b4fa;
            }
            QFrame#status_frame {
                background-color: #313244;
                border-radius: 8px;
                border: 1px solid #585b70;
            }
            QLabel#status_value {
                font-weight: bold;
                padding: 2px 8px;
                border-radius: 4px;
            }
            QSlider {
                min-height: 20px;
            }
            QSlider::groove:horizontal {
                border: 1px solid #585b70;
                height: 8px;
                background: #313244;
                margin: 0px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #89b4fa;
                border: 1px solid #5c81e3;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #89b4fa;
                border: 1px solid #5c81e3;
                height: 8px;
                border-radius: 4px;
            }
            QProgressBar {
                border: 1px solid #585b70;
                border-radius: 4px;
                text-align: center;
                background-color: #313244;
            }
            QProgressBar::chunk {
                background-color: #89b4fa;
                border-radius: 4px;
            }
        """)
        
    def init_ui(self):
        self.setWindowTitle('🖐️ Gesture Remote Control')
        # use adaptive sizing based on screen dimensions
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()
        
        # Set window to 80% width and 85% height of screen size
        window_width = int(screen_width * 0.8)
        window_height = int(screen_height * 0.85)
        self.setGeometry(
            (screen_width - window_width) // 2,
            (screen_height - window_height) // 2,
            window_width,
            window_height
        )
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)
        
        # Fullscreen button
        self.fullscreen_btn = QPushButton("Fullscreen")
        self.fullscreen_btn.setFixedSize(max(90, int(window_width * 0.14)), 30)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.fullscreen_btn.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #1e1e2e;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #74c7ec;
            }
        """)
         #  Fullscreen play button
        self.fullscreen_play_btn = QPushButton(" Fullscreen Play Mode")
        self.fullscreen_play_btn.setFixedSize(max(130, int(window_width * 0.22)), 30)
        self.fullscreen_play_btn.clicked.connect(self.enter_fullscreen_play_mode)
        self.fullscreen_play_btn.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #1e1e2e;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #74c7ec;
            }
        """)
        
        # Main content area - horizontal split
        content_splitter = QSplitter(Qt.Horizontal)
        
        # Left side - video display area
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(10)
        
        # Camera display area
        camera_group = QGroupBox("📷 Camera Feed")
        camera_layout = QVBoxLayout()
        
        self.camera_display = QLabel("Starting camera...")
        self.camera_display.setAlignment(Qt.AlignCenter)
        # Use relative dimensions instead of fixed sizes
        self.camera_display.setMinimumSize(int(window_width * 0.4), int(window_height * 0.26))
        self.camera_display.setStyleSheet("""
            QLabel {
                background-color: #000000;
                border-radius: 8px;
                border: 2px solid #585b70;
                color: #ffffff;
                font-size: 14px;
            }
        """)
        
        camera_layout.addWidget(self.camera_display)
        camera_group.setLayout(camera_layout)
        left_layout.addWidget(camera_group)
        
        # Video playback area
        video_group = QGroupBox("🎬 Video Player")
        video_layout = QVBoxLayout()
        
        self.video_display = QLabel("Click to select a video file")
        self.video_display.setAlignment(Qt.AlignCenter)
        # Use relative dimensions instead of fixed sizes
        self.video_display.setMinimumSize(int(window_width * 0.4), int(window_height * 0.26))
        self.video_display.setStyleSheet("""
            QLabel {
                background-color: #000000;
                border-radius: 8px;
                border: 2px solid #585b70;
                color: #ffffff;
                font-size: 14px;
            }
        """)
        self.video_display.setCursor(Qt.PointingHandCursor)
        self.video_display.mousePressEvent = self.open_video_from_display
        
        # Video controls
        video_controls = QWidget()
        video_controls_layout = QVBoxLayout(video_controls)
        
        # Progress slider
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderMoved.connect(self.on_progress_slider_moved)
        self.progress_slider.sliderPressed.connect(self.on_progress_slider_pressed)
        self.progress_slider.sliderReleased.connect(self.on_progress_slider_released)
        
        # Time display and buttons
        control_row = QWidget()
        control_layout = QHBoxLayout(control_row)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        
        self.video_play_btn = QPushButton("Play")
        self.video_play_btn.clicked.connect(self.play_video)
        self.video_play_btn.setFixedSize(max(58, int(window_width * 0.08)), 30)
        
        self.video_pause_btn = QPushButton("Pause")
        self.video_pause_btn.clicked.connect(self.pause_video)
        self.video_pause_btn.setFixedSize(max(58, int(window_width * 0.08)), 30)
        
        self.video_stop_btn = QPushButton("Stop")
        self.video_stop_btn.clicked.connect(self.stop_video)
        self.video_stop_btn.setFixedSize(max(58, int(window_width * 0.08)), 30)
        
        control_layout.addWidget(self.time_label)
        control_layout.addStretch()
        control_layout.addWidget(self.video_play_btn)
        control_layout.addWidget(self.video_pause_btn)
        control_layout.addWidget(self.video_stop_btn)
        
        video_controls_layout.addWidget(self.progress_slider)
        video_controls_layout.addWidget(control_row)
        
        video_layout.addWidget(self.video_display)
        video_layout.addWidget(video_controls)
        video_group.setLayout(video_layout)
        left_layout.addWidget(video_group)
        
        # Right side - control panel
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)

        screen_show_group = QGroupBox()
        screen_show_layout = QGridLayout()
        screen_show_layout.setContentsMargins(6, 4, 6, 4)
        screen_show_layout.setColumnStretch(0, 1)
        screen_show_layout.setColumnStretch(1, 1)
        screen_show_layout.addWidget(self.fullscreen_play_btn, 0, 0)
        screen_show_layout.addWidget(self.fullscreen_btn, 0, 1)
        screen_show_group.setLayout(screen_show_layout)
        right_layout.addWidget(screen_show_group)
        # file_info_layout = QVBoxLayout()
        
        # Real-time status display
        status_group = QGroupBox("📊 System Status")
        status_layout = QGridLayout()
        status_layout.setVerticalSpacing(4)
        
        # Camera status
        cam_status_label = QLabel("📷 Camera:")
        cam_status_label.setStyleSheet("color: #a6adc8;")
        
        self.cam_status = QLabel("Running")
        self.cam_status.setObjectName("status_value")
        self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        self.cam_status.setFixedSize(int(window_width * 0.1), 25)  # Use relative width
        
        # FPS display
        fps_label = QLabel("⚡ Camera FPS:")
        fps_label.setStyleSheet("color: #a6adc8;")
        
        self.fps_display = QLabel("0.0")
        self.fps_display.setObjectName("status_value")
        self.fps_display.setStyleSheet("background-color: #cba6f7; color: #000000;")
        self.fps_display.setFixedSize(120, 25)  # Fixed size to prevent layout changes
        
        # Detection status
        detect_status_label = QLabel("🔍 Detection Status:")
        detect_status_label.setStyleSheet("color: #a6adc8;")
        
        self.detect_status = QLabel("Detecting...")
        self.detect_status.setObjectName("status_value")
        self.detect_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
        self.detect_status.setFixedSize(120, 25)  # Fixed size to prevent layout changes
        
        # Video playback status
        video_status_label = QLabel("▶️ Video Status:")
        video_status_label.setStyleSheet("color: #a6adc8;")
        
        self.video_status = QLabel("Not Loaded")
        self.video_status.setObjectName("status_value")
        self.video_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
        self.video_status.setFixedSize(120, 25)  # Fixed size to prevent layout changes

        # Gesture control status
        gesture_mode_label = QLabel("🎛️ Gesture Mode:")
        gesture_mode_label.setStyleSheet("color: #a6adc8;")

        self.gesture_mode_status = QLabel("Inactive")
        self.gesture_mode_status.setObjectName("status_value")
        self.gesture_mode_status.setStyleSheet("background-color: #585b70; color: #ffffff;")
        self.gesture_mode_status.setFixedSize(120, 25)

        gesture_action_label = QLabel("🖐️ Current Gesture:")
        gesture_action_label.setStyleSheet("color: #a6adc8;")

        self.gesture_action_status = QLabel("Waiting")
        self.gesture_action_status.setObjectName("status_value")
        self.gesture_action_status.setStyleSheet("background-color: #585b70; color: #ffffff;")
        self.gesture_action_status.setFixedSize(120, 25)
        
        # Add to grid layout
        status_layout.addWidget(cam_status_label, 0, 0)
        status_layout.addWidget(self.cam_status, 0, 1)
        status_layout.addWidget(fps_label, 0, 2)
        status_layout.addWidget(self.fps_display, 0, 3)
        
        status_layout.addWidget(detect_status_label, 1, 0)
        status_layout.addWidget(self.detect_status, 1, 1)
        status_layout.addWidget(video_status_label, 1, 2)
        status_layout.addWidget(self.video_status, 1, 3)

        status_layout.addWidget(gesture_mode_label, 2, 0)
        status_layout.addWidget(self.gesture_mode_status, 2, 1)
        status_layout.addWidget(gesture_action_label, 2, 2)
        status_layout.addWidget(self.gesture_action_status, 2, 3)
        
        status_group.setLayout(status_layout)
        right_layout.addWidget(status_group)
        # right_layout.addWidget(file_info_group)
        
        # Control instructions
        instruction_group = QGroupBox("📋 Control Instructions")
        instruction_layout = QVBoxLayout()
        instruction_layout.setContentsMargins(6, 4, 6, 4)
        
        instructions = QLabel(
            "<b>Gesture Control Commands:</b><br>"
            "• Open palm (5 fingers) → Play<br>"
            "• Fist → Pause<br>"
            "• Swipe right → Fast forward 5 seconds<br>"
            "• Swipe left → Rewind 5 seconds<br>"
            "• Swipe up → Volume +5%<br>"
            "• Swipe down → Volume -5%<br>"
            "<b>Note:</b><br>"
            "• Keep your hand within the camera view<br>"
            "• Perform gestures clearly and steadily<br>"
            "• Ensure adequate lighting"
        )

        instructions.setStyleSheet("color: #cdd6f4; padding: 2px;")
        instructions.setWordWrap(True)
        
        instruction_layout.addWidget(instructions)
        instruction_group.setLayout(instruction_layout)
        right_layout.addWidget(instruction_group)
        
        # Camera controls
        camera_control_group = QGroupBox("🎮 Camera Controls")
        camera_control_layout = QVBoxLayout()
        camera_control_layout.setContentsMargins(6, 4, 6, 4)
        camera_control_layout.setSpacing(4)
        
        self.camera_toggle_btn = QPushButton("Turn Off Camera")
        self.camera_toggle_btn.clicked.connect(self.toggle_camera)
        self.camera_toggle_btn.setFixedHeight(30)
        
        self.detect_checkbox = QCheckBox('Enable Gesture Detection')
        self.detect_checkbox.setChecked(True)
        self.detect_checkbox.stateChanged.connect(self.toggle_detection)
        
        self.landmarks_checkbox = QCheckBox("Show Landmarks")
        self.landmarks_checkbox.setChecked(True)
        self.landmarks_checkbox.stateChanged.connect(self.toggle_landmarks)
        
        camera_control_layout.addWidget(self.camera_toggle_btn)
        camera_control_layout.addWidget(self.detect_checkbox)
        camera_control_layout.addWidget(self.landmarks_checkbox)
        camera_control_group.setLayout(camera_control_layout)
        right_layout.addWidget(camera_control_group)
        
        # Video file controls
        file_control_group = QGroupBox("📁 Video File Controls")
        file_control_layout = QVBoxLayout()
        file_control_layout.setContentsMargins(6, 4, 6, 4)
        
        self.select_video_btn = QPushButton("Select Video File")
        self.select_video_btn.clicked.connect(self.select_video)
        self.select_video_btn.setFixedHeight(32)
        
        file_control_layout.addWidget(self.select_video_btn)
        file_control_group.setLayout(file_control_layout)
        right_layout.addWidget(file_control_group)
        
        # Add to splitter
        content_splitter.addWidget(left_widget)
        content_splitter.addWidget(right_widget)
        content_splitter.setSizes([900, 500])
        
        main_layout.addWidget(content_splitter)
        
        # Setup fullscreen shortcut
        self.fullscreen_btn.setShortcut("F11")

    def _set_badge(self, label, text, background, color="#000000"):
        label.setText(text)
        label.setStyleSheet(f"background-color: {background}; color: {color};")

    def command_display_text(self, command):
        labels = {
            "play": "Play",
            "pause": "Pause",
            "toggle": "Play/Pause",
            "seek_forward": "Fast Forward 5s",
            "seek_back": "Rewind 5s",
            "vol_up": "Volume +5%",
            "vol_down": "Volume -5%",
        }
        return labels.get(command, "Waiting")

    def control_mode_text(self, command):
        if command in ("vol_up", "vol_down"):
            return "Volume Control", "#cba6f7"
        if command in ("seek_forward", "seek_back"):
            return "Seek Control", "#74c7ec"
        if command in ("play", "pause", "toggle"):
            return "Playback Control", "#89b4fa"
        return "Waiting", "#f9e2af"

    def open_video_from_display(self, event):
        if event.button() == Qt.LeftButton:
            self.select_video()
        
    def auto_start_camera(self):
        try:
            self.video_thread.start_capture()
            self.camera_active = True
            self.camera_toggle_btn.setText("Turn Off Camera")
            self.cam_status.setText("Running")
            self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        except Exception as e:
            self.cam_status.setText("Failed to Start")
            self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            QMessageBox.critical(self, "Error", f"Cannot auto-start camera: {str(e)}")
            
    def toggle_camera(self):
        if self.camera_active:
            self.stop_camera()
        else:
            self.start_camera()
            
    def start_camera(self):
        try:
            self.video_thread.start_capture()
            self.camera_active = True
            self.camera_toggle_btn.setText("Turn Off Camera")
            self.cam_status.setText("Running")
            self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
            # When camera starts, also update detection status if detection is enabled
            if self.detect_checkbox.isChecked():
                self.detect_status.setText("Detecting")
                self.detect_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        except Exception as e:
            self.cam_status.setText("Failed to Start")
            self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            QMessageBox.critical(self, "Error", f"Cannot start camera: {str(e)}")
            
    def stop_camera(self):
        self.video_thread.stop_capture()
        self.camera_active = False
        self.camera_toggle_btn.setText("Start Camera")
        self.cam_status.setText("Stopped")
        self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
        self.camera_display.setText("Camera Stopped")
        self.camera_display.setPixmap(QPixmap())
        # When camera stops, update detection status
        self.detect_status.setText("Camera Off")
        self.detect_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
        
        # Also stop video playback when camera is turned off
        if self.video_loaded:
            self.video_player_thread.stop()
            self.video_status.setText("Stopped")
            self.video_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            self.progress_slider.setValue(0)
            if hasattr(self, 'video_duration'):
                self.update_time_label(0, self.video_duration)
    def on_video_stopped(self):
        self.camera_active = False
        
    def toggle_detection(self, state):
        is_detecting = state == Qt.CheckState.Checked.value
        self.video_thread.toggle_detection(is_detecting)
        if is_detecting:
            self.detect_status.setText("Detecting")
            self.detect_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        else:
            self.detect_status.setText("Disabled")
            self.detect_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
        
    def toggle_landmarks(self, state):
        self.video_thread.toggle_landmarks(state == Qt.CheckState.Checked.value)
        
    def select_video(self):
        default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
        if self.current_video_file:
            current_dir = os.path.dirname(self.current_video_file)
            if os.path.isdir(current_dir):
                default_dir = current_dir
        if not os.path.isdir(default_dir):
            default_dir = os.getcwd()

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video File", default_dir, "Video Files (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.MP4 *.AVI *.MOV *.MKV *.FLV *.WMV)")
        
        if file_path:
            self.current_video_file = file_path
            
            # Stop any currently playing video
            if self.video_loaded:
                self.video_player_thread.stop()
            
            if self.video_player_thread.load_video(file_path):
                self.video_loaded = True
                self.video_status.setText("Loaded")
                self.video_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
                # Display first frame
                cap = cv2.VideoCapture(file_path)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret:
                        self.display_video_frame(frame)
                    cap.release()
                
                # Reset progress slider and time label to start
                self.progress_slider.setValue(0)
                if hasattr(self, 'video_duration'):
                    self.update_time_label(0, self.video_duration)
            else:
                self.video_loaded = False
                self.video_status.setText("Load Failed")
                self.video_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
                QMessageBox.warning(self, "Failure", f"Cannot load video: {os.path.basename(file_path)}")
                
    def update_video_info(self, video_info):
        """Update video information display"""
        filename = video_info['filename']
        width = video_info['width']
        height = video_info['height']
        fps = video_info['fps']
        duration = video_info['duration']
        
        # Update time display
        self.video_duration = duration
        self.update_time_label(0, duration)
        
    def handle_command(self, command):
        """Handle detection command"""
        if not command:
            return
        now_ms = int(time.time() * 1000)
        if now_ms < self.gesture_command_locked_until_ms:
            return

        self.last_control_command = command
        self.last_gesture_display_until_ms = now_ms + self.gesture_display_hold_ms
        self.gesture_command_locked_until_ms = self.last_gesture_display_until_ms

        # 播放/暂停控制
        if command in ("play", "pause", "toggle"):
            if self.video_loaded:
                should_pause = command == "pause" or (
                    command == "toggle" and self.video_player_thread.playing and not self.video_player_thread.paused
                )
                if should_pause:
                    self.pause_video()
                    if self.is_in_fullscreen_mode and self.fullscreen_player:
                        self.fullscreen_player.play_pause_btn.setText("Play")
                        self.fullscreen_player.show_overlays(playback_text="Paused")
                else:
                    self.play_video()
                    if self.is_in_fullscreen_mode and self.fullscreen_player:
                        self.fullscreen_player.play_pause_btn.setText("Pause")
                        self.fullscreen_player.show_overlays(playback_text="Playing")
            return
        # 快进/快退（默认 5 秒）
        if command in ("seek_forward", "seek_back") and self.video_loaded:
            try:
                delta = 5.0 if command == "seek_forward" else -5.0
                cur_pos = self.video_player_thread.get_position() * self.video_duration
                new_pos = max(0.0, min(self.video_duration, cur_pos + delta))
                target_frame = int((new_pos / self.video_duration) * self.video_player_thread.total_frames) \
                                if self.video_duration > 0 else self.video_player_thread.current_frame
                self.video_player_thread.seek(target_frame)
                # 立即刷新暂停态下的 UI 反馈（进度条与时间）
                position = self.video_player_thread.get_position()
                self.progress_slider.setValue(int(position * 1000))
                self.update_time_label(position * self.video_duration, self.video_duration)
                self.statusBar().showMessage(f"Seek to {int(new_pos)}s")
            except Exception as e:
                error(f"seek error (prep): {e}")
            return
        # 音量（系统 PulseAudio）
        if command in ("vol_up", "vol_down"):
            try:
                step = "+5%" if command == "vol_up" else "-5%"
                import subprocess
                subprocess.run(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', step],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.statusBar().showMessage("Volume adjusted")
            except Exception as e:
                error(f"volume error: {e}")
            return

    def play_video(self):
        if self.video_loaded:
            self.video_player_thread.play()
            self.video_status.setText("Playing")
            self.video_status.setStyleSheet("background-color: #89b4fa; color: #000000;")
                
    def pause_video(self):
        if self.video_loaded:
            self.video_player_thread.pause()
            self.video_status.setText("Paused")
            self.video_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
            
    def stop_video(self):
        if self.video_loaded:
            self.video_player_thread.stop()
            self.video_status.setText("Stopped")
            self.video_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            self.progress_slider.setValue(0)
            self.update_time_label(0, self.video_duration)
            
    def update_camera_frame(self, frame):
        self.display_frame(self.camera_display, frame)
        
    def update_video_frame(self, frame):
        self.display_video_frame(frame)
        
    def display_frame(self, label, frame):
        """Display frame to specified label"""
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qt_image)
        
        scaled_pixmap = pixmap.scaled(
            label.size(), 
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        label.setPixmap(scaled_pixmap)
        
    def display_video_frame(self, frame):
        """Display video frame"""
        self.display_frame(self.video_display, frame)

    def update_detection_status(self, detection_result):
        """Update detection status (hand & gesture)"""
        now_ms = int(time.time() * 1000)
        if not detection_result:
            if self.last_control_command and now_ms < self.last_gesture_display_until_ms:
                mode_text, mode_color = self.control_mode_text(self.last_control_command)
                self._set_badge(self.gesture_mode_status, mode_text, mode_color)
                self._set_badge(self.gesture_action_status, self.command_display_text(self.last_control_command), "#a6e3a1")
                return

            if self.camera_active and self.detect_checkbox.isChecked():
                self._set_badge(self.detect_status, "Waiting", "#f9e2af")
            self._set_badge(self.gesture_mode_status, "Inactive", "#585b70", "#ffffff")
            self._set_badge(self.gesture_action_status, "Waiting", "#585b70", "#ffffff")
            return

        hand_present = detection_result.get('hand_present', False)
        command = detection_result.get('cmd', None)

        if not hand_present:
            self._set_badge(self.detect_status, "No Hand", "#f38ba8")
            if self.last_control_command and now_ms < self.last_gesture_display_until_ms:
                mode_text, mode_color = self.control_mode_text(self.last_control_command)
                self._set_badge(self.gesture_mode_status, mode_text, mode_color)
                self._set_badge(self.gesture_action_status, self.command_display_text(self.last_control_command), "#a6e3a1")
                return

            self._set_badge(self.gesture_mode_status, "Inactive", "#585b70", "#ffffff")
            self._set_badge(self.gesture_action_status, "Waiting", "#585b70", "#ffffff")
            return

        self._set_badge(self.detect_status, "Gesture Active", "#a6e3a1")
        if command:
            if now_ms < self.gesture_command_locked_until_ms:
                if self.last_control_command and now_ms < self.last_gesture_display_until_ms:
                    mode_text, mode_color = self.control_mode_text(self.last_control_command)
                    self._set_badge(self.gesture_mode_status, mode_text, mode_color)
                    self._set_badge(self.gesture_action_status, self.command_display_text(self.last_control_command), "#a6e3a1")
                return

            mode_text, mode_color = self.control_mode_text(command)
            self.last_control_command = command
            self.last_gesture_display_until_ms = now_ms + self.gesture_display_hold_ms
            self._set_badge(self.gesture_mode_status, mode_text, mode_color)
            self._set_badge(self.gesture_action_status, self.command_display_text(command), "#a6e3a1")
        else:
            if self.last_control_command and now_ms < self.last_gesture_display_until_ms:
                mode_text, mode_color = self.control_mode_text(self.last_control_command)
                self._set_badge(self.gesture_mode_status, mode_text, mode_color)
                self._set_badge(self.gesture_action_status, self.command_display_text(self.last_control_command), "#a6e3a1")
            else:
                neutral_text = "Done, Waiting" if self.last_control_command else "Waiting"
                self._set_badge(self.gesture_mode_status, neutral_text, "#f9e2af")
                self._set_badge(self.gesture_action_status, "Waiting", "#f9e2af")
        
    def update_fps_display(self, fps):
        """Update FPS display"""
        self.fps_display.setText(f"{fps:.1f}")
        
    def update_progress(self):
        """Update progress bar"""
        if self.video_loaded and self.video_player_thread.playing and not self.video_player_thread.paused:
            position = self.video_player_thread.get_position()
            self.progress_slider.setValue(int(position * 1000))
            
            # Update time display
            current_time = position * self.video_duration
            self.update_time_label(current_time, self.video_duration)
        
    def update_time_label(self, current_time, total_time):
        """Update time display label"""
        current_str = f"{int(current_time // 60):02d}:{int(current_time % 60):02d}"
        total_str = f"{int(total_time // 60):02d}:{int(total_time % 60):02d}"
        self.time_label.setText(f"{current_str} / {total_str}")
        
    def on_progress_slider_moved(self, value):
        """Progress slider moved event"""
        if self.video_loaded and not self.is_slider_pressed:
            position = value / 1000.0
            target_frame = int(position * self.video_player_thread.total_frames)
            self.video_player_thread.seek(target_frame)  # Send signal, handled by playback thread
            
    def on_progress_slider_pressed(self):
        """Progress slider pressed event"""
        self.is_slider_pressed = True
        
    def on_progress_slider_released(self):
        """Progress slider released event"""
        if self.video_loaded:
            position = self.progress_slider.value() / 1000.0
            self.video_player_thread.seek(int(position * self.video_player_thread.total_frames))
        self.is_slider_pressed = False
        
    def on_playback_finished(self):
        """Video playback finished event"""
        self.video_status.setText("Playback Completed")
        self.video_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        self.progress_slider.setValue(1000)
        
        # Automatically find and play the next video file
        self.play_next_video()
        
    def play_next_video(self):
        """Find and play the next video file"""
        if not self.current_video_file:
            return
            
        # Get the directory of the current video
        current_dir = os.path.dirname(self.current_video_file)
        if not current_dir:
            current_dir = "."
            
        # If no directory is specified, use the current working directory
        if current_dir == ".":
            current_dir = os.getcwd()
            
        # Find all supported video files in the directory
        try:
            video_files = []
            # 支持更多视频格式
            supported_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv')
            for file in os.listdir(current_dir):
                if file.lower().endswith(supported_extensions):
                    video_files.append(file)
                    
            # If no video files are found, return
            if not video_files:
                return
                
            # Sort the files
            video_files.sort()
            
            # Find the position of the currently playing file in the list
            current_filename = os.path.basename(self.current_video_file)
            try:
                current_index = video_files.index(current_filename)
                # Calculate the index of the next file (loop playback)
                next_index = (current_index + 1) % len(video_files)
                next_video = video_files[next_index]
            except ValueError:
                # If the current file is not in the list, play the first file
                next_video = video_files[0]
                
            # Build the full file path
            next_video_path = os.path.join(current_dir, next_video)
            
            # Check if the file exists
            if not os.path.exists(next_video_path):
                self.statusBar().showMessage(f"Next video file not found: {next_video}")
                return
            
            # Before loading a new video, make sure the current video resources have been released
            if self.video_player_thread:
                # First stop the current playback
                self.video_player_thread.stop()
                
                # Wait a short time to ensure resources are released
                time.sleep(0.1)
            
            # Load and play the next video
            if self.video_player_thread.load_video(next_video_path):
                self.current_video_file = next_video_path
                self.video_loaded = True
                self.video_status.setText("Auto Playing")
                self.video_status.setStyleSheet("background-color: #89b4fa; color: #000000;")
                
                # Ensure the video player is in the correct state
                self.video_player_thread.play()
                
                # Show message
                self.statusBar().showMessage(f"Auto-playing next video: {next_video}")
                
                # If in fullscreen mode, update the fullscreen player as well
                if self.is_in_fullscreen_mode and self.fullscreen_player:
                    self.fullscreen_player.play_pause_btn.setText("Pause")
                    self.fullscreen_player.show_status(f"Auto-playing: {next_video}")
            else:
                self.video_loaded = False
                self.video_status.setText("Auto Play Failed")
                self.video_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
                self.statusBar().showMessage("Failed to auto-play next video")
                
        except Exception as e:
            error(f" Error finding next video: {e}")
            self.statusBar().showMessage("Error occurred while finding next video")
      
        
    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        if self.is_fullscreen:
            self.showNormal()
            self.fullscreen_btn.setText("Fullscreen")
            self.is_fullscreen = False
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("Exit Fullscreen")
            self.is_fullscreen = True
    def enter_fullscreen_play_mode(self):
        """Enter fullscreen play mode"""
        if not self.video_loaded:
            QMessageBox.warning(self, "Notice", "Please select a video file first")
            return
            
        if self.fullscreen_player is None:
            self.fullscreen_player = FullScreenPlayer(self)
            
            # Connect signals
            self.video_player_thread.frame_ready.connect(self.fullscreen_player.update_video_frame)
            self.video_thread.detection_status.connect(self.fullscreen_player.update_detection_status)
            
        # Set fullscreen play button text based on current playback status
        if self.video_player_thread.playing and not self.video_player_thread.paused:
            self.fullscreen_player.play_pause_btn.setText("Pause")
        else:
            self.fullscreen_player.play_pause_btn.setText("Play")
            
        # Hide main window, show fullscreen player
        self.hide()
        self.fullscreen_player.show()
        self.is_in_fullscreen_mode = True
        
        # Update status
        self.fullscreen_player.show_status("Entered fullscreen play mode")
        
    def closeEvent(self, event):
        """Window close event """ 
        # Closing application, cleaning up resources
        # If fullscreen player exists, close it first
        if self.fullscreen_player:
            try:
                self.fullscreen_player.close()
            except Exception as e:
                error(f"Error closing fullscreen player: {e}")
            self.fullscreen_player = None
            
        # Stop timers
        try:
            if hasattr(self, 'progress_timer'):
                self.progress_timer.stop()
        except Exception as e:
            error(f"Error Stop timers : {e}")
        
        # Stop camera thread
        try:
            if hasattr(self, 'video_thread'):
                # Stopping camera thread
                self.video_thread.stop_capture()
        except Exception as e:
            error(f"Error stopping camera thread : {e}")
        
        # Stop video player thread
        try:
            if hasattr(self, 'video_player_thread'):
                # Stopping video player thread
                self.video_player_thread.shutdown()  # Use new shutdown method
                
                # Wait for thread to finish
                if self.video_player_thread.isRunning():
                    self.video_player_thread.quit()
                    self.video_player_thread.wait(3000)  # Wait up to 3 seconds
        except Exception as e:
            error(f"Error stopping video player thread: {e}")
        
        # Force close MediaPipe related resources (if possible)
        try:
            # If there is a MediaPipe cleanup method, call it
            if (hasattr(self, 'video_thread') and 
                hasattr(self.video_thread, 'hand_detector') and
                hasattr(self.video_thread.hand_detector, 'close')):
                self.video_thread.hand_detector.close()
        except Exception as e:
            error(f"Error closing MediaPipe resources: {e}")
        
        # Ensure all OpenCV resources are released
        try:
            cv2.destroyAllWindows()
        except:
            error(f"Error release OpenCV resources: {e}")
        
        # Resource cleanup completed
        event.accept()

    def resizeEvent(self, event):
        """Handle window resize events"""
        super().resizeEvent(event)
        # 在窗口大小改变时重新调整显示区域
        if hasattr(self, 'camera_display') and hasattr(self, 'video_display'):
            # 根据新的窗口大小调整显示区域
            new_width = event.size().width()
            new_height = event.size().height()
            
            # 更新显示区域的最小尺寸
            self.camera_display.setMinimumSize(int(new_width * 0.4), int(new_height * 0.26))
            self.video_display.setMinimumSize(int(new_width * 0.4), int(new_height * 0.26))
            
            # 更新按钮尺寸
            self.fullscreen_btn.setFixedSize(max(90, int(new_width * 0.14)), 30)
            self.fullscreen_play_btn.setFixedSize(max(130, int(new_width * 0.22)), 30)
            self.video_play_btn.setFixedSize(max(58, int(new_width * 0.08)), 30)
            self.video_pause_btn.setFixedSize(max(58, int(new_width * 0.08)), 30)
            self.video_stop_btn.setFixedSize(max(58, int(new_width * 0.08)), 30)
            
            # 更新状态标签尺寸
            if hasattr(self, 'cam_status'):
                self.cam_status.setFixedSize(int(new_width * 0.1), 25)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
