from django.apps import AppConfig
import sys
import threading

def run_sqs_poller():
    """Run the management command in the background."""
    from django.core.management import call_command
    try:
        call_command('poll_sqs')
    except Exception as e:
        import logging
        logging.getLogger('django').error(f"Background SQS Poller Crash: {e}")

class StocksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stocks'
    verbose_name = 'Stock Market Analysis'
    
    # Track if started to prevent duplicates during testing or reloads
    poller_started = False

    def ready(self):
        # Only start the background worker if this is the actual active web server
        # (Skip during 'migrate', 'collectstatic', 'check', etc)
        is_server = 'runserver' in sys.argv or 'gunicorn' in sys.argv[0]
        
        if is_server and not StocksConfig.poller_started:
            StocksConfig.poller_started = True
            
            # Run the infinite poll loop as a daemon thread.
            # This cleanly attaches it to the main web process and avoids needing custom Procfiles!
            thread = threading.Thread(target=run_sqs_poller, daemon=True)
            thread.start()
