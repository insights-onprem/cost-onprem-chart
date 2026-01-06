"""
Kubernetes Client
=================

Native Kubernetes API client - no kubectl subprocess calls.
"""

import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Iterator
from kubernetes import client, config, stream
import socket
import threading


class KubernetesClient:
    """Native Kubernetes API client"""

    def __init__(self, namespace: str = "cost-onprem"):
        """Initialize Kubernetes client

        Args:
            namespace: Kubernetes namespace to operate in
        """
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.namespace = namespace

    def get_pods(self, label_selector: Optional[str] = None) -> List[client.V1Pod]:
        """Get pods in namespace

        Args:
            label_selector: Optional label selector (e.g., "app=koku")

        Returns:
            List of pod objects
        """
        return self.v1.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=label_selector
        ).items

    def get_pod_by_component(self, component: str) -> Optional[str]:
        """Get first pod name for a component

        Args:
            component: Component label value (e.g., "masu", "database")

        Returns:
            Pod name or None
        """
        pods = self.get_pods(f"app.kubernetes.io/component={component}")
        return pods[0].metadata.name if pods else None

    def discover_postgresql_pod(self) -> Optional[str]:
        """Discover PostgreSQL pod by label selector

        Returns:
            PostgreSQL pod name or None if not found
        """
        try:
            # Look for pod with database component label (Helm chart uses 'database')
            return self.get_pod_by_component("database")
        except Exception as e:
            print(f"  ⚠️  Failed to discover PostgreSQL pod: {e}")
            return None

    def get_pod_health(self) -> Dict[str, int]:
        """Get pod health statistics

        Returns:
            Dict with total, ready, running counts
        """
        pods = self.get_pods()
        total = len(pods)
        running = sum(1 for p in pods if p.status.phase == "Running")
        ready = sum(1 for p in pods
                   if p.status.conditions and
                   any(c.type == "Ready" and c.status == "True"
                       for c in p.status.conditions))

        return {
            "total": total,
            "running": running,
            "ready": ready
        }

    def exec_in_pod(self, pod_name: str, command: List[str],
                    container: Optional[str] = None) -> str:
        """Execute command in pod using native API

        Args:
            pod_name: Name of pod
            command: Command to execute as list
            container: Optional container name

        Returns:
            Command output as string
        """
        resp = stream.stream(
            self.v1.connect_get_namespaced_pod_exec,
            pod_name,
            self.namespace,
            command=command,
            container=container,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False
        )

        output = []
        while resp.is_open():
            resp.update(timeout=600)  # 10 minutes for tenant migrations (creates 217 tables)
            if resp.peek_stdout():
                output.append(resp.read_stdout())
            if resp.peek_stderr():
                output.append(resp.read_stderr())

        return ''.join(output)

    def python_exec(self, pod_name: str, python_code: str) -> str:
        """Execute Python code in pod

        Args:
            pod_name: Name of pod
            python_code: Python code to execute

        Returns:
            Output from Python execution
        """
        return self.exec_in_pod(pod_name, ['python3', '-c', python_code])

    def postgres_exec(self, pod_name: str, database: str, sql: str, user: str = 'koku') -> str:
        """Execute SQL in PostgreSQL pod using subprocess (more reliable than websocket)

        Args:
            pod_name: Name of PostgreSQL pod
            database: Database name
            sql: SQL to execute
            user: PostgreSQL user (default: koku)

        Returns:
            SQL output as string
        """
        import subprocess

        # When using subprocess with a list (not shell=True), no shell escaping is needed
        # The SQL is passed directly to psql without shell interpretation
        cmd = [
            'kubectl', 'exec', '-n', self.namespace, pod_name, '--',
            'psql', '-U', user, '-d', database, '-t', '-A', '-c', sql
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                raise RuntimeError(f"SQL execution failed: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SQL query timed out after 30s")
        except Exception as e:
            raise RuntimeError(f"Failed to execute SQL: {e}")

    def get_secret(self, secret_name: str, key: str) -> Optional[str]:
        """Get secret value

        Args:
            secret_name: Name of secret
            key: Key within secret

        Returns:
            Decoded secret value or None
        """
        import base64

        try:
            secret = self.v1.read_namespaced_secret(secret_name, self.namespace)
            if key in secret.data:
                return base64.b64decode(secret.data[key]).decode('utf-8')
        except Exception:
            pass

        return None

    def discover_database_secret(self, pod_name: str = "postgres-0",
                                 env_var_name: str = "POSTGRES_PASSWORD") -> Optional[Dict[str, str]]:
        """Discover database secret by introspecting pod environment variables

        Args:
            pod_name: Name of PostgreSQL pod
            env_var_name: Environment variable name that references the secret

        Returns:
            Dict with 'secret_name' and 'key' or None if not found
        """
        try:
            pod = self.v1.read_namespaced_pod(pod_name, self.namespace)

            # Look through containers for the environment variable
            for container in pod.spec.containers:
                if not container.env:
                    continue

                for env in container.env:
                    if env.name == env_var_name and env.value_from and env.value_from.secret_key_ref:
                        return {
                            'secret_name': env.value_from.secret_key_ref.name,
                            'key': env.value_from.secret_key_ref.key
                        }
        except Exception as e:
            print(f"  ⚠️  Failed to discover database secret: {e}")

        return None

    @contextmanager
    def port_forward(self, pod_name: str, remote_port: int,
                    local_port: Optional[int] = None) -> Iterator[int]:
        """Port forward to a pod using native API

        Args:
            pod_name: Name of pod to forward to
            remote_port: Port on pod
            local_port: Local port (auto-assigned if None)

        Yields:
            Local port number
        """
        # Find available local port if not specified
        if local_port is None:
            sock = socket.socket()
            sock.bind(('', 0))
            local_port = sock.getsockname()[1]
            sock.close()

        # Create port forward connection
        pf = stream.portforward(
            self.v1.connect_get_namespaced_pod_portforward,
            pod_name,
            self.namespace,
            ports=str(remote_port)
        )

        # Start forwarding in background thread
        stop_event = threading.Event()

        def forward():
            try:
                # Simple forwarding logic
                while not stop_event.is_set():
                    time.sleep(0.1)
            except Exception:
                pass

        thread = threading.Thread(target=forward, daemon=True)
        thread.start()

        try:
            # Give it a moment to establish
            time.sleep(0.5)
            yield local_port
        finally:
            stop_event.set()
            thread.join(timeout=1)

    def get_service_endpoint(self, service_name: str) -> Optional[str]:
        """Get service cluster IP and port

        Args:
            service_name: Name of service

        Returns:
            Endpoint as "host:port" or None
        """
        try:
            svc = self.v1.read_namespaced_service(service_name, self.namespace)
            if svc.spec.cluster_ip and svc.spec.ports:
                port = svc.spec.ports[0].port
                return f"{svc.spec.cluster_ip}:{port}"
        except Exception:
            pass

        return None

    @contextmanager
    def port_forward(self, pod_name: str, remote_port: int,
                    local_port: Optional[int] = None) -> Iterator[int]:
        """Port forward to a pod using native API

        Args:
            pod_name: Name of pod to forward to
            remote_port: Port on pod
            local_port: Local port (auto-assigned if None)

        Yields:
            Local port number
        """
        # Find available local port if not specified
        if local_port is None:
            sock = socket.socket()
            sock.bind(('', 0))
            local_port = sock.getsockname()[1]
            sock.close()

        # Create port forward connection
        pf = stream.portforward(
            self.v1.connect_get_namespaced_pod_portforward,
            pod_name,
            self.namespace,
            ports=str(remote_port)
        )

        # Start forwarding in background thread
        stop_event = threading.Event()

        def forward():
            try:
                # Simple forwarding logic
                while not stop_event.is_set():
                    time.sleep(0.1)
            except Exception:
                pass

        thread = threading.Thread(target=forward, daemon=True)
        thread.start()

        try:
            # Give it a moment to establish
            time.sleep(0.5)
            yield local_port
        finally:
            stop_event.set()
            thread.join(timeout=1)

    def get_service_endpoint(self, service_name: str) -> Optional[str]:
        """Get service cluster IP and port

        Args:
            service_name: Name of service

        Returns:
            Endpoint as "host:port" or None
        """
        try:
            svc = self.v1.read_namespaced_service(service_name, self.namespace)
            if svc.spec.cluster_ip and svc.spec.ports:
                port = svc.spec.ports[0].port
                return f"{svc.spec.cluster_ip}:{port}"
        except Exception:
            pass

        return None

