"""
Celery Client Wrapper
======================

Direct Celery connection via Redis.
"""

from celery import Celery
from typing import Dict, Optional


class CeleryClient:
    """Direct Celery connection via Redis"""

    def __init__(self, redis_url: str):
        """Initialize Celery client

        Args:
            redis_url: Redis connection URL (e.g., redis://redis:6379/0)
        """
        self.redis_url = redis_url
        self.celery = Celery(broker=redis_url, backend=redis_url)

    def trigger_task(self, task_name: str, **kwargs) -> str:
        """Trigger a Celery task

        Args:
            task_name: Full task name (e.g., 'masu.celery.tasks.check_report_updates')
            **kwargs: Task arguments

        Returns:
            Task ID
        """
        result = self.celery.send_task(task_name, kwargs=kwargs)
        return result.id

    def get_task_status(self, task_id: str) -> Dict:
        """Get task status

        Args:
            task_id: Task ID from trigger_task()

        Returns:
            Status dict with id, state, ready, successful
        """
        result = self.celery.AsyncResult(task_id)
        return {
            'id': task_id,
            'state': result.state,
            'ready': result.ready(),
            'successful': result.successful() if result.ready() else None,
            'result': result.result if result.ready() else None
        }

    def wait_for_task(self, task_id: str, timeout: int = 300) -> Dict:
        """Wait for task to complete

        Args:
            task_id: Task ID from trigger_task()
            timeout: Max wait time in seconds

        Returns:
            Task result dict
        """
        result = self.celery.AsyncResult(task_id)
        try:
            result.get(timeout=timeout)
            return self.get_task_status(task_id)
        except Exception as e:
            return {
                'id': task_id,
                'state': 'FAILED',
                'ready': True,
                'successful': False,
                'error': str(e)
            }

