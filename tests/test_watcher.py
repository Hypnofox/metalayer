"""
Tests for Kubernetes pod watcher functionality
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from main import K8sIdentityResolver, PodLeaseTable
import threading
import time


@pytest.fixture
def resolver():
    """Create a resolver instance for testing"""
    resolver = K8sIdentityResolver()
    resolver.v1_api = Mock()
    return resolver


class TestPodLeaseTable:
    """Tests for PodLeaseTable class"""
    
    def test_update_pod(self):
        """Test updating pod in lease table"""
        table = PodLeaseTable()
        
        pod_metadata = {
            'name': 'test-pod',
            'namespace': 'default',
            'labels': {'app': 'test'},
            'uid': 'abc-123',
            'node_name': 'node-1',
            'service_account': 'default',
            'phase': 'Running'
        }
        
        table.update_pod("10.244.1.5", pod_metadata)
        
        assert table.get_pod_identity("10.244.1.5") == pod_metadata
        assert len(table.get_all_pods()) == 1
    
    def test_remove_pod(self):
        """Test removing pod from lease table"""
        table = PodLeaseTable()
        
        pod_metadata = {
            'name': 'test-pod',
            'namespace': 'default',
            'labels': {},
            'uid': 'abc-123',
            'node_name': 'node-1',
            'service_account': 'default',
            'phase': 'Running'
        }
        
        table.update_pod("10.244.1.5", pod_metadata)
        assert len(table.get_all_pods()) == 1
        
        table.remove_pod("10.244.1.5")
        assert len(table.get_all_pods()) == 0
        assert table.get_pod_identity("10.244.1.5") is None
    
    def test_get_nonexistent_pod(self):
        """Test getting pod that doesn't exist"""
        table = PodLeaseTable()
        
        assert table.get_pod_identity("192.168.1.1") is None


class TestPodMetadataExtraction:
    """Tests for pod metadata extraction"""
    
    def test_extract_pod_metadata(self, resolver):
        """Test extracting metadata from pod object"""
        # Create mock pod object
        mock_pod = Mock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.metadata.namespace = "default"
        mock_pod.metadata.labels = {"app": "test", "version": "v1"}
        mock_pod.metadata.uid = "abc-123"
        mock_pod.spec.node_name = "node-1"
        mock_pod.spec.service_account_name = "default"
        mock_pod.status.phase = "Running"
        
        metadata = resolver._extract_pod_metadata(mock_pod)
        
        assert metadata['name'] == "test-pod"
        assert metadata['namespace'] == "default"
        assert metadata['labels'] == {"app": "test", "version": "v1"}
        assert metadata['uid'] == "abc-123"
        assert metadata['node_name'] == "node-1"
        assert metadata['service_account'] == "default"
        assert metadata['phase'] == "Running"
    
    def test_extract_pod_metadata_no_labels(self, resolver):
        """Test extracting metadata when pod has no labels"""
        mock_pod = Mock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.metadata.namespace = "default"
        mock_pod.metadata.labels = None
        mock_pod.metadata.uid = "abc-123"
        mock_pod.spec.node_name = "node-1"
        mock_pod.spec.service_account_name = "default"
        mock_pod.status.phase = "Running"
        
        metadata = resolver._extract_pod_metadata(mock_pod)
        
        assert metadata['labels'] == {}


class TestWatcherLifecycle:
    """Tests for watcher start/stop lifecycle"""
    
    def test_start_watching(self, resolver):
        """Test starting the pod watcher"""
        resolver.start_watching()
        
        assert resolver._watch_thread is not None
        assert resolver._shutdown_event is not None
        assert resolver._watch_thread.is_alive()
        
        # Cleanup
        resolver.stop_watching()
    
    def test_stop_watching(self, resolver):
        """Test stopping the pod watcher"""
        resolver.start_watching()
        assert resolver._watch_thread.is_alive()
        
        resolver.stop_watching()
        
        # Give thread time to stop
        time.sleep(0.5)
        
        assert not resolver._watch_thread.is_alive() or resolver._watch_thread is None
    
    def test_start_watching_twice(self, resolver):
        """Test that starting watcher twice doesn't create duplicate threads"""
        resolver.start_watching()
        first_thread = resolver._watch_thread
        
        resolver.start_watching()
        second_thread = resolver._watch_thread
        
        assert first_thread is second_thread
        
        # Cleanup
        resolver.stop_watching()


class TestFlowCorrelation:
    """Tests for flow correlation logic"""
    
    def test_correlate_flow_both_found(self, resolver):
        """Test correlating flow when both pods exist"""
        resolver.lease_table.update_pod("10.244.1.5", {
            'name': 'pod-1',
            'namespace': 'default',
            'labels': {},
            'uid': 'abc',
            'node_name': 'node-1',
            'service_account': 'default',
            'phase': 'Running'
        })
        
        resolver.lease_table.update_pod("10.244.2.10", {
            'name': 'pod-2',
            'namespace': 'default',
            'labels': {},
            'uid': 'def',
            'node_name': 'node-2',
            'service_account': 'default',
            'phase': 'Running'
        })
        
        result = resolver.correlate_flow("10.244.1.5", "10.244.2.10")
        
        assert result['source_ip'] == "10.244.1.5"
        assert result['destination_ip'] == "10.244.2.10"
        assert result['source_identity']['name'] == 'pod-1'
        assert result['destination_identity']['name'] == 'pod-2'
    
    def test_correlate_flow_not_found(self, resolver):
        """Test correlating flow when pods don't exist"""
        result = resolver.correlate_flow("192.168.1.1", "192.168.1.2")
        
        assert result['source_identity'] is None
        assert result['destination_identity'] is None
