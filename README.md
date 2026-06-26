# Gesture Remote Control

[中文](README_zh.md) | English

This project is a gesture-recognition–based remote video control demo implemented on the [Quectel Pi H1 single-board computer](https://developer.quectel.com/doc/sbc/en/index.html).
The system captures hand images through a camera and applies AI-based hand gesture recognition algorithms to enable touchless control of video playback, progress, and volume, providing a natural and intuitive user interaction experience.

![](docs/assets/main_recognize.png)

## Features

- Real-time hand detection and tracking
- Support for multiple intuitive control gestures
- Remote video playback control
   - Play / Pause
   - Fast Forward / Rewind
   - Volume adjustment
- Hand status visualization (Hand detected / No hand detected)
- Single-hand natural operation with a low learning curve
- Fully local processing: low latency and no cloud dependency

**Supported Gesture Mapping**

> Please ensure that the camera is facing the user’s operation area and that the lighting conditions are stable.

|Gesture Action|Control Function|
|----------|---------|
|Open palm (5 fingers)|Play / Pause|
|Swipe right|Fast forward 5 seconds|
|Swipe left|Rewind 5 seconds|
|Swipe up|Volume +5%|
|Swipe down|Volume −5%|

## Hardware Requirements

- Quectel Pi H1 single-board computer
- USB camera
- Display device (DSI touchscreen)
- Audio output device (speaker or headphones)

## Software Environment Setup

> Verify whether multiple Python versions exist on the system to avoid import issues after package installation.

- Operating System: Debian 13 (default OS for Quectel Pi H1)
- Video playback: ffmpeg 
- Python：Python 3  
- Dependencies:
   - Python 3.9-3.12
   - OpenCV-Python == 4.8.1.78
   - MediaPipe == 0.10.9
   - NumPy == 1.24.3
   - PySide6 == 6.5.3
   - protobuf == 3.20.3

### Implementation Steps

1. Clone the project using git.

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/Quectel-Pi/demo-gesture-remote-control.git
```

After execution, a `demo-gesture-remote-control` folder should be created in the current directory.

2. Run the following commands in sequence on the board's terminal. If the terminal shows `Deployment complete`, deployment is successful.

```bash
cd demo-gesture-remote-control
sudo chmod 755 install.sh
./install.sh
```

If an error occurs while running `./install.sh`, or the terminal does not show `Deployment complete`, do not continue to the next steps yet. Re-run the installation script from the project root and make sure network connectivity is normal.

3. Reopen the terminal and verify the Python version. If the output is `Python 3.10.15`, the installation is successful.

```bash
python3 --version
```

#### Run the program

Run the `main.py` startup script in the `src` directory. If the program starts successfully, a camera preview window will open.

```bash
cd demo-gesture-remote-control/src/
python3 main.py
```
![](docs/assets/main.png)

### Enable Auto-start at Boot (Optional)

This project provides `setup_autostart.sh` to configure a user-level systemd service.

```bash
cd gesture-remote-control/demo-gesture-remote-control/
./setup_autostart.sh install
```

Service management:

```bash
./setup_autostart.sh status
./setup_autostart.sh uninstall
```

Logs:

```bash
journalctl --user -u gesture-remote-control -f
```

## Project Structure

```
gesture-remote-control/
├── docs                        # Project documentation
│   ├── assets                  # Static asset files
│   └── oceans.mp4              # Sample demo video
├── src/                        # Source code directory
|   ├── main.py                 # Main program entry point
│   ├── gesture_recognizer.py   # Gesture recognition core logic
│   ├── video_capture.py        # Video capture thread
│   ├── video_player.py         # Video player thread
│   ├── fullscreen_player_mode.py # Fullscreen playback UI
│   ├── log.py                  # Logging module
├── log_files/                  # Log files
├── README.md                   # Project english documentation
├── README_zh.md                # Project chinese documentation
├── install.sh                  # Environment deployment script
├── setup_autostart.sh          # Auto-start setup script
└── requirements.txt            # Dependency list
```

## Reporting Issues
If you encounter any issues during use, please submit technical inquiries on the [Quectel Official Forum](https://forumschinese.quectel.com/c/quectel-pi/58). Our technical support team will respond promptly.
Issues and Pull Requests are welcome to help improve this project.
