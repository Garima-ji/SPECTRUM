import logging
import sys

def setup_logger():
    logger = logging.getLogger("spectrum")
    logger.setLevel(logging.DEBUG)
    
    # Check if handler is already added to avoid duplicates
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        
        # Standard stdout stream handler
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
    return logger

# Initialize the logger
logger = setup_logger()
