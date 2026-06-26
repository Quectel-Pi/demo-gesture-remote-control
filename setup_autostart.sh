#!/usr/bin/env bash
#===============================================================================
# Gesture Remote Control - 开机自启动安装脚本
# 用法: bash gesture_autostart.sh [install|uninstall|status]
#===============================================================================
set -euo pipefail

# ==================== 配置 ====================
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="gesture-remote-control"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SERVICE_DIR/${SERVICE_NAME}.service"
PYTHON_BIN="$(command -v python3 || echo '/usr/bin/python3')"
MAIN_SCRIPT="$APP_DIR/src/main.py"
REQUIREMENTS="$APP_DIR/requirements.txt"

# ==================== 函数 ====================
generate_service() {
    echo "==> 生成服务文件..."
    mkdir -p "$SERVICE_DIR"
    mkdir -p "$APP_DIR/log_files"

    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Gesture Remote Control - 手势遥控器
After=graphical-session.target
PartOf=graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
Environment=DISPLAY=:0
Environment=XAUTHORITY=%h/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/%U
# 1. 先停掉占用摄像头的系统服务，释放 /dev/video* 设备
ExecStartPre=-/usr/bin/systemctl stop cam-server.service cam2file.service
# 2. 等待摄像头设备就绪（最多 15 秒）
ExecStartPre=/bin/bash -c 'for i in \$(seq 1 15); do if ls /dev/video0 >/dev/null 2>&1; then echo "Camera ready"; exit 0; fi; echo "Waiting camera... \$i/15"; sleep 1; done'
ExecStart=${PYTHON_BIN} ${MAIN_SCRIPT}
WorkingDirectory=${APP_DIR}/src
Restart=always
RestartSec=10
StartLimitIntervalSec=120
StartLimitBurst=5
StandardOutput=append:${APP_DIR}/log_files/service.log
StandardError=append:${APP_DIR}/log_files/service_error.log

[Install]
WantedBy=graphical-session.target
EOF

    echo "  [OK] 已生成: $SERVICE_FILE"
}

install_service() {
    echo "==> 安装服务..."
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    echo "  [OK] 已启用开机自启"
}

start_service() {
    echo "==> 启动服务..."
    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "  服务已在运行，正在重启..."
        systemctl --user restart "$SERVICE_NAME"
    else
        systemctl --user start "$SERVICE_NAME"
    fi

    sleep 2
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        echo "  [OK] 服务已启动"
    else
        echo "  [WARN] 服务可能未成功启动，查看状态:"
        systemctl --user status "$SERVICE_NAME" --no-pager -l || true
    fi
}

uninstall_service() {
    echo "==> 卸载服务..."
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl --user daemon-reload
    systemctl --user reset-failed "$SERVICE_NAME" 2>/dev/null || true
    echo "  [OK] 已卸载"
}

show_status() {
    echo "==> 服务状态 =="
    echo "  服务名: $SERVICE_NAME"
    echo "  服务文件: $SERVICE_FILE"
    echo "  Python: $PYTHON_BIN"
    echo "  主程序: $MAIN_SCRIPT"
    echo ""
    if [ -f "$SERVICE_FILE" ]; then
        echo "  已安装: 是"
        systemctl --user is-enabled --quiet "$SERVICE_NAME" 2>/dev/null && echo "  开机自启: 是" || echo "  开机自启: 否"
        systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null && echo "  运行状态: 运行中" || echo "  运行状态: 已停止"
        echo ""
        systemctl --user status "$SERVICE_NAME" --no-pager -l 2>/dev/null || true
    else
        echo "  已安装: 否"
    fi
}

print_usage() {
    echo "用法: $0 [install|uninstall|status]"
    echo "  install   - 安装依赖并启用开机自启动 (默认)"
    echo "  uninstall - 停止并移除服务"
    echo "  status    - 查看服务状态"
    echo ""
    echo "手动控制:"
    echo "  systemctl --user start   $SERVICE_NAME   # 启动"
    echo "  systemctl --user stop    $SERVICE_NAME   # 停止"
    echo "  systemctl --user restart $SERVICE_NAME   # 重启"
    echo "  journalctl --user -u $SERVICE_NAME -f    # 查看日志"
}

# ==================== 主流程 ====================
ACTION="${1:-install}"

case "$ACTION" in
    install)
        generate_service
        install_service
        start_service
        echo ""
        echo "============================================"
        echo "  安装完成！开机后将自动启动手势遥控器"
        echo "  手动启动: systemctl --user start $SERVICE_NAME"
        echo "  查看日志: journalctl --user -u $SERVICE_NAME -f"
        echo "============================================"
        ;;
    uninstall)
        uninstall_service
        ;;
    status)
        show_status
        ;;
    -h|--help|help)
        print_usage
        ;;
    *)
        echo "[ERROR] 未知操作: $ACTION"
        print_usage
        exit 1
        ;;
esac
