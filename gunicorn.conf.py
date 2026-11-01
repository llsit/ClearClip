"""One process is required: job state and download queue are in memory."""
import os

bind = '0.0.0.0:' + os.environ.get('PORT', '10000')
workers = 1
worker_class = 'gthread'
threads = 8
timeout = 120
graceful_timeout = 30
accesslog = '-'
errorlog = '-'
preload_app = False
