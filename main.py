#!/usr/bin/env python3
"""
Kubernetes Semantic Identity Resolver
Main entry point for the microservice
"""
import logging
import sys
import threading
import time
import os 
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class PodLeaseTable:
    """Manages the mapping of pod IPs to their Kubernetes identities"""
    
    def __init__(self):
        self.pod_ip_map = {}
        self._lock = threading.RLock()
        logger.info("Initialized Pod Lease Table")

    def update_pod(self, pod_ip: str, pod_metadata: dict):
        """Update or add a pod entry in the lease table"""
        with self._lock:
            self.pod_ip_map[pod_ip] = pod_metadata
        logger.debug(f"Updated pod entry for IP {pod_ip}")

    def remove_pod(self, pod_ip: str):
        """Remove a pod entry from the lease table"""
        with self._lock:
            if pod_ip in self.pod_ip_map:
                del self.pod_ip_map[pod_ip]
        logger.debug(f"Removed pod entry for IP {pod_ip}")

    def get_pod_identity(self, pod_ip: str) -> dict:
        """Retrieve pod identity by IP address"""
        with self._lock:
            return self.pod_ip_map.get(pod_ip)

    def get_all_pods(self) -> dict:
        """Get all pod mappings"""
        with self._lock:
            return self.pod_ip_map.copy()


class K8sIdentityResolver:
    """Main resolver class for Kubernetes semantic identities"""
    
    def __init__(self, kubeconfig_path: str = None):
        self.kubeconfig_path = kubeconfig_path
        self.lease_table = PodLeaseTable()
        self.v1_api = None
        self._watch_thread = None
        self._shutdown_event = None
        
    def connect_to_k8s(self):
        """Establish connection to Kubernetes API"""
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
                logger.info(f"Loaded kubeconfig from {self.kubeconfig_path}")
            else:
                # Try in-cluster config first, fall back to default kubeconfig
                try:
                    config.load_incluster_config()
                    logger.info("Loaded in-cluster Kubernetes configuration")
                except config.ConfigException:
                    config.load_kube_config()
                    logger.info("Loaded default kubeconfig")
            
            self.v1_api = client.CoreV1Api()
            logger.info("Successfully connected to Kubernetes API")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Kubernetes API: {e}")
            return False
    
    def build_pod_lease_table(self):
        """Build the initial pod lease table from current cluster state"""
        try:
            logger.info("Building pod lease table...")
            pods = self.v1_api.list_pod_for_all_namespaces(watch=False)
            
            for pod in pods.items:
                if pod.status.pod_ip:
                    pod_metadata = self._extract_pod_metadata(pod)
                    self.lease_table.update_pod(pod.status.pod_ip, pod_metadata)
            
            logger.info(f"Built pod lease table with {len(self.lease_table.get_all_pods())} entries")
            
        except ApiException as e:
            logger.error(f"Kubernetes API error while building lease table: {e}")
        except Exception as e:
            logger.error(f"Error building pod lease table: {e}")
    
    def _extract_pod_metadata(self, pod) -> dict:
        """Extract relevant metadata from a pod object"""
        return {
            'name': pod.metadata.name,
            'namespace': pod.metadata.namespace,
            'labels': pod.metadata.labels or {},
            'uid': pod.metadata.uid,
            'node_name': pod.spec.node_name,
            'service_account': pod.spec.service_account_name,
            'phase': pod.status.phase
        }
    
    def _watch_pods(self, shutdown_event):
        """Watch for pod changes and update lease table in real-time"""
        from kubernetes import watch
        
        logger.info("Starting pod watcher...")
        w = watch.Watch()
        
        while not shutdown_event.is_set():
            try:
                # Watch pod events across all namespaces
                for event in w.stream(
                    self.v1_api.list_pod_for_all_namespaces,
                    timeout_seconds=60
                ):
                    if shutdown_event.is_set():
                        break
                    
                    event_type = event['type']
                    pod = event['object']
                    pod_ip = pod.status.pod_ip
                    
                    if event_type == 'ADDED':
                        if pod_ip:
                            pod_metadata = self._extract_pod_metadata(pod)
                            self.lease_table.update_pod(pod_ip, pod_metadata)
                            logger.info(f"Added pod {pod.metadata.namespace}/{pod.metadata.name} with IP {pod_ip}")
                    
                    elif event_type == 'MODIFIED':
                        if pod_ip:
                            pod_metadata = self._extract_pod_metadata(pod)
                            self.lease_table.update_pod(pod_ip, pod_metadata)
                            logger.debug(f"Updated pod {pod.metadata.namespace}/{pod.metadata.name} with IP {pod_ip}")
                    
                    elif event_type == 'DELETED':
                        if pod_ip:
                            self.lease_table.remove_pod(pod_ip)
                            logger.info(f"Removed pod {pod.metadata.namespace}/{pod.metadata.name} with IP {pod_ip}")
                
            except ApiException as e:
                if shutdown_event.is_set():
                    break
                logger.error(f"Kubernetes API error in watch: {e}")
                logger.info("Reconnecting watch in 5 seconds...")
                shutdown_event.wait(5)
                
            except Exception as e:
                if shutdown_event.is_set():
                    break
                logger.error(f"Error in pod watch: {e}")
                logger.info("Reconnecting watch in 5 seconds...")
                shutdown_event.wait(5)
        
        logger.info("Pod watcher stopped")
    
    def start_watching(self):
        """Start the pod watching thread"""
        if self._watch_thread is not None:
            logger.warning("Pod watcher already running")
            return
        
        self._shutdown_event = threading.Event()
        self._watch_thread = threading.Thread(
            target=self._watch_pods,
            args=(self._shutdown_event,),
            daemon=True
        )
        self._watch_thread.start()
        logger.info("Pod watcher thread started")
    
    def stop_watching(self):
        """Stop the pod watching thread"""
        if self._shutdown_event:
            self._shutdown_event.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=5)
            self._watch_thread = None
        logger.info("Pod watcher stopped")
    
    def correlate_flow(self, source_ip: str, dest_ip: str) -> dict:
        """Correlate a network flow with pod identities"""
        result = {
            'source_ip': source_ip,
            'destination_ip': dest_ip,
            'source_identity': self.lease_table.get_pod_identity(source_ip),
            'destination_identity': self.lease_table.get_pod_identity(dest_ip)
        }
        return result
    
    def run(self):
        """Main run loop"""
        logger.info("Starting Kubernetes Semantic Identity Resolver...")
        
        if not self.connect_to_k8s():
            logger.error("Failed to connect to Kubernetes. Exiting.")
            sys.exit(1)
        
        self.build_pod_lease_table()
        self.start_watching()
        
        logger.info("Identity resolver is running. Press Ctrl+C to stop.")
        
        try:
            # Keep the service running
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.stop_watching()


