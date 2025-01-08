from pathlib import Path
from datetime import datetime
from flask import Blueprint, Response, render_template, current_app
import pytz
import time

logs = Blueprint('logs', __name__)

def tail_file(filename):
    """Generator function to tail a file"""
    with open(filename, 'r') as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            yield line

@logs.record
def record_params(setup_state):
    """Store config in blueprint when registering"""
    config = setup_state.options.get('config')
    logs.config = config

@logs.route('/logs', defaults={'date': None})
@logs.route('/logs/<date>')
def get_logs(date):
    """API endpoint to stream logs"""
    log_path = Path('logs/trading_system.log')
    
    if date:
        try:
            target_date = datetime.strptime(date, '%Y%m%d')
            filtered_logs = []
            
            with open(log_path, 'r') as f:
                for line in f:
                    try:
                        log_date = datetime.strptime(line.split()[0], '%Y-%m-%d')
                        if log_date.date() == target_date.date():
                            filtered_logs.append(line)
                    except (ValueError, IndexError):
                        continue
            
            return Response(''.join(filtered_logs), mimetype='text/plain')
        
        except ValueError:
            return Response('Invalid date format. Use YYYYMMDD', status=400)
    
    return render_template('logs.html')

@logs.route('/logs/stream')
def stream_logs():
    """Endpoint for SSE streaming"""
    log_path = Path('logs/trading_system.log')
    today = datetime.now(logs.config.TIMEZONE).date()
    
    def generate():
        with open(log_path, 'r') as f:
            for line in f:
                try:
                    log_date = datetime.strptime(line.split()[0], '%Y-%m-%d').date()
                    if log_date == today:
                        yield f"data: {line}\n\n"
                except (ValueError, IndexError):
                    continue
        
        for line in tail_file(log_path):
            try:
                log_date = datetime.strptime(line.split()[0], '%Y-%m-%d').date()
                if log_date == today:
                    yield f"data: {line}\n\n"
            except (ValueError, IndexError):
                continue
    
    return Response(generate(), mimetype='text/event-stream')