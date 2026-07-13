from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, 
    QHBoxLayout, QSlider,
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QKeyEvent, QMouseEvent


class FullScreenPlayer(QWidget):
    """Full screen player window"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setup_ui()
        self.setup_style()
        self.frame_remain = 0
        self.last_command = ''
        
    def setup_ui(self):
        # Set window flags to make it a full screen window
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Video display area
        self.video_label = QLabel("Loading video...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("""
            QLabel {
                background-color: #000000;
                color: #ffffff;
                font-size: 24px;
                font-weight: bold;
            }
        """)
        
        # Adjust overlay position and style
        self.detection_overlay = QLabel(self.video_label)
        self.detection_overlay.setStyleSheet("""
            QLabel {
                color: #ff5555;
                font-size: 24px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 180);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.detection_overlay.setAlignment(Qt.AlignCenter)
        self.detection_overlay.hide()
        
        # Adjust playback status label position and style
        self.playback_status_overlay = QLabel(self.video_label)
        self.playback_status_overlay.setStyleSheet("""
            QLabel {
                color: #50fa7b;
                font-size: 24px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 180);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.playback_status_overlay.setAlignment(Qt.AlignCenter)
        self.playback_status_overlay.hide()
        
        # Add new status label (for displaying time and other information)
        self.status_overlay = QLabel(self.video_label)
        self.status_overlay.setStyleSheet("""
            QLabel {
                color: #f1fa8c;
                font-size: 20px;
                font-weight: normal;
                background-color: rgba(0, 0, 0, 180);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.status_overlay.setAlignment(Qt.AlignCenter)
        self.status_overlay.hide()
        
        # Control bar (hidden by default, shown on mouse move)
        self.control_bar = QWidget()
        self.control_bar.setObjectName("control_bar")
        self.control_bar.setFixedHeight(80)
        self.control_bar.hide()
        
        control_layout = QHBoxLayout(self.control_bar)
        control_layout.setContentsMargins(20, 0, 20, 20)
        
        # Back button
        self.back_btn = QPushButton("Back")
        self.back_btn.setFixedSize(100, 40)
        self.back_btn.clicked.connect(self.exit_fullscreen)
        
        # Play/Pause button
        self.play_pause_btn = QPushButton("Pause")
        self.play_pause_btn.setFixedSize(100, 40)
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        
        # Progress slider
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        
        # Time label
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #ffffff; font-size: 14px;")
        
        # Status label (display recognition status)
        self.status_label = QLabel("Detecting...")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 14px;
                padding: 5px 10px;
                background-color: rgba(0, 0, 0, 150);
                border-radius: 5px;
            }
        """)
        
        control_layout.addWidget(self.back_btn)
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.progress_slider, 1)
        control_layout.addWidget(self.time_label)
        control_layout.addWidget(self.status_label)
        
        main_layout.addWidget(self.video_label, 1)
        main_layout.addWidget(self.control_bar)
        
        # Mouse move detection timer
        self.mouse_timer = QTimer()
        self.mouse_timer.timeout.connect(self.hide_controls)
        self.mouse_timer.setSingleShot(True)
        
        # Control bar show/hide animation
        self.control_animation = QPropertyAnimation(self.control_bar, b"windowOpacity")
        self.control_animation.setDuration(300)
        
        # Status label timer (auto-hide)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.hide_status)
        self.status_timer.setSingleShot(True)
        
        # Overlay display timer
        self.overlay_timer = QTimer()
        self.overlay_timer.timeout.connect(self.hide_overlays)
        self.overlay_timer.setSingleShot(True)
        
    def setup_style(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #000000;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 30);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 50);
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 50);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 20);
            }
            QSlider::groove:horizontal {
                border: 1px solid rgba(255, 255, 255, 50);
                height: 6px;
                background: rgba(255, 255, 255, 20);
                margin: 0px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #cccccc;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #89b4fa;
                border: 1px solid rgba(137, 180, 250, 100);
                height: 6px;
                border-radius: 3px;
            }
        """)
        
    def showEvent(self, event):
        """Window show event"""
        super().showEvent(event)
        self.showFullScreen()
        # Ensure controls are properly sized in fullscreen mode
        self.adjust_overlay_positions()
        
    def keyPressEvent(self, event: QKeyEvent):
        """Keyboard event handling"""
        if event.key() == Qt.Key_Escape:
            self.exit_fullscreen()
        elif event.key() == Qt.Key_Space:
            self.toggle_play_pause()
        elif event.key() == Qt.Key_F11:
            # Toggle full screen/window mode
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        else:
            super().keyPressEvent(event)
            
    def mouseMoveEvent(self, event: QMouseEvent):
        """Mouse move event - show control bar"""
        super().mouseMoveEvent(event)
        self.show_controls()
        
    def show_controls(self):
        """Show control bar"""
        if not self.control_bar.isVisible():
            self.control_bar.show()
            self.control_animation.setStartValue(0)
            self.control_animation.setEndValue(1)
            self.control_animation.start()
        
        # Reset hide timer
        self.mouse_timer.stop()
        self.mouse_timer.start(3000)  # Hide after 3 seconds
        
    def hide_controls(self):
        """Hide control bar"""
        self.control_animation.setStartValue(1)
        self.control_animation.setEndValue(0)
        self.control_animation.finished.connect(lambda: self.control_bar.hide())
        self.control_animation.start()
        
    def show_status(self, message, duration=2000):
        """Show status message"""
        self.status_label.setText(message)
        self.status_label.show()
        self.status_timer.stop()
        self.status_timer.start(duration)
        
    def hide_status(self):
        """Hide status message"""
        self.status_label.hide()
        
    def show_overlays(self, detection_text="", playback_text="", status_text=""):
        """Show overlay information"""
        # Ensure labels adjust size based on text content
        if detection_text:
            self.detection_overlay.setText(detection_text)
            self.detection_overlay.adjustSize()  # Adjust size based on text
            self.detection_overlay.show()
            
        if playback_text:
            self.playback_status_overlay.setText(playback_text)
            self.playback_status_overlay.adjustSize()  # Adjust size based on text
            self.playback_status_overlay.show()
            
        if status_text:
            self.status_overlay.setText(status_text)
            self.status_overlay.adjustSize()  # Adjust size based on text
            self.status_overlay.show()
            
        # Ensure overlays don't overlap
        self.adjust_overlay_positions()
        
        # Reset hide timer
        self.overlay_timer.stop()
        self.overlay_timer.start(2000)  # Hide after 2 seconds
        
    def hide_overlays(self):
        """Hide overlays"""
        self.detection_overlay.hide()
        self.playback_status_overlay.hide()
        self.status_overlay.hide()
        
    def update_detection_status(self, detection_result):
        """Update detection status display"""
        gesture_cmd = None
        if detection_result:
            hand_present = detection_result.get('hand_present', False)
            gesture_cmd = detection_result.get('cmd', None)
            if hand_present:
                playback_text = "Gesture Active"
            else:
                playback_text = "No Hand"
        else:
            playback_text = "Detection Inactive"

        if gesture_cmd is None:
            gesture_cmd = "Waiting"
            if self.frame_remain >= 0:
                self.frame_remain -= 1
                gesture_cmd = self.last_command or gesture_cmd
        else:
            self.frame_remain = 5
            if self.parent_window and hasattr(self.parent_window, 'command_display_text'):
                self.last_command = self.parent_window.command_display_text(gesture_cmd)
            else:
                self.last_command = gesture_cmd
            gesture_cmd = self.last_command
            if self.parent_window and hasattr(self.parent_window, 'control_mode_text'):
                mode_text, _ = self.parent_window.control_mode_text(detection_result.get('cmd', None))
                playback_text = mode_text
        self.show_overlays(
            detection_text=gesture_cmd,
            playback_text=playback_text,
        )

    def exit_fullscreen(self):
        """Exit full screen mode"""
        self.close()
        if self.parent_window:
            self.parent_window.showNormal()
            self.parent_window.show()
            
    def toggle_play_pause(self):
        """Toggle play/pause"""
        if self.parent_window:
            if self.parent_window.video_player_thread.playing and not self.parent_window.video_player_thread.paused:
                self.parent_window.pause_video()
                self.play_pause_btn.setText("Play")
                self.show_status("Paused")
                self.show_overlays(playback_text="Paused")
            else:
                self.parent_window.play_video()
                self.play_pause_btn.setText("Pause")
                self.show_status("Playing")
                self.show_overlays(playback_text="Playing")
                
    def update_video_frame(self, frame):
        """Update video frame"""
        if self.parent_window:
            self.parent_window.display_frame(self.video_label, frame)
            
    def update_progress(self, position, duration):
        """Update progress slider and time display"""
        if not self.progress_slider.isSliderDown():  # If user is not dragging the slider
            self.progress_slider.setValue(int(position * 1000))
            
        # Update time display
        current_time = position * duration
        current_str = f"{int(current_time // 60):02d}:{int(current_time % 60):02d}"
        total_str = f"{int(duration // 60):02d}:{int(duration % 60):02d}"
        self.time_label.setText(f"{current_str} / {total_str}")

    def adjust_overlay_positions(self):
        """Adjust overlay positions to avoid overlap"""
        # Get video label dimensions
        video_rect = self.video_label.rect()
        
        # Adjust detection result overlay position (top-left)
        if self.detection_overlay.isVisible():
            self.detection_overlay.adjustSize()
            detection_size = self.detection_overlay.sizeHint()
            self.detection_overlay.setGeometry(
                20,  # Left margin
                20,  # Top margin
                detection_size.width(),
                detection_size.height()
            )
            
        # Adjust playback status overlay position (top-right)
        if self.playback_status_overlay.isVisible():
            self.playback_status_overlay.adjustSize()
            playback_size = self.playback_status_overlay.sizeHint()
            self.playback_status_overlay.setGeometry(
                video_rect.width() - playback_size.width() - 20,  # Right margin 20 pixels
                20,  # Top margin
                playback_size.width(),
                playback_size.height()
            )
            
        # Adjust status overlay position (bottom-center)
        if self.status_overlay.isVisible():
            self.status_overlay.adjustSize()
            status_size = self.status_overlay.sizeHint()
            self.status_overlay.setGeometry(
                (video_rect.width() - status_size.width()) // 2,  # Centered
                video_rect.height() - status_size.height() - 20,  # Bottom margin 20 pixels
                status_size.width(),
                status_size.height()
            )