def main():
    """Entry point"""
    import argparse
    import uvicorn
    from api import create_app
    
    parser = argparse.ArgumentParser(description="Kubernetes Semantic Identity Resolver")
    parser.add_argument(
        '--host',
        default=os.getenv('HOST', '0.0.0.0'),
        help='API server host (default: 0.0.0.0, ignores env)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=int(os.getenv('PORT', '8000')),
        help='API server port (default: 8000, ignores env)'
    )
    parser.add_argument(
        '--kubeconfig',
        help='Path to kubeconfig file'
    )
    
    args = parser.parse_args()
    
    # Check for custom kubeconfig path
    if not args.kubeconfig:
        config_dir = Path(__file__).parent / "config"
        kubeconfig_path = config_dir / "kubeconfig"
        if kubeconfig_path.exists():
            args.kubeconfig = str(kubeconfig_path)
    
    # Create resolver instance
    resolver = K8sIdentityResolver(kubeconfig_path=args.kubeconfig)
    
    # Connect and build initial state
    if not resolver.connect_to_k8s():
        logger.warning("Failed to connect to Kubernetes. Running with empty pod lease table.")
    else:
        resolver.build_pod_lease_table()
        resolver.start_watching()
    
    # Create and run API server
    app = create_app(resolver)
    
    try:
        logger.info(f"Starting API server on {args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        resolver.stop_watching()


if __name__ == "__main__":
    main()

