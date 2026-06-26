import time
import os
import threading
import subprocess
import signal
from moviepy.editor import VideoFileClip
from PySide6.QtCore import QThread, Signal
from log import debug, error 

class VideoPlayerThread(QThread):
    """Video player thread using MoviePy for video frames and system audio for audio"""
    frame_ready = Signal(object)
    playback_finished = Signal()
    video_info_ready = Signal(dict)
    
    def __init__(self):
        super().__init__()
        self.clip = None
        self.current_file = ""
        self.playing = False
        self.paused = False
        self.stopped = True
        self.video_fps = 30
        self.total_frames = 0
        self.video_width = 0
        self.video_height = 0
        self.video_duration = 0
        self.exiting = False
        self.current_frame = 0
        self.last_frame_time = 0
        self._lock = threading.RLock()
        
        # Audio player process
        self.audio_process = None
        self.audio_process_start_time = 0
        self._pause_position = 0
        self._audio_restart_inflight = False
        self._audio_restart_lock = threading.Lock()
        self._last_audio_restart_time = 0.0
        self._audio_restart_min_interval = 0.35
        self._audio_resync_delay = 0.05
        self._seek_audio_target_time = None
        self._seek_audio_requested_at = 0.0
        

    def load_video(self, file_path):
        """Load video file using MoviePy for video frames and prepare audio"""
        try:
            debug(f"Attempting to load video: {file_path}")
            
            with self._lock:
                # Release existing clip
                if self.clip:
                    self.clip.close()
                    self.clip = None
                
                # Stop any playing audio process
                self._stop_audio_process()
                
                # Load new clip for frame extraction
                self.clip = VideoFileClip(file_path)
                
                self.current_file = file_path
                self.video_duration = self.clip.duration
                self.video_fps = self.clip.fps if self.clip.fps else 30
                self.total_frames = int(self.video_duration * self.video_fps)
                
                if self.clip.size:
                    self.video_width, self.video_height = self.clip.size
                else:
                    self.video_width, self.video_height = 1920, 1080
                
                self.stopped = True
                self.playing = False
                self.paused = False
                self.current_frame = 0
                self._pause_position = 0

            # Prepare video information
            video_info = {
                'file_path': file_path,
                'filename': os.path.basename(file_path),
                'width': self.video_width,
                'height': self.video_height,
                'fps': self.video_fps,
                'total_frames': self.total_frames,
                'duration': self.video_duration
            }

            # Emit video information
            self.video_info_ready.emit(video_info)
            debug(f"Successfully loaded video: {file_path}")
            return True
        except Exception as e:
            error(f"Failed to load video: {e}")
            return False
     
    
    def _stop_audio_process(self):
        """Safely stop audio process with proper resource release and device status tracking"""
        if self.audio_process:
            try:
                if self.audio_process.poll() is None:
                    os.killpg(os.getpgid(self.audio_process.pid), signal.SIGTERM)
                    
                    # Wait 0.5s as per best practices for complete resource release
                    try:
                        self.audio_process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(self.audio_process.pid), signal.SIGKILL)
                    debug("PulseAudio process terminated safely")
                
            except Exception as e:
                error(f"Critical audio process termination error: {e}")
            finally:
                self.audio_process = None
                
    def _check_audio_device_status(self):
        """Check if audio devices are available"""
        try:
            # Check if any audio devices are available
            result = subprocess.run(['pactl', 'list', 'sinks'], 
                                    stdout=subprocess.DEVNULL, 
                                    stderr=subprocess.DEVNULL, 
                                    timeout=2)
            return result.returncode == 0
        except:
            # Fallback: check if pulseaudio daemon is running
            try:
                result = subprocess.run(['pulseaudio', '--check'], 
                                        stdout=subprocess.DEVNULL, 
                                        stderr=subprocess.DEVNULL)
                return result.returncode == 0
            except:
                return False

    def _get_current_volume(self):
        """Get current system volume percentage (0-100) from PulseAudio"""
        try:
            # Get the current volume of the default sink
            result = subprocess.run(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'], 
                                    stdout=subprocess.PIPE, 
                                    stderr=subprocess.DEVNULL, 
                                    text=True, 
                                    timeout=1)
            if result.returncode == 0:
                # Parse the volume percentage
                # Example output: "Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB"
                # We'll take the first percentage value
                import re
                match = re.search(r'(\d+)%', result.stdout)
                if match:
                    return int(match.group(1))
        except Exception as e:
            error(f"Failed to get current volume: {e}")
        return 100  # Default to 100% if we can't get the volume

    def _start_audio(self, start_time=0):
        """Start audio playback with device status checking and retry mechanism"""
        if not self.clip or not self.clip.audio:
            return
            
        # Check device status before starting audio
        if not self._check_audio_device_status():
            error("Audio device not available, delaying audio start")
            # Implement retry with exponential backoff (0.5s, 1.0s, 1.5s)
            for attempt in range(3):
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                if self._check_audio_device_status():
                    debug(f"Audio device available on attempt {attempt + 1}")
                    break
            else:
                error("Audio device not available after 3 attempts")
                return  # Give up after 3 attempts
                
        try:
            # Stop any existing audio process
            self._stop_audio_process()
            
            # Verify device is still available after stopping previous process
            if not self._check_audio_device_status():
                error("Audio device became unavailable during process stop")
                return
                
            # Only use PulseAudio as requested by user
            if os.system('which paplay > /dev/null 2>&1') == 0:
                debug("Using PulseAudio for audio playback")
                
                # Wait for device to be ready (per specification: 1.5s for driver init)
                time.sleep(0.5)  # Reduced from 1.5s to avoid excessive delay
                
                temp_audio = f'/tmp/temp_audio_{int(time.time())}_{os.getpid()}.wav'
                
                # Extract audio segment
                extract_cmd = [
                    'ffmpeg',
                    '-ss', str(start_time),
                    '-i', self.current_file,
                    '-acodec', 'pcm_s16le',
                    '-ac', '2',
                    '-ar', '48000',
                    '-f', 'wav',
                    '-y',
                    temp_audio
                ]
                
                subprocess.run(
                    extract_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                # Get current system volume and convert to paplay scale
                volume_percent = self._get_current_volume()
                volume_value = int(volume_percent * 655.36)
                debug(f"Setting audio volume to {volume_percent}% ({volume_value})")
                
                # Critical: Capture stderr for diagnostics (per best practices)
                self.audio_process = subprocess.Popen(
                    ['paplay', '--volume', str(volume_value), temp_audio],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
                
                # Start dedicated thread to capture stderr
                def monitor_stderr():
                    try:
                        while True:
                            line = self.audio_process.stderr.readline()
                            if not line:
                                break
                            error(f"PulseAudio error: {line.decode().strip()}")
                    except Exception as e:
                        error(f"Error monitoring paplay: {e}")
                
                threading.Thread(target=monitor_stderr, daemon=True).start()
                
                self.audio_process_start_time = time.time() - start_time
                debug(f"Started PulseAudio (PID: {self.audio_process.pid})")
                
                # Enhanced cleanup with error handling
                def cleanup():
                    try:
                        time.sleep(max(0, self.video_duration - start_time + 1))
                        if os.path.exists(temp_audio):
                            os.remove(temp_audio)
                    except Exception as e:
                        error(f"Cleanup failed: {e}")
                
                threading.Thread(target=cleanup, daemon=True).start()
            
        except Exception as e:
            error(f"PulseAudio initialization failed: {e}")

    def _pause_audio(self):
        """Pause the audio process if running - for this implementation we stop and restart at correct position"""
        if self.audio_process:
            try:
                # Calculate the elapsed time and store it as the pause position
                elapsed_time = time.time() - self.audio_process_start_time
                self._pause_position = elapsed_time
                # For this implementation, we'll stop the current process and restart at the correct position
                self._stop_audio_process()
                return self._pause_position
            except Exception as e:
                error(f"Error pausing audio process: {e}")
        return self._pause_position

    def _resume_audio(self):
        """Resume audio from the pause position"""
        if self.clip and self.clip.audio:
            try:
                self._start_audio(self._pause_position)
                debug(f"Resumed audio from position: {self._pause_position}")
            except Exception as e:
                error(f"Error resuming audio: {e}")

    def _start_audio_async(self, start_time=0):
        """Start/restart audio in background to avoid blocking video frame loop."""
        with self._audio_restart_lock:
            if self._audio_restart_inflight:
                return
            self._audio_restart_inflight = True

        def _worker(target_time):
            try:
                self._start_audio(target_time)
            except Exception as e:
                error(f"Async audio start failed: {e}")
            finally:
                with self._audio_restart_lock:
                    self._audio_restart_inflight = False
                    self._last_audio_restart_time = time.time()

        threading.Thread(target=_worker, args=(start_time,), daemon=True).start()

    def _request_audio_resync(self, seek_time):
        """Coalesce frequent seek operations into one delayed audio resync request."""
        with self._lock:
            self._seek_audio_target_time = seek_time
            self._seek_audio_requested_at = time.time()

    def _maybe_process_audio_resync(self):
        """Apply pending seek audio resync when command burst settles."""
        with self._lock:
            target_time = self._seek_audio_target_time
            requested_at = self._seek_audio_requested_at
            playing = self.playing
            paused = self.paused
            stopped = self.stopped

        if target_time is None or stopped or paused or not playing:
            return

        now = time.time()
        with self._audio_restart_lock:
            inflight = self._audio_restart_inflight
            last_restart = self._last_audio_restart_time

        if inflight:
            return
        if (now - requested_at) < self._audio_resync_delay:
            return
        if (now - last_restart) < self._audio_restart_min_interval:
            return

        with self._lock:
            if self._seek_audio_target_time is None:
                return
            target_time = self._seek_audio_target_time
            self._seek_audio_target_time = None

        self._start_audio_async(target_time)

    def play(self):
        """Start playback"""
        start_time = 0
        should_start_audio = False
        with self._lock:
            was_stopped = self.stopped
            was_paused = self.paused
            self.playing = True
            self.paused = False
            self.stopped = False
            self.last_frame_time = time.time()
            
            # Calculate start time based on current frame position
            start_time = (self.current_frame / self.video_fps) if self.video_fps > 0 else 0
            
            # For paused state, use the stored pause position
            if (not was_stopped) and was_paused:
                start_time = self._pause_position
            
            should_start_audio = bool(self.clip and self.clip.audio)
            
        if should_start_audio:
            self._start_audio_async(start_time)
            debug("Audio playback started")

    def pause(self):
        """Pause playback"""
        should_pause_audio = False
        with self._lock:
            if self.playing and not self.stopped:
                # Calculate the current position in the video
                elapsed_time = time.time() - self.last_frame_time
                frames_advanced = int(elapsed_time * self.video_fps)
                current_frame = self.current_frame + frames_advanced
                self._pause_position = current_frame / self.video_fps if self.video_fps > 0 else 0
                should_pause_audio = True
                
            self.paused = True
            self.playing = False
            debug("Playback paused")

        if should_pause_audio:
            self._pause_audio()

    def stop(self):
        """Stop playback"""
        should_stop_audio = False
        with self._lock:
            self.playing = False
            self.paused = False
            self.stopped = True
            self.current_frame = 0
            self._pause_position = 0
            self._seek_audio_target_time = None
            self._seek_audio_requested_at = 0.0
            should_stop_audio = True
            
            debug("Playback stopped")

        if should_stop_audio:
            self._stop_audio_process()

    def get_position(self):
        """Get current playback position (0.0 to 1.0)"""
        with self._lock:
            if self.total_frames > 0:
                return self.current_frame / self.total_frames
        return 0.0

    def seek(self, frame_number):
        """Seek to specific frame"""
        seek_time = 0.0
        should_resync_audio = False
        should_emit_preview = False
        clip = None
        preview_timestamp = 0.0
        with self._lock:
            frame_number = max(0, min(frame_number, self.total_frames - 1))
            self.current_frame = frame_number
            seek_time = (frame_number / self.video_fps) if self.video_fps > 0 else 0
            self._pause_position = seek_time
            should_resync_audio = bool(self.clip and self.clip.audio and self.playing and not self.paused and not self.stopped)
            should_emit_preview = bool(self.clip and (self.paused or self.stopped or not self.playing))
            if should_emit_preview:
                clip = self.clip
                preview_timestamp = seek_time

        if should_resync_audio:
            self._request_audio_resync(seek_time)

        if should_emit_preview and clip is not None:
            try:
                frame = clip.get_frame(t=preview_timestamp)
                frame = frame[:, :, ::-1]
                self.frame_ready.emit(frame)
            except Exception as e:
                error(f"Preview frame seek failed: {e}")

    def run(self):
        """Main playback loop"""
        while not self.exiting:
            self._maybe_process_audio_resync()

            with self._lock:
                playing = self.playing
                paused = self.paused
                stopped = self.stopped
                
            if stopped or not playing or paused:
                time.sleep(0.01)
                continue
                
            if not self.clip:
                time.sleep(0.01)
                continue
                
            with self._lock:
                # Calculate time-based frame advancement
                current_time = time.time()
                elapsed = current_time - self.last_frame_time
                self.last_frame_time = current_time
                
                # Advance frame based on elapsed time
                frames_to_advance = int(elapsed * self.video_fps)
                self.current_frame = min(self.current_frame + frames_to_advance, self.total_frames - 1)
                
                # Check if we've reached the end
                if self.current_frame >= self.total_frames - 1:
                    self.playing = False
                    self.stopped = True
                    self.playback_finished.emit()
                    # Stop audio
                    self._stop_audio_process()
                    continue
            
            # Get frame from clip
            try:
                timestamp = self.current_frame / self.video_fps
                frame = self.clip.get_frame(t=timestamp)
                
                # Convert BGR to RGB
                frame = frame[:, :, ::-1]
                
                # Emit frame
                self.frame_ready.emit(frame)
            except Exception as e:
                error(f"Error getting frame: {e}")
                
            # Maintain frame rate
            time.sleep(1.0 / self.video_fps if self.video_fps > 0 else 0.033)
            
        debug("Video player thread exited")

    def shutdown(self):
        """Safely shut down thread"""
        debug("Shutting down video player thread")
        with self._lock:
            self.exiting = True
            self.playing = False
            self.paused = False
            self.stopped = True
            self._pause_position = 0
            
            # Stop audio process
            self._stop_audio_process()
            
            # Close clip
            if self.clip:
                self.clip.close()