# Gesture Remote Control

[中文](README_zh.md) | English

This project is a gesture-recognition–based remote video control demo implemented on the [Quectel Pi Smart Single-Board Computer](https://www.quectel.com.cn/quectel-pi/documentation-center).
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
|Thumbs up, hold still for about 0.06 seconds (after completing both exercises)|Toggle the favorite star in the top toolbar|

This `gotc-experience` branch is a hands-on exercise version. Complete `TODO Exercise 1` in `src/gesture_recognizer.py` and `TODO Exercise 2` in `src/main.py` before using thumbs up to toggle the star. Until then, the thumbs-up action is not enabled, but the star can still be clicked manually. The favorite button is a visual effect only: it does not change audio or playback, requires no video, and is not saved between runs. Release the pose before repeating; the existing 1-second gesture cooldown still applies.

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
   - av == 16.0.1

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

## Project Structure

```
demo-gesture-remote-control/
├── docs/                       # Project documentation
│   ├── assets/
│   │   ├── main.png
│   │   └── main_recognize.png
│   └── oceans.mp4              # Sample demo video
├── src/                        # Source code directory
│   ├── models/                 # RKNN hand detection and landmark models
│   │   ├── LICENSE
│   │   ├── hand_landmark_full.rknn
│   │   └── palm_detection_full.rknn
│   ├── fullscreen_player_mode.py
│   ├── gesture_recognizer.py
│   ├── log.py
│   ├── main.py
│   ├── npu_hands.py
│   ├── video_capture.py
│   └── video_player.py
├── .gitignore
├── README.md                   # English documentation
├── README_zh.md                # Chinese documentation
├── install.sh                  # Environment deployment script
├── requirements.txt            # Dependency list
└── setup_autostart.sh          # Auto-start setup script
```

## Run the program

Run the `main.py` startup script in the `src` directory. If the program starts successfully, a camera preview window will open.

```bash
cd demo-gesture-remote-control/src/
python3 main.py
```
![](docs/assets/main.png)

## Model Overview

The Quectel Pi M2 development board can use MediaPipe hand models from OpenCV Zoo, converted to FP16 RKNN format for RK3576:

- **Palm detection model**: Locates palms in the image and provides hand regions for the landmark model.
- **Hand landmark model**: Outputs 21 landmarks per hand for the existing gesture recognition logic.

The models are licensed under Apache 2.0 and sourced from [palm detection](https://github.com/opencv/opencv_zoo/tree/main/models/palm_detection_mediapipe) and [hand landmark estimation](https://github.com/opencv/opencv_zoo/tree/main/models/handpose_estimation_mediapipe). The license is included in `src/models/LICENSE`.

Installation and usage remain unchanged: deploy with `./install.sh` and run `python3 main.py` as described above.
M2 automatically attempts NPU inference and reports the reason before falling back to CPU if unavailable; H1 and M1 continue to use MediaPipe on CPU.
Gesture controls remain unchanged, with no additional configuration or manual model conversion required.

## Reporting Issues
If you encounter any issues during use, please submit technical inquiries on the [Quectel Official Forum](https://forumschinese.quectel.com/c/quectel-pi/58). Our technical support team will respond promptly.
Issues and Pull Requests are welcome to help improve this project.
