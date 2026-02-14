#!/usr/bin/env python
# coding: utf-8

"""
YouTube Auto-Downloader
Monitors clipboard for YouTube URLs and automatically downloads videos.
"""

import os
import sys
import time
import json
import re
import logging
import threading
from queue import Queue
from datetime import datetime
from pathlib import Path

try:
    import pyperclip
except ImportError:
    print("Installing required package: pyperclip")
    os.system("pip install pyperclip")
    import pyperclip

try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    print("Installing required package: plyer (for notifications)")
    os.system("pip install plyer")
    try:
        from plyer import notification
        NOTIFICATIONS_AVAILABLE = True
    except:
        NOTIFICATIONS_AVAILABLE = False
        print("Warning: Notifications not available")

# Prefer yt-dlp over youtube-dl for better stability and features
try:
    import yt_dlp as youtube_dl
except ImportError:
    try:
        import youtube_dl
    except ImportError:
        print("Neither yt-dlp nor youtube-dl found. Please install one of them.")
        sys.exit(1)


class YouTubeAutoDownloader:
    """Monitors clipboard and automatically downloads YouTube videos."""
    
    def __init__(self, config_path="config.json"):
        """Initialize the auto-downloader with configuration."""
        self.config = self.load_config(config_path)
        self.downloaded_urls = set()
        self.last_clipboard = ""
        self.setup_logging()
        self.ensure_download_directory()
        
        # Download queue and worker thread
        self.download_queue = Queue()
        self.is_downloading = False
        self.current_download = None
        self.queue_lock = threading.Lock()
        
        # Start download worker thread
        self.worker_thread = threading.Thread(target=self._download_worker, daemon=True)
        self.worker_thread.start()
        
        # YouTube URL patterns
        self.youtube_patterns = [
            r'(https?://)?(www\.)?(youtube\.com/watch\?v=[\w-]+)',
            r'(https?://)?(www\.)?(youtu\.be/[\w-]+)',
            r'(https?://)?(www\.)?(youtube\.com/shorts/[\w-]+)',
            r'(https?://)?(www\.)?(youtube\.com/playlist\?list=[\w-]+)',
        ]
        
    def load_config(self, config_path):
        """Load configuration from JSON file."""
        default_config = {
            "download_path": os.path.join(os.path.expanduser("~"), "Downloads", "YouTube"),
            "video_format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "enable_notifications": True,
            "check_interval": 1.0,
            "filename_template": "%(title)s-%(id)s.%(ext)s",
            "merge_output_format": "mp4"
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception as e:
                print(f"Error loading config: {e}. Using defaults.")
        else:
            # Create default config file
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2)
                print(f"Created default config file: {config_path}")
            except Exception as e:
                print(f"Could not create config file: {e}")
        
        return default_config
    
    def setup_logging(self):
        """Setup logging to file and console."""
        log_dir = Path(self.config['download_path']) / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f'auto_downloader_{datetime.now().strftime("%Y%m%d")}.log'
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def ensure_download_directory(self):
        """Create download directory if it doesn't exist."""
        download_path = Path(self.config['download_path'])
        download_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Download directory: {download_path}")
    
    def is_youtube_url(self, text):
        """Check if text contains a YouTube URL."""
        if not text or not isinstance(text, str):
            return None
        
        for pattern in self.youtube_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Extract full URL
                url = match.group(0)
                if not url.startswith('http'):
                    url = 'https://' + url
                return url
        return None
    
    def show_notification(self, title, message, timeout=5):
        """Show system notification."""
        if not self.config['enable_notifications'] or not NOTIFICATIONS_AVAILABLE:
            return
        
        try:
            notification.notify(
                title=title,
                message=message,
                app_name='YouTube Auto-Downloader',
                timeout=timeout
            )
        except Exception as e:
            self.logger.warning(f"Could not show notification: {e}")
    
    def add_to_queue(self, url):
        """Add URL to download queue."""
        if url in self.downloaded_urls:
            self.logger.info(f"URL already downloaded or in queue: {url}")
            return
        
        # Add to downloaded_urls to prevent duplicates in queue
        self.downloaded_urls.add(url)
        
        # Add to queue
        self.download_queue.put(url)
        queue_size = self.download_queue.qsize()
        
        if queue_size == 1 and not self.is_downloading:
            self.logger.info(f"Added to queue: {url}")
            self.show_notification(
                "Added to Queue",
                f"Download will start shortly...\nQueue: {queue_size} video(s)"
            )
        else:
            self.logger.info(f"Added to queue (position {queue_size}): {url}")
            self.show_notification(
                "Added to Queue",
                f"Position in queue: {queue_size}\nCurrent: {self.current_download or 'Starting...'}"
            )
    
    def _download_worker(self):
        """Worker thread that processes download queue."""
        while True:
            try:
                # Get URL from queue (blocking)
                url = self.download_queue.get()
                
                if url is None:  # Poison pill to stop worker
                    break
                
                # Download the video
                self._download_video(url)
                
                # Mark task as done
                self.download_queue.task_done()
                
            except Exception as e:
                self.logger.error(f"Error in download worker: {e}")
    
    def _download_video(self, url):
        """Actually download video using youtube-dl."""
        with self.queue_lock:
            self.is_downloading = True
            self.current_download = url[:50] + "..."
        
        queue_remaining = self.download_queue.qsize()
        
        self.logger.info(f"Starting download: {url}")
        self.show_notification(
            "Download Started",
            f"Downloading...\nRemaining in queue: {queue_remaining}"
        )
        
        ydl_opts = {
            'format': self.config['video_format'],
            'outtmpl': os.path.join(
                self.config['download_path'],
                self.config['filename_template']
            ),
            'merge_output_format': self.config['merge_output_format'],
            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': False,
            'updatetime': False,
            'retries': 10,
            'fragment_retries': 10,
            'continuedl': True,
            'nocheckcertificate': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }
        
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'Unknown')
                
            self.logger.info(f"Successfully downloaded: {video_title}")
            
            queue_remaining = self.download_queue.qsize()
            if queue_remaining > 0:
                self.show_notification(
                    "Download Complete! ✓",
                    f"{video_title[:50]}\nNext in queue: {queue_remaining} video(s)",
                    timeout=10
                )
            else:
                self.show_notification(
                    "Download Complete! ✓",
                    f"{video_title[:50]}\nQueue is empty",
                    timeout=10
                )
            
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Download failed: {error_msg}")
            self.show_notification(
                "Download Failed ✗",
                f"Error: {error_msg[:100]}",
                timeout=10
            )
        finally:
            with self.queue_lock:
                self.is_downloading = False
                self.current_download = None
    
    def monitor_clipboard(self):
        """Monitor clipboard for YouTube URLs."""
        self.logger.info("Starting clipboard monitor...")
        self.logger.info(f"Download path: {self.config['download_path']}")
        self.logger.info(f"Video format: {self.config['video_format']}")
        self.logger.info("Waiting for YouTube URLs in clipboard...")
        
        self.show_notification(
            "Auto-Downloader Started",
            "Monitoring clipboard for YouTube links"
        )
        
        try:
            while True:
                try:
                    clipboard_content = pyperclip.paste()
                    
                    # Check if clipboard changed and contains YouTube URL
                    if clipboard_content != self.last_clipboard:
                        self.last_clipboard = clipboard_content
                        
                        youtube_url = self.is_youtube_url(clipboard_content)
                        if youtube_url:
                            self.logger.info(f"YouTube URL detected: {youtube_url}")
                            self.add_to_queue(youtube_url)
                    
                    time.sleep(self.config['check_interval'])
                    
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    self.logger.error(f"Error in monitor loop: {e}")
                    time.sleep(self.config['check_interval'])
        
        except KeyboardInterrupt:
            self.logger.info("Stopping clipboard monitor...")
            self.show_notification(
                "Auto-Downloader Stopped",
                "Clipboard monitoring stopped"
            )
    
    def run(self):
        """Start the auto-downloader."""
        print("\n" + "="*60)
        print("YouTube Auto-Downloader with Queue System")
        print("="*60)
        print(f"Download folder: {self.config['download_path']}")
        print(f"Video format: {self.config['video_format']}")
        print(f"Notifications: {'Enabled' if self.config['enable_notifications'] else 'Disabled'}")
        print(f"Queue mode: Sequential (one at a time)")
        print("\nCopy any YouTube URL to add it to the download queue!")
        print("Videos will download one after another.")
        print("Press Ctrl+C to stop\n")
        print("="*60 + "\n")
        
        self.monitor_clipboard()


def main():
    """Main entry point."""
    # Check if config file path provided
    config_path = "config.json"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    
    downloader = YouTubeAutoDownloader(config_path)
    downloader.run()


if __name__ == "__main__":
    main()
