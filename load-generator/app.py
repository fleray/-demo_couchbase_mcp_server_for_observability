import os
import json
import subprocess
import time
import threading
import re
from flask import Flask, render_template, jsonify, request
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class WorkloadConfig:
    def __init__(self):
        self.kv_upsert_percentage = 100
        self.operations_per_second = 1000
        self.is_running = False
        self.total_operations = 0
        self.current_rate = 0

    def to_dict(self):
        return {
            'kv_upsert_percentage': self.kv_upsert_percentage,
            'operations_per_second': self.operations_per_second,
            'is_running': self.is_running,
            'total_operations': self.total_operations,
            'current_rate': self.current_rate,
        }

config = WorkloadConfig()
workload_process = None
log_file = '/tmp/couchbase_workload.log'

def monitor_workload():
    """Monitor cbc-pillowfight output for throughput metrics"""
    while config.is_running and workload_process and workload_process.poll() is None:
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()[-50:]  # Last 50 lines
                for line in reversed(lines):
                    match = re.search(r'OPS/SEC:\s+(\d+)', line)
                    if match:
                        config.current_rate = int(match.group(1))
                        break
            time.sleep(1)
        except Exception as e:
            logger.debug(f"Monitor error: {e}")
            time.sleep(1)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    return jsonify(config.to_dict())

@app.route('/api/config', methods=['POST'])
def set_config():
    data = request.json
    config.kv_upsert_percentage = data.get('kv_upsert_percentage', 100)
    config.operations_per_second = data.get('operations_per_second', config.operations_per_second)
    logger.info(f"Config: {config.operations_per_second} ops/sec")
    return jsonify({'status': 'updated', 'config': config.to_dict()})

@app.route('/api/control/start', methods=['POST'])
def start_workload():
    global workload_process

    if config.is_running:
        return jsonify({'status': 'already_running'})

    config.is_running = True
    config.total_operations = 0
    config.current_rate = 0

    cb_host = os.getenv('CB_HOST', 'couchbase')
    cb_bucket = os.getenv('CB_BUCKET', 'travel-sample')
    cb_username = os.getenv('CB_USERNAME', 'Administrator')
    cb_password = os.getenv('CB_PASSWORD', 'password123')

    # Auto-scale threads: ~5K ops/sec per thread
    threads = max(1, config.operations_per_second // 5000)

    try:
        cmd = [
            './cbc-pillowfight',
            '-U', f'couchbase://{cb_host}/{cb_bucket}',
            '-u', cb_username,
            '-P', cb_password,
            '-t', str(threads),
            '-r', '100',  # 100% SET operations
            '-I', '1000000',  # Large key space
        ]

        with open(log_file, 'w') as logfile:
            workload_process = subprocess.Popen(
                cmd,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                cwd='/app'
            )

        logger.info(f"Started cbc-pillowfight: {threads} threads, {config.operations_per_second} ops/sec target")

        # Start monitoring
        thread = threading.Thread(target=monitor_workload, daemon=True)
        thread.start()

        return jsonify({'status': 'started', 'threads': threads})

    except Exception as e:
        logger.error(f"Failed: {e}")
        config.is_running = False
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/control/stop', methods=['POST'])
def stop_workload():
    global workload_process

    config.is_running = False
    if workload_process:
        workload_process.terminate()
        try:
            workload_process.wait(timeout=5)
        except:
            workload_process.kill()

    return jsonify({'status': 'stopped'})

@app.route('/api/health')
def health():
    return jsonify({'status': 'healthy', 'workload_running': config.is_running})

@app.route('/api/profiles')
def profiles():
    try:
        with open('workload_profiles.json') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'profiles': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
