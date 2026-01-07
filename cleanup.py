import os
from datetime import datetime, timedelta

def cleanup_old_files(directory, days=30):
    """刪除超過指定天數的檔案"""
    cutoff = datetime.now() - timedelta(days=days)
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            if file_time < cutoff:
                os.remove(filepath)
                print(f"已刪除: {filepath}")

if __name__ == "__main__":
    cleanup_old_files('reports', days=30)
    cleanup_old_files('charts', days=7)