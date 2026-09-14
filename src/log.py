import os
import sys
from datetime import datetime
import logging

# Create logs directory if it doesn't exist
LOGS_DIR = "log_files"
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# Configure logging
LOG_LEVEL = logging.DEBUG  # Set to DEBUG, INFO, WARNING, ERROR, or CRITICAL
# Generate log file name with current date and time (including seconds)
LOG_FILE = os.path.join(LOGS_DIR, f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")  # Log file name with date and time including seconds

# Create logger
logger = logging.getLogger("EyeRemoteControl")
logger.setLevel(LOG_LEVEL)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create file handler and set level to debug
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(LOG_LEVEL)
file_handler.setFormatter(formatter)

# Add handlers to logger
if not logger.handlers:
    logger.addHandler(file_handler)
    # logger.addHandler(console_handler)

def debug(message):
    """Log debug message"""
    logger.debug(message)

def info(message):
    """Log info message"""
    logger.info(message)

def warning(message):
    """Log warning message"""
    logger.warning(message)

def error(message):
    """Log error message"""
    logger.error(message)

def critical(message):
    """Log critical message"""
    logger.critical(message)

# Example usage
if __name__ == "__main__":
    debug("Debug message")
    info("Info message")
    warning("Warning message")
    error("Error message")
    critical("Critical message")